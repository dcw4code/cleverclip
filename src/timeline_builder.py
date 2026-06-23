"""时间轴构建模块 —— 将识别结果组织为结构化时间轴"""

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


class TimelineBuilder:
    """构建和管理视频内容时间轴"""

    def __init__(self):
        self.entries: list[dict] = []

    def build_from_frames(self, analyzed_frames: list[dict]) -> list[dict]:
        """将分析后的帧数据组织为时间轴"""
        self.entries = []
        for frame in analyzed_frames:
            self.entries.append({
                "source": frame.get("source", ""),
                "timestamp": frame["timestamp"],
                "description": frame.get("description", ""),
                "objects": frame.get("objects", []),
                "people": frame.get("people", []),
                "text_overlay": frame.get("text_overlay", ""),
                "scene_type": frame.get("scene_type", ""),
            })
        return self.entries

    def display(self) -> None:
        """以表格形式展示时间轴"""
        if not self.entries:
            console.print("[yellow]时间轴为空[/yellow]")
            return

        table = Table(title="视频内容时间轴", show_lines=True)
        table.add_column("来源", style="blue", width=15)
        table.add_column("时间", style="cyan", width=10)
        table.add_column("场景", style="magenta", width=12)
        table.add_column("描述", style="white", min_width=30)
        table.add_column("关键物体", style="dim", min_width=15)
        table.add_column("字幕", style="yellow", min_width=10)

        for e in self.entries:
            ts = f"{e['timestamp']:.1f}s"
            table.add_row(
                e.get("source", ""),
                ts,
                e.get("scene_type", ""),
                e.get("description", "")[:60],
                ", ".join(e.get("objects", []))[:30],
                e.get("text_overlay", "")[:20],
            )

        console.print(table)

    def save(self, output_path: str | Path) -> None:
        """将时间轴保存为 JSON 文件"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=2)
        console.print(f"[green]✓ 时间轴已保存至 {output_path}[/green]")

    def get_summary_text(self) -> str:
        """返回时间轴的纯文本摘要（用于传给 LLM）"""
        lines = []
        for e in self.entries:
            source = e.get("source", "")
            parts = [f"[{source}][{e['timestamp']:.1f}s]"]
            if e.get("description"):
                parts.append(e["description"])
            if e.get("scene_type"):
                parts.append(f"(场景: {e['scene_type']})")
            if e.get("objects"):
                parts.append(f"[物体: {', '.join(e['objects'])}]")
            if e.get("text_overlay"):
                parts.append(f"「字幕: {e['text_overlay']}」")
            lines.append(" ".join(parts))
        return "\n".join(lines)
