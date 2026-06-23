"""LLM 客户端 —— 封装视觉识别与文本推理"""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, OpenAI
from rich.console import Console

from .config import Config

console = Console()

# 视觉识别系统提示词
VISION_SYSTEM_PROMPT = """你是一个专业的视频内容分析助手。请仔细分析给定的视频帧图片，并返回 JSON 格式的分析结果。

对于每一帧，请描述：
1. "description": 画面中发生的事情（中文，简洁但准确）
2. "objects": 画面中可见的关键物体或场景元素（列表）
3. "people": 画面中出现的人物描述（如穿着、动作等，如果没有人则为空列表）
4. "text_overlay": 画面中出现的文字（如字幕、标牌等，没有则为空字符串）
5. "scene_type": 场景类型（如"室内"、"室外"、"自然风光"、"城市街道"等）

请以严格的 JSON 数组格式返回，每个元素对应一帧图片。不要包含任何其他文字。"""


# 剪辑规划系统提示词（按单个视频调用）
PLANNER_SYSTEM_PROMPT = """你是一个专业的视频剪辑规划助手。用户会给你一段视频的时间轴分析结果（包含时间点和内容描述），以及一段剪辑需求。

请根据需求和内容分析，规划出需要从这段视频中剪辑的片段，返回 JSON 格式：

{
  "clips": [
    {
      "start": 开始时间（秒，数字）,
      "end": 结束时间（秒，数字）,
      "reason": "选择这段的原因（中文）"
    }
  ]
}

注意：
- 片段时间应基于该视频中的实际内容
- 如果该视频中没有符合需求的内容，返回空数组 {"clips": []}
- 片段之间不要重叠
- 按时间顺序排列
- 只返回 JSON，不要包含其他文字"""


class LLMClient:
    """统一的 LLM 调用客户端"""

    def __init__(self, config: Config):
        self.config = config
        vc = config.vision_config
        tc = config.text_config

        self._vision_client = AsyncOpenAI(
            base_url=vc.get("base_url", "https://api.openai.com/v1"),
            api_key=vc.get("api_key", ""),
        )
        self._vision_model = vc.get("model", "gpt-4o")
        self._vision_max_tokens = vc.get("max_tokens", 500)
        self._vision_batch_size = max(1, int(vc.get("batch_size", 5)))
        self._vision_concurrency = max(1, int(vc.get("concurrency", 5)))

        self._text_client = OpenAI(
            base_url=tc.get("base_url", "https://api.openai.com/v1"),
            api_key=tc.get("api_key", ""),
        )
        self._text_model = tc.get("model", "gpt-4o")
        self._text_max_tokens = tc.get("max_tokens", 2000)

    # ----------------------------------------------------------------
    # 视觉识别（并发）
    # ----------------------------------------------------------------

    @staticmethod
    def _encode_image(image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _build_vision_content(self, batch: list[dict]) -> list[dict[str, Any]]:
        """构造视觉 LLM 的请求内容"""
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"以下是视频中的 {len(batch)} 帧图片，按时间顺序排列。"
                    "请分析每一帧并返回 JSON 数组。"
                ),
            }
        ]
        for frame in batch:
            b64 = self._encode_image(frame["image_path"])
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        return content

    async def _analyze_batch(self, batch_idx: int, batch: list[dict], total_batches: int) -> list[dict]:
        """分析单个批次并返回增强后的帧列表"""
        console.print(f"[dim]识别批次 {batch_idx + 1}/{total_batches} (并发中)...[/dim]")

        response = await self._vision_client.chat.completions.create(
            model=self._vision_model,
            messages=[
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {"role": "user", "content": self._build_vision_content(batch)},
            ],
            max_tokens=self._vision_max_tokens,
        )

        raw = response.choices[0].message.content.strip()
        batch_results = self._parse_json_response(raw)

        results = []
        for i, frame in enumerate(batch):
            if i < len(batch_results):
                r = batch_results[i]
                frame.update({
                    "description": r.get("description", ""),
                    "objects": r.get("objects", []),
                    "people": r.get("people", []),
                    "text_overlay": r.get("text_overlay", ""),
                    "scene_type": r.get("scene_type", ""),
                })
            else:
                frame.update({
                    "description": "", "objects": [], "people": [],
                    "text_overlay": "", "scene_type": "",
                })
            results.append(frame)
        return results

    async def analyze_frames(self, frames: list[dict]) -> list[dict]:
        """
        对帧列表进行视觉内容识别，并发请求以提高效率。

        为避免单次请求过大，按 batch_size 分批发送；
        批次之间使用 asyncio.Semaphore 限制并发数量。
        批次大小和并发数从配置文件的 llm.vision.batch_size 和 llm.vision.concurrency 读取。

        返回增强后的帧列表，每帧新增以下字段：
        - description, objects, people, text_overlay, scene_type
        """
        batch_size = self._vision_batch_size
        total = len(frames)
        total_batches = (total + batch_size - 1) // batch_size
        console.print(
            f"[dim]共 {total} 帧，分 {total_batches} 批，"
            f"每批 {batch_size} 帧，并发数 {self._vision_concurrency}[/dim]"
        )

        # 准备所有批次
        batches = []
        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, total)
            batches.append((batch_idx, frames[start:end]))

        semaphore = asyncio.Semaphore(self._vision_concurrency)

        async def _run_with_semaphore(batch_idx: int, batch: list[dict]) -> list[dict]:
            async with semaphore:
                return await self._analyze_batch(batch_idx, batch, total_batches)

        # 并发执行所有批次
        batch_results = await asyncio.gather(
            *[_run_with_semaphore(idx, batch) for idx, batch in batches]
        )

        # 按原始顺序合并结果
        results = []
        for batch_result in batch_results:
            results.extend(batch_result)

        console.print(f"[green]✓ 完成了 {len(results)} 帧的内容识别[/green]")
        return results

    # ----------------------------------------------------------------
    # 剪辑规划
    # ----------------------------------------------------------------

    def plan_clips(self, source_name: str, timeline: list[dict], requirement: str) -> list[dict]:
        """
        根据单个视频的时间轴和用户需求，生成该视频的剪辑片段方案。

        Args:
            source_name: 视频名称（不含扩展名）
            timeline: 该视频的时间轴条目列表
            requirement: 用户的自然语言剪辑需求

        返回: [{"source": str, "start": float, "end": float, "reason": str}, ...]
        """
        # 构建时间轴摘要
        timeline_text = self._build_timeline_text(timeline)

        user_msg = (
            f"以下是视频「{source_name}」的时间轴内容分析：\n\n{timeline_text}\n\n"
            f"用户的剪辑需求：{requirement}\n\n"
            f"请根据需求和时间轴内容，规划需要从「{source_name}」中剪辑的片段。"
        )

        response = self._text_client.chat.completions.create(
            model=self._text_model,
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=self._text_max_tokens,
        )

        raw = response.choices[0].message.content.strip()
        parsed = self._parse_json_response(raw)

        if isinstance(parsed, dict) and "clips" in parsed:
            raw_clips = parsed["clips"]
        elif isinstance(parsed, list):
            raw_clips = parsed
        else:
            raw_clips = []

        # 为每个片段补充 source 字段
        clips = []
        for clip in raw_clips:
            clip["source"] = source_name
            clips.append(clip)

        console.print(f"[green]✓ [{source_name}] 规划了 {len(clips)} 个片段[/green]")
        return clips

    # ----------------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------------

    @staticmethod
    def _parse_json_response(raw: str) -> Any:
        """尝试从 LLM 回复中提取 JSON"""
        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 代码块
        if "```json" in raw:
            start = raw.index("```json") + 7
            end = raw.index("```", start)
            return json.loads(raw[start:end].strip())
        if "```" in raw:
            start = raw.index("```") + 3
            end = raw.index("```", start)
            return json.loads(raw[start:end].strip())

        # 尝试找到第一个 { 或 [ 到最后一个 } 或 ]
        for open_ch, close_ch in [("{", "}"), ("[", "]")]:
            if open_ch in raw and close_ch in raw:
                start = raw.index(open_ch)
                end = raw.rindex(close_ch)
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    continue

        console.print(f"[red]无法解析 LLM 的 JSON 回复:\n{raw[:200]}...[/red]")
        return []

    @staticmethod
    def _build_timeline_text(timeline: list[dict]) -> str:
        lines = []
        for item in timeline:
            ts = item.get("timestamp", 0)
            source = item.get("source", "")
            desc = item.get("description", "")
            scene = item.get("scene_type", "")
            overlay = item.get("text_overlay", "")
            objs = ", ".join(item.get("objects", []))

            parts = [f"[{source}][{ts:.1f}s]"]
            if desc:
                parts.append(desc)
            if scene:
                parts.append(f"(场景: {scene})")
            if objs:
                parts.append(f"[物体: {objs}]")
            if overlay:
                parts.append(f"「字幕: {overlay}」")
            lines.append(" ".join(parts))

        return "\n".join(lines)
