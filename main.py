#!/usr/bin/env python3
"""CleverClip —— AI 驱动的视频自动化剪辑工具"""

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from src.config import Config
from src.frame_extractor import FrameExtractor
from src.llm_client import LLMClient
from src.timeline_builder import TimelineBuilder
from src.video_editor import VideoEditor
from src.utils import check_ffmpeg

console = Console()


def discover_source_videos(cfg: Config) -> list[Path]:
    """扫描 source_clips 目录，返回所有 .mp4 文件"""
    videos = cfg.discover_source_videos()
    if not videos:
        console.print(
            f"[red]错误: 在 {cfg.source_clips_dir} 中没有找到 .mp4 文件[/red]\n"
            f"[dim]请将视频文件放入 source_clips/ 目录[/dim]"
        )
        sys.exit(1)
    return videos


def build_source_map(videos: list[Path]) -> dict[str, Path]:
    """构建 {视频名(stem): 路径} 映射表"""
    return {v.stem: v for v in videos}


async def run_analyze(cfg: Config) -> None:
    """执行分析流程"""
    videos = discover_source_videos(cfg)

    console.print(f"[cyan]发现 {len(videos)} 个视频源:[/cyan]")
    for v in videos:
        console.print(f"  • {v.name}")

    # Step 1: 抽帧（所有视频）
    console.print("\n[bold blue]━━━ Step 1: 抽帧 ━━━[/bold blue]")
    extractor = FrameExtractor(cfg)
    all_frames = []
    for video_path in videos:
        session_dir = cfg.temp_dir / video_path.stem
        frames = extractor.extract(video_path, session_dir)
        all_frames.extend(frames)

    console.print(f"[green]✓ 共抽取 {len(all_frames)} 帧（来自 {len(videos)} 个视频）[/green]")

    # Step 2: LLM 视觉识别（并发）
    console.print("\n[bold blue]━━━ Step 2: AI 内容识别 ━━━[/bold blue]")
    client = LLMClient(cfg)
    analyzed = await client.analyze_frames(all_frames)

    # Step 3: 构建时间轴
    console.print("\n[bold blue]━━━ Step 3: 构建时间轴 ━━━[/bold blue]")
    builder = TimelineBuilder()
    timeline = builder.build_from_frames(analyzed)
    builder.display()
    builder.save(cfg.temp_dir / "timeline.json")


async def run_edit(cfg: Config, timeline: str | None, requirement: str) -> Path:
    """执行剪辑流程"""
    if timeline:
        console.print(f"[dim]使用已有时间轴: {timeline}[/dim]")
        import json
        with open(timeline, "r", encoding="utf-8") as f:
            timeline_data = json.load(f)
    else:
        videos = discover_source_videos(cfg)

        console.print(f"[cyan]发现 {len(videos)} 个视频源:[/cyan]")
        for v in videos:
            console.print(f"  • {v.name}")

        # 1a. 抽帧（所有视频）
        console.print("\n[bold blue]━━━ Step 1: 抽帧 ━━━[/bold blue]")
        extractor = FrameExtractor(cfg)
        all_frames = []
        for video_path in videos:
            session_dir = cfg.temp_dir / video_path.stem
            frames = extractor.extract(video_path, session_dir)
            all_frames.extend(frames)
        console.print(f"[green]✓ 共抽取 {len(all_frames)} 帧（来自 {len(videos)} 个视频）[/green]")

        # 1b. LLM 视觉识别（并发）
        console.print("\n[bold blue]━━━ Step 2: AI 内容识别 ━━━[/bold blue]")
        client = LLMClient(cfg)
        analyzed = await client.analyze_frames(all_frames)

        # 1c. 构建时间轴
        console.print("\n[bold blue]━━━ Step 3: 构建时间轴 ━━━[/bold blue]")
        builder = TimelineBuilder()
        timeline_data = builder.build_from_frames(analyzed)
        builder.display()
        builder.save(cfg.temp_dir / "timeline.json")

    # 始终需要 source_map 来定位视频文件
    videos = discover_source_videos(cfg)
    source_map = build_source_map(videos)

    # Step 2: LLM 规划剪辑方案
    console.print("\n[bold blue]━━━ Step 4: AI 剪辑规划 ━━━[/bold blue]")
    console.print(f"[cyan]剪辑需求:[/cyan] {requirement}")
    client = LLMClient(cfg)
    clips = client.plan_clips(timeline_data, requirement)

    if not clips:
        console.print("[red]未能生成有效的剪辑方案，请尝试调整需求描述。[/red]")
        sys.exit(1)

    # Step 3: 执行剪辑
    console.print("\n[bold blue]━━━ Step 5: 执行视频剪辑 ━━━[/bold blue]")
    editor = VideoEditor(cfg)
    result_path = editor.edit(source_map, clips)
    return result_path


@click.group()
def cli():
    """CleverClip —— AI 驱动的视频自动化剪辑工具"""
    pass


@cli.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
def analyze(config: str):
    """分析 source_clips/ 中所有视频内容，生成统一时间轴（不执行剪辑）"""
    console.print(Panel.fit("[bold cyan]CleverClip —— 视频内容分析[/bold cyan]"))

    if not check_ffmpeg():
        console.print("[red]错误: 需要安装 ffmpeg 和 ffprobe[/red]")
        sys.exit(1)

    cfg = Config(config)
    asyncio.run(run_analyze(cfg))

    console.print(f"\n[green]✓ 分析完成！时间轴已保存至 {cfg.temp_dir / 'timeline.json'}[/green]")


@cli.command()
@click.argument("requirement", type=str)
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.option("--output", "-o", default=None, help="输出文件名")
@click.option("--timeline", "-t", default=None, type=click.Path(exists=True), help="使用已有的时间轴 JSON 文件（跳过分析步骤）")
def edit(requirement: str, config: str, output: str, timeline: str):
    """根据自然语言需求剪辑视频（输入源为 source_clips/ 下的所有 .mp4）

    \b
    示例:
      python main.py edit "提取所有有人在说话的片段"
      python main.py edit "保留户外风景部分" -o result.mp4
      python main.py edit "精彩集锦" -t temp/timeline.json
    """
    console.print(Panel.fit("[bold cyan]CleverClip —— AI 视频自动剪辑[/bold cyan]"))

    if not check_ffmpeg():
        console.print("[red]错误: 需要安装 ffmpeg 和 ffprobe[/red]")
        sys.exit(1)

    cfg = Config(config)

    if output is None:
        output = "output.mp4"

    result_path = asyncio.run(run_edit(cfg, timeline, requirement))

    # 如果指定了输出文件名，重命名结果文件
    if output != "output.mp4":
        final_path = cfg.output_dir / output
        result_path.rename(final_path)
        result_path = final_path

    console.print(f"\n[bold green]🎉 剪辑完成！输出文件: {result_path}[/bold green]")


if __name__ == "__main__":
    cli()
