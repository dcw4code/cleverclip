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

    def edit(self, source_videos: dict[str, Path], clips: list[dict],
             output_name: str = "output.mp4",
             bgm_path: str | Path | None = None,
             bgm_config: dict | None = None,
             lut_path: str | Path | None = None,
             lut_strength: float = 1.0) -> Path:
        """
        根据剪辑方案裁切并拼接视频片段，可选添加背景音乐和 LUT 滤镜。

        Args:
            source_videos: {来源名称(stem): 视频文件路径} 映射表
            clips: [{"source": str, "start": float, "end": float, "reason": str}, ...]
            output_name: 输出文件名
            bgm_path: 背景音乐文件路径（简单模式，直接替换原声）
            bgm_config: 背景音乐配置字典（高级模式，来自 bgm.yaml）
            lut_path: LUT 文件路径（.cube 格式），指定后在裁切时应用
            lut_strength: LUT 应用强度 (0.0~1.0)，1.0 为完全应用

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
                self._cut_segment(Path(video_path), start, end, seg_path,
                                  lut_path=lut_path, lut_strength=lut_strength)
                segment_paths.append(seg_path)

            # 2. 拼接所有片段
            concat_output = self.config.output_dir / output_name
            self._concat_segments(segment_paths, concat_output)

        console.print(f"[green]✓ 拼接完成: {concat_output}[/green]")

        # 3. 添加背景音乐
        #    bgm_config（高级模式）优先；其次 bgm_path（简单模式）
        if bgm_config:
            self._apply_bgm_config(concat_output, bgm_config, bgm_path)
        elif bgm_path:
            bgm_path = Path(bgm_path)
            if not bgm_path.exists():
                console.print(f"[red]BGM 文件不存在: {bgm_path}，跳过背景音乐[/red]")
            else:
                console.print(f"\n[dim]添加背景音乐（替换原声）: {bgm_path.name}...[/dim]")
                final_output = concat_output.with_suffix(".tmp.mp4")
                self._replace_audio_with_bgm(concat_output, bgm_path, final_output)
                concat_output.unlink()
                final_output.rename(concat_output)
                console.print(f"[green]✓ 背景音乐已添加（已替换原声）[/green]")

        console.print(f"[green]✓ 输出视频已生成: {concat_output}[/green]")
        return concat_output

    def _cut_segment(self, video_path: Path, start: float, end: float, output: Path,
                     lut_path: str | Path | None = None,
                     lut_strength: float = 1.0) -> None:
        """裁切单个片段，可选应用 LUT 滤镜"""
        duration = end - start

        # 构造视频滤镜
        vf_filters = []
        if lut_path:
            lut_path = Path(lut_path)
            if lut_path.exists():
                lut_str = f"lut3d='{lut_path}'"
                if lut_strength < 1.0:
                    lut_str += f":strength={lut_strength}"
                vf_filters.append(lut_str)
                console.print(f"[dim]  LUT: {lut_path.name} (strength={lut_strength})[/dim]")
            else:
                console.print(f"[yellow]  LUT 文件不存在: {lut_path}，跳过 LUT[/yellow]")

        cmd = [
            "ffmpeg",
            "-ss", f"{start:.3f}",       # 起始时间
            "-i", str(video_path),
            "-t", f"{duration:.3f}",     # 持续时长
        ]

        # 有 LUT 时需要重编码视频（不能用 copy）
        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])

        cmd.extend([
            "-c:v", self.config.video_codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-c:a", "aac",
            "-avoid_negative_ts", "make_zero",
            "-y", str(output),
        ])
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

    def _replace_audio_with_bgm(self, video_path: Path, bgm_path: Path, output: Path) -> None:
        """
        用 BGM 替换视频的原始音频。

        - BGM 短于视频时自动循环
        - BGM 长于视频时截断到视频长度
        - 视频画面直接 copy，不重新编码
        """
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-stream_loop", "-1",          # BGM 无限循环
            "-i", str(bgm_path),
            "-map", "0:v",                 # 取视频画面
            "-map", "1:a",                 # 取 BGM 音频（替换原声）
            "-c:v", "copy",               # 视频直接复制
            "-c:a", "aac",                # 音频编码为 AAC
            "-shortest",                   # 输出以视频长度为准
            "-y", str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"添加背景音乐失败:\n{result.stderr[-500:]}")

    def _mix_audio_with_bgm(self, video_path: Path, bgm_path: Path,
                            original_vol: float, bgm_vol: float, output: Path) -> None:
        """
        将原声与 BGM 按指定音量比例混合。

        Args:
            original_vol: 原声音量 (0.0~1.0)
            bgm_vol: BGM 音量 (0.0~1.0)
        """
        filter_complex = (
            f"[0:a]volume={original_vol}[a1];"
            f"[1:a]volume={bgm_vol}[a2];"
            f"[a1][a2]amix=inputs=2:duration=first[aout]"
        )
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-stream_loop", "-1",
            "-i", str(bgm_path),
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            "-y", str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"混合背景音乐失败:\n{result.stderr[-500:]}")

    def _apply_bgm_config(self, concat_output: Path, bgm_config: dict,
                          bgm_override: str | Path | None = None) -> None:
        """
        根据 bgm.yaml 配置添加背景音乐。

        Args:
            concat_output: 拼接后的视频路径
            bgm_config: bgm.yaml 解析后的字典
            bgm_override: 命令行 --bgm 指定的路径，优先于配置文件中的 bgm_path
        """
        keep_original = bgm_config.get("keep_original_audio", False)
        original_vol_raw = bgm_config.get("original_volume", 5)
        bgm_vol_raw = bgm_config.get("bgm_volume", 5)
        config_bgm_path = bgm_config.get("bgm_path", "")

        # 确定最终 BGM 文件路径：命令行 --bgm 优先，其次配置文件
        bgm_path = Path(bgm_override) if bgm_override else Path(config_bgm_path)
        if not bgm_path.exists():
            console.print(f"[red]BGM 文件不存在: {bgm_path}，跳过背景音乐[/red]")
            return

        # 音量从 0-10 刻度转换为 0.0-1.0
        original_vol = max(0.0, min(1.0, original_vol_raw / 10.0))
        bgm_vol = max(0.0, min(1.0, bgm_vol_raw / 10.0))

        console.print(f"\n[dim]背景音乐配置: {bgm_path.name}[/dim]")
        console.print(
            f"[dim]  保留原声: {'是' if keep_original else '否'}"
            f"  |  原声: {original_vol_raw}/10  BGM: {bgm_vol_raw}/10[/dim]"
        )

        final_output = concat_output.with_suffix(".tmp.mp4")

        if keep_original:
            console.print("[dim]  模式: 原声 + BGM 混合...[/dim]")
            self._mix_audio_with_bgm(concat_output, bgm_path, original_vol, bgm_vol, final_output)
        else:
            console.print("[dim]  模式: BGM 替换原声...[/dim]")
            self._replace_audio_with_bgm(concat_output, bgm_path, final_output)

        concat_output.unlink()
        final_output.rename(concat_output)
        console.print(f"[green]✓ 背景音乐已添加[/green]")

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
