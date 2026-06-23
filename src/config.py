"""配置加载模块"""

from pathlib import Path
from typing import Any

import yaml


class Config:
    """全局配置管理"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

    # ---- LLM 配置 ----
    @property
    def vision_config(self) -> dict[str, Any]:
        return self._data.get("llm", {}).get("vision", {})

    @property
    def text_config(self) -> dict[str, Any]:
        return self._data.get("llm", {}).get("text", {})

    # ---- 抽帧配置 ----
    @property
    def frame_interval(self) -> float:
        return float(self._data.get("frame_extraction", {}).get("interval", 1.0))

    @property
    def max_frames(self) -> int:
        return int(self._data.get("frame_extraction", {}).get("max_frames", 0))

    @property
    def frame_quality(self) -> int:
        return int(self._data.get("frame_extraction", {}).get("quality", 2))

    # ---- 源视频目录 ----
    @property
    def source_clips_dir(self) -> Path:
        d = Path(self._data.get("input", {}).get("source_clips_dir", "./source_clips"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def discover_source_videos(self) -> list[Path]:
        """扫描 source_clips 目录下所有 .mp4 文件，按文件名排序"""
        videos = sorted(self.source_clips_dir.glob("*.mp4"))
        return videos

    # ---- 输出配置 ----
    @property
    def temp_dir(self) -> Path:
        d = Path(self._data.get("output", {}).get("temp_dir", "./temp"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def output_dir(self) -> Path:
        d = Path(self._data.get("output", {}).get("output_dir", "./output"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def video_codec(self) -> str:
        return self._data.get("output", {}).get("video_codec", "libx264")

    @property
    def crf(self) -> int:
        return int(self._data.get("output", {}).get("crf", 18))

    @property
    def preset(self) -> str:
        return self._data.get("output", {}).get("preset", "medium")
