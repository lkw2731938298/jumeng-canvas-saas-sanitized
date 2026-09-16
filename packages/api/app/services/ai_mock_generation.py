"""AI_MOCK_ENABLED=true 时：不调上游模型，返回可跑通画布流程的占位产物。

测试服联调一键出海 / 爆款复刻全流程用；生产必须 AI_MOCK_ENABLED=false。
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..core.config import get_settings
from ..models.job import GenerationJob

logger = logging.getLogger(__name__)


def is_ai_mock_enabled() -> bool:
    """是否启用本地 Mock 生成（不调上游）。"""
    return bool(get_settings().ai_mock_enabled)


def _job_params(job: GenerationJob) -> dict[str, Any]:
    return job.input_params if isinstance(job.input_params, dict) else {}


def _count_storyboard_shots(job: GenerationJob) -> int:
    """从参考图数量或用户文案里的「镜头N」推断镜数。"""
    params = _job_params(job)
    refs = params.get("references") or []
    img_n = 0
    for item in refs:
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "").lower()
        if t in ("image", "img", "keyframe"):
            img_n += 1
    if img_n >= 2:
        return max(2, min(img_n, 16))

    content = str(params.get("content") or params.get("prompt") or "")
    idxs = [int(x) for x in re.findall(r"镜头\s*(\d+)", content)]
    if idxs:
        return max(2, min(max(idxs), 16))
    return 4


def _detect_spoken_language(job: GenerationJob) -> str:
    """出海文案里常带「对白语言：English」；默认 English。"""
    content = str(_job_params(job).get("content") or "")
    m = re.search(r"对白语言(?:[（(]硬约束[）)])?[：:]\s*([^\n—\-|｜]+)", content)
    if m:
        lang = m.group(1).strip()
        if lang:
            return lang
    if "Japanese" in content or "日语" in content:
        return "Japanese"
    if "Korean" in content or "韩语" in content:
        return "Korean"
    if "Portuguese" in content:
        return "Portuguese"
    return "English"


def build_mock_text_content(job: GenerationJob) -> str:
    """按 textPromptKind 组装可被前端解析的 Mock 文本。"""
    params = _job_params(job)
    kind = str(
        params.get("textPromptKind")
        or params.get("text_prompt_kind")
        or params.get("canvasTool")
        or params.get("canvas_tool")
        or ""
    ).strip()
    content = str(params.get("content") or params.get("prompt") or "")

    if kind in (
        "storyboard_overseas_localize",
        "storyboard_from_video",
        "storyboard_from_image",
        "storyboard_table",
    ):
        n = _count_storyboard_shots(job)
        lang = _detect_spoken_language(job)
        is_overseas = kind == "storyboard_overseas_localize" or "出海" in content
        shot_rows: list[dict[str, str]] = []
        for i in range(1, n + 1):
            dialogue = (
                f"Hey, this is shot {i} — testing overseas remake."
                if lang.lower().startswith("english")
                else f"[{lang}] dialogue for shot {i}"
            )
            video_prompt = (
                f"Medium shot of localized character in modern outfit matching subject refs, "
                f"camera push-in, natural street light. "
                f"Spoken dialogue in {lang} only, no Chinese speech. {dialogue}"
            )
            shot_rows.append(
                {
                    "镜头号": str(i),
                    "时长": "3s",
                    "画面描述": f"Mock localized beat {i}: character walks and talks in target market look.",
                    "景别": "中景" if i % 2 else "近景",
                    "光影氛围": "自然日光，清晰通透",
                    "对话": dialogue,
                    "音效": "环境底噪",
                    "运镜提示词": "缓慢推进，轻微手持感",
                    "视频提示词": video_prompt,
                }
            )
        roles = [
            {
                "name": "Alex",
                "extractPrompt": (
                    "25岁男性，身高180，都市青年；短发浅棕，五官立体；"
                    "米色休闲西装外套，白T恤，深蓝牛仔裤，白运动鞋"
                ),
            },
            {
                "name": "Maya",
                "extractPrompt": (
                    "24岁女性，身高165；黑色长发，自然妆；"
                    "浅蓝牛仔外套，白色上衣，黑色长裤，帆布鞋"
                ),
            },
        ]
        locale_plan = (
            f"Mock locale plan: cast/outfit/scene fully localized; dialogue in {lang} only."
            if is_overseas
            else "Mock replace plan: keep camera rhythm, swap subjects."
        )
        payload = {
            "styleSummary": "Mock style: bright vertical short-form, punchy cuts.",
            "localePlan": locale_plan,
            "replacePlan": locale_plan,
            "shotRows": shot_rows,
            "roles": roles,
            "scenes": [{"name": "CityStreet", "extractPrompt": "现代都市街景，干净招牌，无中文店招"}],
            "props": [{"name": "CoffeeCup", "extractPrompt": "外卖咖啡杯特写，英文杯套"}],
        }
        return json.dumps(payload, ensure_ascii=False)

    if kind == "storyboard_camera":
        return "缓慢推进，轻微手持，结尾定格特写"
    if kind == "storyboard_video":
        return (
            "角色中景行走并口播，运镜缓慢推进。"
            " Spoken dialogue in English only, no Chinese speech."
        )

    snippet = content.strip()[:120] or "hello"
    return f"[AI_MOCK] Echo:\n{snippet}"


async def build_mock_media_bytes(
    *,
    category: str,
    prompt: str,
    model_name: str,
) -> tuple[bytes, str, str, str]:
    """返回 (data, ext, mime, asset_category)。"""
    cat = (category or "image").lower()
    if cat == "video":
        data, ext, mime = await _mock_video_bytes()
        return data, ext, mime, "video"
    if cat == "audio":
        return b"AI_MOCK audio placeholder\n", "txt", "text/plain; charset=utf-8", "audio"

    safe_prompt = html.escape((prompt or "mock")[:72])
    safe_model = html.escape((model_name or "ai_mock")[:40])
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='768' height='1024'>
  <defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
    <stop offset='0%' stop-color='#0f172a'/><stop offset='100%' stop-color='#1e293b'/>
  </linearGradient></defs>
  <rect fill='url(#g)' width='768' height='1024'/>
  <text fill='#94a3b8' x='384' y='460' text-anchor='middle' font-size='28' font-family='sans-serif'>AI_MOCK</text>
  <text fill='#cbd5e1' x='384' y='510' text-anchor='middle' font-size='16' font-family='sans-serif'>{safe_model}</text>
  <text fill='#64748b' x='384' y='560' text-anchor='middle' font-size='14' font-family='sans-serif'>{safe_prompt}</text>
</svg>"""
    return svg.encode("utf-8"), "svg", "image/svg+xml", "image"


async def _mock_video_bytes() -> tuple[bytes, str, str]:
    """优先 ffmpeg 生成 3s 色块（可播放）；失败退回极小 mp4。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            with tempfile.TemporaryDirectory(prefix="ai_mock_vid_") as tmp:
                out = Path(tmp) / "mock.mp4"
                proc = await asyncio.create_subprocess_exec(
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x1a1a2e:s=640x360:d=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=stereo",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    "-t",
                    "3",
                    "-y",
                    str(out),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0 and out.is_file() and out.stat().st_size > 32:
                    return out.read_bytes(), "mp4", "video/mp4"
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_mock ffmpeg video failed: %s", exc)
    return (
        bytes.fromhex(
            "0000001C6674797069736F6D0000020069736F6D69736F326D703431"
            "0000000866726565000000026D64617400000000"
        ),
        "mp4",
        "video/mp4",
    )


async def mock_delay() -> None:
    """短延迟，模拟排队体感。"""
    await asyncio.sleep(max(0.05, float(get_settings().ai_mock_min_delay_s)))
