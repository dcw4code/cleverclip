"""通用工具函数"""

import json
import subprocess
from pathlib import Path
from typing import Optional


def get_video_duration(video_path: str | Path) -> float:
    """使用 ffprobe 获取视频时长（秒）"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS.mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def check_ffmpeg() -> bool:
    """检查系统是否安装了 ffmpeg/ffprobe"""
    for tool in ["ffmpeg", "ffprobe"]:
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
    return True
