"""视频抽帧模块 —— 使用 ffmpeg 按固定频率抽取帧"""

import subprocess
from pathlib import Path

from rich.console import Console

from .config import Config
from .utils import get_video_duration

console = Console()


class FrameExtractor:
    """按固定间隔从视频中抽取帧"""

    def __init__(self, config: Config):
        self.config = config

    def extract(self, video_path: str | Path, session_dir: Path | None = None) -> list[dict]:
        """
        从视频中抽取帧，返回帧信息列表。

        每个元素: {"timestamp": float, "image_path": str, "source": str}

        Args:
            video_path: 视频文件路径
            session_dir: 存放帧图片的目录，默认使用 temp_dir/frames
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        source_name = video_path.stem
        frames_dir = session_dir / "frames" if session_dir else self.config.temp_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        duration = get_video_duration(video_path)
        interval = self.config.frame_interval
        fps_filter = f"fps=1/{interval}"

        console.print(f"[cyan]视频:[/cyan] {video_path.name}  |  [cyan]时长:[/cyan] {duration:.1f}s  |  [cyan]抽帧间隔:[/cyan] {interval}s")

        # 使用 ffmpeg 抽帧，文件名带视频名前缀以避免多视频冲突
        frame_prefix = f"frame_{source_name}_"
        output_pattern = str(frames_dir / f"{frame_prefix}%06d.jpg")
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vf", fps_filter,
            "-q:v", str(self.config.frame_quality),
            "-y", output_pattern,
        ]

        console.print("[dim]正在抽帧...[/dim]")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 抽帧失败:\n{result.stderr}")

        # 收集帧信息
        frame_files = sorted(frames_dir.glob(f"{frame_prefix}*.jpg"))
        if self.config.max_frames > 0 and len(frame_files) > self.config.max_frames:
            frame_files = frame_files[: self.config.max_frames]

        frames = []
        for i, f in enumerate(frame_files):
            timestamp = i * interval
            frames.append({
                "timestamp": round(timestamp, 3),
                "image_path": str(f),
                "source": source_name,
            })

        console.print(f"[green]✓ [{video_path.name}] 抽取了 {len(frames)} 帧[/green]")
        return frames
