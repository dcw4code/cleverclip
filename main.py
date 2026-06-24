#!/usr/bin/env python3
"""CleverClip —— AI 驱动的视频自动化剪辑工具"""

import asyncio
import json
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


def find_timeline_files(timeline_dir: Path) -> list[Path]:
    """扫描目录下所有 timeline_*.json 文件，按文件名排序"""
    return sorted(timeline_dir.glob("timeline_*.json"))


def source_name_from_timeline(path: Path) -> str:
    """从 timeline_xxx.json 中提取源视频名"""
    # 文件名格式: timeline_{video_stem}.json
    return path.stem.replace("timeline_", "", 1)


async def run_analyze(cfg: Config) -> None:
    """执行分析流程 —— 每个视频单独分析，生成各自的 timeline JSON"""
    videos = discover_source_videos(cfg)

    console.print(f"[cyan]发现 {len(videos)} 个视频源:[/cyan]")
    for v in videos:
        console.print(f"  • {v.name}")

    extractor = FrameExtractor(cfg)
    client = LLMClient(cfg)

    for idx, video_path in enumerate(videos):
        source_name = video_path.stem
        console.print(f"\n[bold yellow]━━━ 视频 {idx + 1}/{len(videos)}: {video_path.name} ━━━[/bold yellow]")

        # Step 1: 抽帧
        console.print("[bold blue]Step 1: 抽帧[/bold blue]")
        session_dir = cfg.temp_dir / source_name
        frames = extractor.extract(video_path, session_dir)

        # Step 2: LLM 视觉识别（并发）
        console.print("[bold blue]Step 2: AI 内容识别[/bold blue]")
        analyzed = await client.analyze_frames(frames)

        # Step 3: 构建并保存时间轴
        console.print("[bold blue]Step 3: 构建时间轴[/bold blue]")
        builder = TimelineBuilder()
        builder.build_from_frames(analyzed)
        builder.display()
        timeline_path = cfg.temp_dir / f"timeline_{source_name}.json"
        builder.save(timeline_path)

    console.print(f"\n[green]✓ 全部 {len(videos)} 个视频分析完成！[/green]")
    console.print(f"[dim]时间轴文件: {cfg.temp_dir}/timeline_*.json[/dim]")


async def run_edit(cfg: Config, timeline_dir: str | None, requirement: str, output: str,
                   bgm: str | None, bgm_cfg: str | None) -> Path:
    """执行剪辑流程"""
    # Step 1: 确保有时间轴数据
    if timeline_dir:
        tl_dir = Path(timeline_dir)
    else:
        # 完整流程：逐个视频抽帧 → 识别 → 生成 timeline
        videos = discover_source_videos(cfg)

        console.print(f"[cyan]发现 {len(videos)} 个视频源:[/cyan]")
        for v in videos:
            console.print(f"  • {v.name}")

        extractor = FrameExtractor(cfg)
        client = LLMClient(cfg)

        for idx, video_path in enumerate(videos):
            source_name = video_path.stem
            console.print(f"\n[bold yellow]━━━ 视频 {idx + 1}/{len(videos)}: {video_path.name} ━━━[/bold yellow]")

            console.print("[bold blue]Step 1: 抽帧[/bold blue]")
            session_dir = cfg.temp_dir / source_name
            frames = extractor.extract(video_path, session_dir)

            console.print("[bold blue]Step 2: AI 内容识别[/bold blue]")
            analyzed = await client.analyze_frames(frames)

            console.print("[bold blue]Step 3: 构建时间轴[/bold blue]")
            builder = TimelineBuilder()
            builder.build_from_frames(analyzed)
            builder.save(cfg.temp_dir / f"timeline_{source_name}.json")

        tl_dir = cfg.temp_dir

    # Step 2: 逐个时间轴调用 LLM 规划剪辑片段
    timeline_files = find_timeline_files(tl_dir)
    if not timeline_files:
        console.print(f"[red]错误: 在 {tl_dir} 中没有找到 timeline_*.json 文件[/red]")
        sys.exit(1)

    console.print(f"\n[bold blue]━━━ AI 剪辑规划 ━━━[/bold blue]")
    console.print(f"[cyan]剪辑需求:[/cyan] {requirement}")
    console.print(f"[cyan]发现 {len(timeline_files)} 个时间轴文件[/cyan]")

    client = LLMClient(cfg)
    all_clips = []
    for tl_path in timeline_files:
        source_name = source_name_from_timeline(tl_path)
        console.print(f"\n[dim]分析时间轴: {tl_path.name}[/dim]")

        with open(tl_path, "r", encoding="utf-8") as f:
            timeline_data = json.load(f)

        clips = client.plan_clips(source_name, timeline_data, requirement)
        all_clips.extend(clips)

    console.print(f"\n[green]✓ 共规划了 {len(all_clips)} 个剪辑片段（来自 {len(timeline_files)} 个视频）[/green]")

    if not all_clips:
        console.print("[red]未能生成有效的剪辑方案，请尝试调整需求描述。[/red]")
        sys.exit(1)

    # Step 3: 执行剪辑
    console.print("\n[bold blue]━━━ 执行视频剪辑 ━━━[/bold blue]")
    videos = discover_source_videos(cfg)
    source_map = build_source_map(videos)
    editor = VideoEditor(cfg)

    # 加载 bgm 配置
    bgm_config = None
    if bgm_cfg:
        import yaml
        bgm_cfg_path = Path(bgm_cfg)
        if not bgm_cfg_path.exists():
            console.print(f"[red]BGM 配置文件不存在: {bgm_cfg_path}[/red]")
            sys.exit(1)
        with open(bgm_cfg_path, "r", encoding="utf-8") as f:
            bgm_config = yaml.safe_load(f) or {}
        console.print(f"[cyan]已加载 BGM 配置: {bgm_cfg}[/cyan]")

    result_path = editor.edit(source_map, all_clips, bgm_path=bgm, bgm_config=bgm_config)

    # 如果指定了输出文件名，重命名
    if output != "output.mp4":
        final_path = cfg.output_dir / output
        result_path.rename(final_path)
        result_path = final_path

    return result_path


@click.group()
def cli():
    """CleverClip —— AI 驱动的视频自动化剪辑工具"""
    pass


@cli.command()
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
def analyze(config: str):
    """分析 source_clips/ 中所有视频内容，每个视频生成独立的时间轴 JSON"""
    console.print(Panel.fit("[bold cyan]CleverClip —— 视频内容分析[/bold cyan]"))

    if not check_ffmpeg():
        console.print("[red]错误: 需要安装 ffmpeg 和 ffprobe[/red]")
        sys.exit(1)

    cfg = Config(config)
    asyncio.run(run_analyze(cfg))


@cli.command()
@click.argument("requirement", type=str)
@click.option("--config", "-c", default="config.yaml", help="配置文件路径")
@click.option("--output", "-o", default="output.mp4", help="输出文件名")
@click.option("--timeline-dir", "-t", default=None, help="使用已有时间轴的目录（含 timeline_*.json），跳过分析步骤")
@click.option("--bgm", default=None, help="背景音乐文件路径，快捷模式直接替换原声（如 bgm/summer.mp3）")
@click.option("--bgm-cfg", default=None, help="BGM 配置文件路径，支持音量混合等高级选项（如 bgm.yaml）")
def edit(requirement: str, config: str, output: str, timeline_dir: str, bgm: str, bgm_cfg: str):
    """根据自然语言需求剪辑视频（输入源为 source_clips/ 下的所有 .mp4）

    \b
    示例:
      python main.py edit "提取所有有人在说话的片段"
      python main.py edit "保留户外风景部分" -o result.mp4
      python main.py edit "精彩集锦" -t temp/
      python main.py edit "火山片段" --bgm bgm/summer.mp3
      python main.py edit "火山片段" --bgm-cfg bgm.yaml
    """
    console.print(Panel.fit("[bold cyan]CleverClip —— AI 视频自动剪辑[/bold cyan]"))

    if not check_ffmpeg():
        console.print("[red]错误: 需要安装 ffmpeg 和 ffprobe[/red]")
        sys.exit(1)

    cfg = Config(config)
    result_path = asyncio.run(run_edit(cfg, timeline_dir, requirement, output, bgm, bgm_cfg))

    console.print(f"\n[bold green]🎉 剪辑完成！输出文件: {result_path}[/bold green]")


if __name__ == "__main__":
    cli()
