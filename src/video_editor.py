"""视频剪辑模块 —— 使用 ffmpeg 执行裁切与拼装"""

import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import Config
from .utils import format_timestamp

console = Console()


class VideoEditor:
    """使用 ffmpeg 裁切和拼接视频片段（支持多来源视频混合剪辑）"""

    def __init__(self, config: Config):
        self.config = config

    def edit(self, source_videos: dict[str, Path], clips: list[dict], output_name: str = "output.mp4") -> Path:
        """
        根据剪辑方案裁切并拼接视频片段。

        Args:
            source_videos: {来源名称(stem): 视频文件路径} 映射表
            clips: [{"source": str, "start": float, "end": float, "reason": str}, ...]
            output_name: 输出文件名

        Returns:
            输出文件路径
        """
        if not clips:
            console.print("[red]没有剪辑片段，跳过视频生成[/red]")
            raise ValueError("clips 为空")

        self._display_clips(clips)

        # 1. 裁切每个片段
        segment_paths = []
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            for i, clip in enumerate(clips):
                source = clip.get("source", "")
                start = clip["start"]
                end = clip["end"]
                seg_path = tmpdir / f"segment_{i:04d}.mp4"

                video_path = source_videos.get(source)
                if video_path is None:
                    raise FileNotFoundError(f"找不到来源视频: {source}")

                console.print(
                    f"[dim]裁切片段 {i + 1}/{len(clips)}: "
                    f"[{source}] {format_timestamp(start)} → {format_timestamp(end)}[/dim]"
                )
                self._cut_segment(Path(video_path), start, end, seg_path)
                segment_paths.append(seg_path)

            # 2. 拼接所有片段
            output_path = self.config.output_dir / output_name
            self._concat_segments(segment_paths, output_path)

        console.print(f"[green]✓ 输出视频已生成: {output_path}[/green]")
        return output_path

    def _cut_segment(self, video_path: Path, start: float, end: float, output: Path) -> None:
        """裁切单个片段"""
        duration = end - start
        cmd = [
            "ffmpeg",
            "-ss", f"{start:.3f}",       # 起始时间
            "-i", str(video_path),
            "-t", f"{duration:.3f}",     # 持续时长
            "-c:v", self.config.video_codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-c:a", "aac",
            "-avoid_negative_ts", "make_zero",
            "-y", str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"裁切片段失败 ({start:.1f}s-{end:.1f}s):\n{result.stderr[-500:]}"
            )

    def _concat_segments(self, segment_paths: list[Path], output: Path) -> None:
        """拼接多个片段（使用 concat demuxer）"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, dir=str(segment_paths[0].parent)
        ) as f:
            for seg in segment_paths:
                f.write(f"file '{seg.resolve()}'\n")
            list_path = f.name

        cmd = [
            "ffmpeg",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-y", str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        Path(list_path).unlink(missing_ok=True)

        if result.returncode != 0:
            # concat copy 可能因编码不一致失败，回退到重新编码
            console.print("[yellow]直接拼接失败，尝试重新编码拼接...[/yellow]")
            cmd = [
                "ffmpeg",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c:v", self.config.video_codec,
                "-crf", str(self.config.crf),
                "-preset", self.config.preset,
                "-c:a", "aac",
                "-y", str(output),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"拼接视频失败:\n{result.stderr[-500:]}")

    @staticmethod
    def _display_clips(clips: list[dict]) -> None:
        """以表格形式展示剪辑方案"""
        table = Table(title="剪辑方案", show_lines=True)
        table.add_column("#", style="dim", width=4)
        table.add_column("来源", style="blue", width=15)
        table.add_column("起始", style="cyan", width=14)
        table.add_column("结束", style="cyan", width=14)
        table.add_column("时长", style="green", width=10)
        table.add_column("说明", style="white")

        total_duration = 0
        for i, clip in enumerate(clips):
            start = clip["start"]
            end = clip["end"]
            dur = end - start
            total_duration += dur
            table.add_row(
                str(i + 1),
                clip.get("source", ""),
                format_timestamp(start),
                format_timestamp(end),
                f"{dur:.1f}s",
                clip.get("reason", ""),
            )

        console.print(table)
        console.print(f"[bold green]总时长: {total_duration:.1f}s[/bold green]")
