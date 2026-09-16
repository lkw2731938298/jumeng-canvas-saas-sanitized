"""聚梦 AI 网关上游：视频优先 ``POST /v1/video/generations``，图片 ``POST /v1/images/generations``。

文档：https://docs.example.com/guide/getting-started
定价页模型 id 须原样提交（含 /discount 后缀）。
去字幕模型：火山字幕擦除-视频字幕擦除（精准版）
提交：POST {base}/videos ；轮询：GET {base}/videos/{id} ；下载：GET {base}/videos/{id}/content
密钥仅经 llm-keys / DB 加密凭证读取，禁止写死在代码中。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ...core.llm_keys import get_llm_keys
from .ark import (
    build_seedance_i2v_body,
    build_seedance_multimodal_body,
    build_seedance_t2v_body,
    normalize_seedance_resolution,
)
from .errors import UpstreamError
from .trace_context import maybe_flush_upstream_trace, note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)

DEFAULT_JUMENGAI_API_BASE = "https://api.example.com/v1"
# 网关文档展示名，作为 upstream_model 原样提交
DEFAULT_SUBTITLE_ERASE_MODEL = "火山字幕擦除-视频字幕擦除（精准版）"

_SUBTITLE_ERASE_TYPES = frozenset({"subtitle", "watermark", "all"})
_SUBTITLE_ENCODE_MODES = frozenset({"size", "quality"})
_SIZE_ALIASES = {
    "480": "480P",
    "480p": "480P",
    "480P": "480P",
    "720": "720P",
    "720p": "720P",
    "720P": "720P",
    "768": "768P",
    "768p": "768P",
    "768P": "768P",
    "1080": "1080P",
    "1080p": "1080P",
    "1080P": "1080P",
    "4k": "4K",
    "4K": "4K",
    "2160": "4K",
    "2160p": "4K",
    "2160P": "4K",
}


def _creds() -> tuple[str, str]:
    cfg = get_llm_keys()
    key = (cfg.jumengai.api_key or "").strip()
    base = (cfg.jumengai.api_base or DEFAULT_JUMENGAI_API_BASE).rstrip("/")
    if not key:
        raise UpstreamError("未配置 JUMENGAI_API_KEY", code="NOT_CONFIGURED")
    return key, base


def _error_message(body: dict[str, Any], fallback: str) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "").strip()
        if msg:
            return msg[:500]
    return str(body.get("message") or body.get("msg") or fallback)[:500]


def _response_json(resp: httpx.Response) -> dict[str, Any]:
    """解析上游 JSON。504/HTML/空 body 不得把 JSONDecodeError 抛给用户。"""
    raw = (resp.content or b"").strip()
    if not raw:
        return {}
    try:
        data = resp.json()
    except json.JSONDecodeError:
        logger.warning(
            "[upstream] jumengai non-json HTTP %s ctype=%s body=%s",
            resp.status_code,
            (resp.headers.get("content-type") or "")[:80],
            raw[:200],
        )
        return {}
    return data if isinstance(data, dict) else {}


def _raise_submit_http_error(resp: httpx.Response, body: dict[str, Any], *, fallback: str) -> None:
    """提交 HTTP≥400：网关超时停车（可能已扣上游），其它错误原样 SUBMIT_ERROR。"""
    status = int(resp.status_code)
    if status < 400:
        return
    if status in (502, 503, 504):
        raise UpstreamError(
            f"聚梦网关超时或无响应（HTTP {status}），任务可能仍在上游处理，请联系管理员核对后再提交",
            code="GATEWAY_TIMEOUT",
        )
    snippet = (resp.text or "").strip()[:300]
    raise UpstreamError(_error_message(body, snippet or fallback), code="SUBMIT_ERROR")


def _normalize_size(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return "720P"
    return _SIZE_ALIASES.get(text, _SIZE_ALIASES.get(text.upper(), "720P"))


def _normalize_erase_ratio_location(raw: Any) -> list[dict[str, float]]:
    """将框选区域规范为相对坐标 0–1（与 RunningHub 精细化版字段对齐）。"""
    if raw is None or raw == "":
        return []
    items: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UpstreamError("eraseRatioLocation 不是合法 JSON", code="INVALID_MEDIA") from exc
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise UpstreamError("eraseRatioLocation 须为数组", code="INVALID_MEDIA")

    out: list[dict[str, float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if all(k in item for k in ("topLeftX", "topLeftY", "bottomRightX", "bottomRightY")):
            tlx = float(item["topLeftX"])
            tly = float(item["topLeftY"])
            brx = float(item["bottomRightX"])
            bry = float(item["bottomRightY"])
        elif all(k in item for k in ("x", "y", "w", "h")):
            x = float(item["x"])
            y = float(item["y"])
            w = float(item["w"])
            h = float(item["h"])
            tlx, tly, brx, bry = x, y, x + w, y + h
        else:
            continue
        tlx = max(0.0, min(1.0, tlx))
        tly = max(0.0, min(1.0, tly))
        brx = max(0.0, min(1.0, brx))
        bry = max(0.0, min(1.0, bry))
        if brx <= tlx or bry <= tly:
            continue
        out.append(
            {
                "topLeftX": round(tlx, 6),
                "topLeftY": round(tly, 6),
                "bottomRightX": round(brx, 6),
                "bottomRightY": round(bry, 6),
            }
        )
    return out


def build_subtitle_erase_payload(
    *,
    model: str,
    video_url: str,
    prompt: str = "",
    seconds: int | str = 5,
    size: str = "720P",
    erase_type: str = "subtitle",
    encode_mode: str = "size",
    erase_ratio_location: Any = None,
) -> dict[str, Any]:
    """构造聚梦网关去字幕 JSON（兼容文档字段 + 源视频 URL）。"""
    url = (video_url or "").strip()
    if not url:
        raise UpstreamError("去字幕需要源视频 URL（videoUrl）", code="INVALID_MEDIA")
    et = (erase_type or "subtitle").strip().lower()
    if et not in _SUBTITLE_ERASE_TYPES:
        et = "subtitle"
    em = (encode_mode or "size").strip().lower()
    if em not in _SUBTITLE_ENCODE_MODES:
        em = "size"
    try:
        dur = int(seconds)
    except (TypeError, ValueError):
        dur = 5
    dur = max(1, min(600, dur))
    resolved_size = _normalize_size(size)
    boxes = _normalize_erase_ratio_location(erase_ratio_location)
    meta: dict[str, Any] = {
        "videoUrl": url,
        "video_url": url,
        "eraseType": et,
        "encodeMode": em,
    }
    if boxes:
        meta["eraseRatioLocation"] = boxes
    payload: dict[str, Any] = {
        "model": (model or DEFAULT_SUBTITLE_ERASE_MODEL).strip() or DEFAULT_SUBTITLE_ERASE_MODEL,
        # 网关文档把 prompt 标为必填（通用视频模板）；去字幕用说明文案占位
        "prompt": (prompt or "擦除视频字幕").strip()[:20000] or "擦除视频字幕",
        "seconds": str(dur),
        "size": resolved_size,
        # 文档字段 image 为参考资源 URL；同时透传 video / videoUrl 以免网关只认其一
        "image": url,
        "video": url,
        "videoUrl": url,
        "videos": [url],
        "metadata": json.dumps(meta, ensure_ascii=False),
        "eraseType": et,
        "encodeMode": em,
    }
    if boxes:
        payload["eraseRatioLocation"] = boxes
    return payload


_VIDEO_URL_KEYS = (
    "url",
    "video_url",
    "videoUrl",
    "output_url",
    "result_url",
    "file_url",
    "fileUrl",
    "download_url",
    "downloadUrl",
    "play_url",
    "output",
    "result",
    "video",
)


def _extract_video_url(body: dict[str, Any]) -> str:
    """从同步/轮询 JSON 取可下载视频 URL（含 NewAPI output / MiniMax CDN）。"""
    for key in _VIDEO_URL_KEYS:
        val = str(body.get(key) or "").strip()
        if val.startswith("http"):
            return val
    data = body.get("data")
    if isinstance(data, dict):
        nested = _extract_video_url(data)
        if nested:
            return nested
        outputs = data.get("outputs") or data.get("results") or data.get("video")
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                return _extract_video_url(first)
        if isinstance(outputs, str) and outputs.startswith("http"):
            return outputs
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return _extract_video_url(first)
        if isinstance(first, str) and first.startswith("http"):
            return first
    results = body.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        nested = _extract_video_url(results[0])
        if nested:
            return nested
    return ""


def _extract_task_id(body: dict[str, Any]) -> str:
    for key in ("id", "task_id", "taskId"):
        val = str(body.get(key) or "").strip()
        if val:
            return val
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("id", "task_id", "taskId"):
            val = str(data.get(key) or "").strip()
            if val:
                return val
    return ""


def _status_of(body: dict[str, Any]) -> str:
    for key in ("status", "task_status", "state"):
        val = str(body.get(key) or "").strip().lower()
        if val:
            return val
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("status", "task_status", "state"):
            val = str(data.get(key) or "").strip().lower()
            if val:
                return val
    return ""


def _content_url(api_base: str, task_id: str) -> str:
    tid = quote(str(task_id).strip(), safe="")
    return f"{api_base.rstrip('/')}/videos/{tid}/content"


def _kling_mode_from_resolution(resolution: str) -> str:
    """可灵 V3：720→std、1080→pro，与画布清晰度档对齐。"""
    text = str(resolution or "").strip().lower()
    if text in {"1080", "1080p", "pro"}:
        return "pro"
    return "std"


def _normalize_video_resolution(raw: str | None) -> str:
    """清晰度：上游常用 720p / 1080p，禁止把 720P 写进 size。"""
    mapped = _normalize_size(raw)
    if mapped == "4K":
        return "4k"
    return mapped.lower()


def _normalize_video_ratio(raw: str | None) -> str:
    """画幅比例。自适应原样保留，供 metadata；顶层 size 另映射为具体比例。"""
    text = str(raw or "").strip().replace("：", ":").replace("x", ":").replace("*", ":")
    if not text:
        return "16:9"
    low = text.lower()
    if low in {"adaptive", "auto"}:
        return "adaptive"
    allowed = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "3:2", "2:3"}
    if text in allowed:
        return text
    parts = text.split(":", 1)
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}:{parts[1]}"
    return "16:9"


def _size_as_aspect_ratio(ratio: str) -> str:
    """聚梦网关把 OpenAI ``size`` 校验成画幅比例（如 16:9），不能传 720P。"""
    normalized = _normalize_video_ratio(ratio)
    if normalized in {"adaptive", "auto"}:
        return "16:9"
    return normalized


def _mention_image_labels_in_prompt_order(prompt: str, labels: list[str]) -> list[str]:
    """按 prompt 中 @ 首次出现顺序收集已提及的图片标签。"""
    text = prompt or ""
    valid = [str(x or "").strip() for x in labels if str(x or "").strip()]
    if not text or not valid:
        return []
    found: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(text):
        if text[i] != "@":
            i += 1
            continue
        rest = text[i + 1 :]
        matched = ""
        for label in sorted(valid, key=len, reverse=True):
            if rest.startswith(label):
                matched = label
                break
        if matched and matched not in seen:
            seen.add(matched)
            found.append(matched)
            i += 1 + len(matched)
        else:
            i += 1
    return found


def _reorder_jumengai_image_refs(
    prompt: str,
    image_urls: list[str],
    image_labels: list[str] | None,
) -> tuple[list[str], list[str]]:
    """多参考图：prompt 中 @ 提及的图排在 images[0] 起，与 [Image N] 下标对齐。"""
    labels = [str(x or "").strip() for x in (image_labels or [])]
    if len(image_urls) <= 1 or len(labels) != len(image_urls):
        return image_urls, labels
    pairs = list(zip(image_urls, labels, strict=True))
    label_to_idx = {lab: idx for idx, (_, lab) in enumerate(pairs) if lab}
    order: list[int] = []
    seen: set[int] = set()
    for lab in _mention_image_labels_in_prompt_order(prompt, labels):
        idx = label_to_idx.get(lab)
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        order.append(idx)
    for idx in range(len(pairs)):
        if idx not in seen:
            order.append(idx)
    return [pairs[i][0] for i in order], [pairs[i][1] for i in order]


def _rewrite_jumengai_prompt_image_refs(prompt: str, image_labels: list[str]) -> str:
    """聚梦多参考生：prompt 用 [Image N] 对应 images 数组（docs.example.com/api/video）。"""
    text = (prompt or "").strip()
    labels = [str(x or "").strip() for x in image_labels if str(x or "").strip()]
    if not text or not labels:
        return text
    slot_by_label = {label: idx + 1 for idx, label in enumerate(labels)}
    out = text
    # 长标签优先，避免「图片」误匹配「图片 35」
    for label in sorted(labels, key=len, reverse=True):
        slot = slot_by_label[label]
        out = re.sub(re.escape(f"@{label}"), f"[Image {slot}]", out)
    return out


def _ensure_jumengai_multimodal_ref_prompt(
    prompt: str,
    *,
    image_count: int,
    video_count: int = 0,
) -> str:
    """未在 prompt 中显式引用全部参考时，前缀补充 [Image N]/[Video N] 约束。"""
    text = (prompt or "").strip()
    if image_count <= 0 and video_count <= 0:
        return text
    missing_img = [i for i in range(1, image_count + 1) if f"[Image {i}]" not in text]
    missing_vid = [i for i in range(1, video_count + 1) if f"[Video {i}]" not in text]
    if not missing_img and not missing_vid:
        return text
    parts: list[str] = []
    if missing_img:
        imgs = "、".join(f"[Image {i}]" for i in missing_img)
        parts.append(f"须严格参考{imgs}中的角色外貌、服装、场景与画面风格")
    if missing_vid:
        vids = "、".join(f"[Video {i}]" for i in missing_vid)
        parts.append(f"须参考{vids}中的动作与镜头语言")
    prefix = "。".join(parts) + "。"
    return f"{prefix}{text}"[:20000]


def _rewrite_jumengai_prompt_video_refs(prompt: str, video_labels: list[str]) -> str:
    """Seedance 多模态扩展：@视频标签 → [Video N]（与 images 规则对齐）。"""
    text = (prompt or "").strip()
    labels = [str(x or "").strip() for x in video_labels if str(x or "").strip()]
    if not text or not labels:
        return text
    out = text
    for label in sorted(labels, key=len, reverse=True):
        slot = labels.index(label) + 1
        out = re.sub(re.escape(f"@{label}"), f"[Video {slot}]", out)
    return out


def _is_jumengai_kling_or_h3_model(model: str) -> bool:
    """可灵 / MiniMax H3 等非 Seedance 视频模型（走自定义 V1 payload）。"""
    m = (model or "").lower()
    return "kling" in m or "minimax" in m or "hailuo" in m or "h3" in m


def _is_jumengai_seedance_model(model: str) -> bool:
    """聚梦网关 Seedance 系列（与华狐对齐：Ark body + V1 转换 + 聚梦 size 覆盖）。"""
    if _is_jumengai_kling_or_h3_model(model):
        return False
    m = (model or "").lower()
    return (
        "seedance" in m
        or "doubao-seedance" in m
        or "sd20" in m
        or "sd25" in m
        or "/discount" in m
    )


def _build_jumengai_seedance_payload(
    *,
    model: str,
    prompt: str,
    duration_sec: int,
    resolution: str,
    ratio: str,
    generate_audio: bool,
    image_urls: list[str],
    video_urls: list[str],
    audio_urls: list[str],
    video_mode: str = "r2v",
    real_person_mode: bool = False,
    image_labels: list[str] | None = None,
    video_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Seedance：对齐聚梦官方 /v1/video/generations + Ark content 透传。

    官方文档要求多参考图时 prompt 使用 [Image 1]/[Image 2]；画布 @标签须转换。
    size 仍传画幅比（聚梦 Seedance 校验）；ratio 同时写入 metadata.parameters。
    """
    dur = int(max(1, min(60, int(duration_sec or 5))))
    res_norm = normalize_seedance_resolution(resolution)
    res_display = _normalize_video_resolution(resolution)
    ratio_v = _normalize_video_ratio(ratio)
    vm = (video_mode or "r2v").strip().lower()
    img_labels = [str(x or "").strip() for x in (image_labels or [])]
    vid_labels = [str(x or "").strip() for x in (video_labels or [])]
    image_urls, img_labels = _reorder_jumengai_image_refs(prompt, image_urls, img_labels)
    prompt_text = _rewrite_jumengai_prompt_video_refs(
        _rewrite_jumengai_prompt_image_refs(prompt, img_labels),
        vid_labels,
    )
    prompt_text = _ensure_jumengai_multimodal_ref_prompt(
        prompt_text,
        image_count=len(image_urls),
        video_count=len(video_urls),
    )

    if vm == "i2v":
        first = (image_urls[0] if image_urls else "").strip()
        last = (image_urls[1] if len(image_urls) > 1 else None)
        body = build_seedance_i2v_body(
            model=model,
            prompt=prompt_text,
            first_frame_url=first,
            last_frame_url=last,
            resolution=res_norm,
            ratio=ratio_v,
            duration=dur,
            generate_audio=generate_audio,
            real_person_mode=real_person_mode,
        )
    elif vm == "t2v":
        body = build_seedance_t2v_body(
            model=model,
            prompt=prompt_text,
            resolution=res_norm,
            ratio=ratio_v,
            duration=dur,
            generate_audio=generate_audio,
            real_person_mode=real_person_mode,
        )
    else:
        body = build_seedance_multimodal_body(
            model=model,
            prompt=prompt_text,
            image_urls=image_urls,
            video_urls=video_urls,
            audio_urls=audio_urls,
            resolution=res_norm,
            ratio=ratio_v,
            duration=dur,
            generate_audio=generate_audio,
            real_person_mode=real_person_mode,
        )

    content = body.get("content") if isinstance(body.get("content"), list) else []
    aspect = _size_as_aspect_ratio(ratio_v)
    # 聚梦多分辨率模型从 metadata.resolution / metadata.parameters.resolution 读取清晰度
    metadata: dict[str, Any] = {
        "resolution": res_display,
        "ratio": ratio_v,
        "duration": dur,
        "generate_audio": bool(generate_audio),
        "parameters": {
            "ratio": ratio_v,
            "resolution": res_display,
            "watermark": False,
        },
    }
    if vm == "r2v" and image_urls:
        metadata["imageMode"] = "reference"
    if content:
        metadata["content"] = content

    payload: dict[str, Any] = {
        "model": (model or "").strip(),
        "prompt": prompt_text.strip()[:20000] or "generate video",
        "duration": dur,
        "seconds": str(dur),
        # 聚梦 Seedance 把 size 校验为画幅比；清晰度放 resolution
        "size": aspect,
        "resolution": res_display,
        "ratio": ratio_v,
        "aspect_ratio": ratio_v,
        "generate_audio": bool(generate_audio),
        "metadata": metadata,
    }
    if image_urls:
        payload["images"] = image_urls
        payload["image_urls"] = image_urls
        metadata["image_urls"] = image_urls
        # 多参考生：官方用 images[] + [Image N]；勿再传 image/input_reference 以免网关只取首图
        if len(image_urls) == 1 or vm == "i2v":
            payload["image"] = image_urls[0]
            payload["input_reference"] = image_urls[0]
            metadata["img_url"] = image_urls[0]
    if video_urls:
        payload["videos"] = video_urls
        payload["video"] = video_urls[0]
        payload["video_urls"] = video_urls
        metadata["referenceVideos"] = video_urls
    if audio_urls:
        payload["audios"] = audio_urls
        payload["audio"] = audio_urls[0]
        payload["audio_urls"] = audio_urls
        metadata["referenceAudio"] = audio_urls
    if content:
        payload["content"] = content
    if real_person_mode:
        payload["real_person"] = True
        payload["realPersonMode"] = True
        metadata["real_person"] = True
        metadata["realPersonMode"] = True
        if vm in ("r2v", "i2v"):
            payload["conversionSlots"] = ["all"]
    return payload


def _retryable_submit_error(status_code: int, message: str) -> bool:
    """错误路径或上游未给出 task_id 时，换下一条提交 URL 再试（避免打错 NewAPI 适配器）。"""
    if status_code == 404:
        return True
    msg = (message or "").lower()
    return (
        "task_id is empty" in msg
        or "invalid_response" in msg
        or "not found" in msg
        or "no route" in msg
        or "unknown path" in msg
    )


def _build_jumengai_video_payload(
    *,
    model: str,
    prompt: str,
    duration_sec: int,
    resolution: str,
    ratio: str,
    generate_audio: bool,
    image_urls: list[str],
    video_urls: list[str],
    audio_urls: list[str],
    mode: str | None = None,
    video_mode: str = "r2v",
    real_person_mode: bool = False,
) -> dict[str, Any]:
    """构造聚梦网关视频 JSON：NewAPI V1 字段 + Ark content，兼容可灵/Seedance。

    Seedance 参考生须在 content 项标注 role（reference_image 等）并设置 metadata.imageMode，
    否则网关只按纯文生视频处理，参考图/视频/音频不会送到上游（见 huahu.ark_seedance_body_to_v1_payload）。
    """
    dur = int(max(1, min(60, int(duration_sec or 5))))
    res = _normalize_video_resolution(resolution)
    ratio_v = _normalize_video_ratio(ratio)
    size = _size_as_aspect_ratio(ratio_v)
    text = (prompt or "").strip()[:20000] or "generate video"
    kling_mode = (mode or "").strip() or _kling_mode_from_resolution(res)
    vm = (video_mode or "r2v").strip().lower()
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    image_roles: list[str] = []
    for idx, u in enumerate(image_urls):
        if vm == "i2v":
            role = "first_frame" if idx == 0 else "last_frame"
        else:
            role = "reference_image"
        image_roles.append(role)
        content.append({"type": "image_url", "image_url": {"url": u}, "role": role})
    for u in video_urls:
        content.append({"type": "video_url", "video_url": {"url": u}, "role": "reference_video"})
    for u in audio_urls:
        content.append({"type": "audio_url", "audio_url": {"url": u}, "role": "reference_audio"})
    metadata: dict[str, Any] = {
        "resolution": res,
        "ratio": ratio_v,
        "duration": dur,
        "generate_audio": bool(generate_audio),
        "content": content,
    }
    payload: dict[str, Any] = {
        "model": (model or "").strip(),
        "prompt": text,
        "seconds": str(dur),
        "duration": dur,
        # 网关校验 size 为 16:9 / 9:16，清晰度只放 resolution
        "size": size,
        "resolution": res,
        "aspect_ratio": size,
        "ratio": ratio_v,
        "generate_audio": bool(generate_audio),
        "mode": kling_mode,
        "content": content,
        "metadata": metadata,
    }
    if real_person_mode:
        payload["real_person"] = True
        payload["realPersonMode"] = True
        metadata["real_person"] = True
        metadata["realPersonMode"] = True
    if image_urls:
        payload["images"] = image_urls
        payload["image"] = image_urls[0]
        payload["image_urls"] = image_urls
        metadata["img_url"] = image_urls[0]
        metadata["image_urls"] = image_urls
        # 多模态参考图（非首尾帧）须声明 reference，网关才会按参考生处理
        if vm == "r2v" and image_roles and all(r == "reference_image" for r in image_roles):
            metadata["imageMode"] = "reference"
    if video_urls:
        payload["videos"] = video_urls
        payload["video"] = video_urls[0]
        payload["video_urls"] = video_urls
        payload["videoUrl"] = video_urls[0]
        metadata["video_url"] = video_urls[0]
        metadata["video_urls"] = video_urls
        metadata["referenceVideos"] = video_urls
    if audio_urls:
        payload["audios"] = audio_urls
        payload["audio"] = audio_urls[0]
        payload["audio_urls"] = audio_urls
        metadata["referenceAudio"] = audio_urls
    return payload


def is_jumengai_authenticated_url(url: str, api_base: str | None = None) -> bool:
    """是否需要带聚梦网关 Bearer 才能下载（本网关 /content 或同 host 的 videos 路径）。"""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return False
    base = (api_base or DEFAULT_JUMENGAI_API_BASE).rstrip("/")
    if raw.startswith(base + "/videos/") or raw.startswith(base + "/video/"):
        return True
    host = (urlparse(raw).hostname or "").lower()
    return host in {"example.com", "api.example.com"} and (
        "/v1/videos/" in raw or "/v1/video/" in raw
    )


async def fetch_jumengai_video(url: str, *, max_bytes: int = 80 * 1024 * 1024) -> tuple[bytes, str]:
    """下载去字幕结果：网关 /content 带鉴权，公网 URL 直下。"""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        raise UpstreamError("去字幕结果 URL 无效", code="EMPTY_OUTPUT")
    api_key, api_base = _creds()
    headers: dict[str, str] = {}
    if is_jumengai_authenticated_url(raw, api_base):
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0), follow_redirects=True) as client:
        resp = await client.get(raw, headers=headers)
        if resp.status_code >= 400:
            raise UpstreamError(f"下载失败 HTTP {resp.status_code}", code="DOWNLOAD_FAILED")
        data = resp.content
        if len(data) > max_bytes:
            raise UpstreamError("媒体文件过大", code="DOWNLOAD_TOO_LARGE")
        ctype = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip().lower()
        if "application/json" in ctype:
            body = _response_json(resp)
            raise UpstreamError(_error_message(body, "聚梦网关未返回视频内容"), code="EMPTY_OUTPUT")
        if not data:
            raise UpstreamError("聚梦网关返回空视频", code="EMPTY_OUTPUT")
        return data, ctype or "video/mp4"


async def poll_jumengai_video_task(
    task_id: str,
    *,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 1800.0,
) -> str:
    """按 task_id 轮询直至完成，返回可下载 URL（公网或 /content）。禁止二次 submit。

    MiniMax Hailuo 等常超过 15 分钟，默认 30 分钟；超时抛 TIMEOUT（由上层停车，不退款）。
    """
    api_key, api_base = _creds()
    tid = quote(str(task_id).strip(), safe="")
    # 与提交路径顺序一致：NewAPI 任务通道优先，OpenAI /videos 兜底
    query_paths = [
        f"{api_base}/video/generations/{tid}",
        f"{api_base}/videos/generations/{tid}",
        f"{api_base}/videos/{tid}",
    ]
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            last_err = ""
            for qurl in query_paths:
                resp = await client.get(qurl, headers=headers)
                body = _response_json(resp)
                if resp.status_code == 404 or resp.status_code in (502, 503, 504):
                    if resp.status_code != 404:
                        last_err = f"HTTP {resp.status_code}"
                    continue
                if resp.status_code >= 400:
                    last_err = _error_message(body, resp.text or "聚梦网关查询失败")
                    continue
                state, url, err = map_jumengai_task_status(
                    body, task_id=str(task_id), api_base=api_base
                )
                if state == "failed":
                    raise UpstreamError(err or "聚梦网关视频任务失败", code="TASK_FAILED")
                if state == "succeeded":
                    note_upstream_billing_from_response(body, provider="jumengai", event="poll_succeeded")
                    return url or _content_url(api_base, task_id)
                break
            else:
                # 5xx/空响应继续轮询；其它 4xx 才中止
                if last_err and not last_err.startswith("HTTP "):
                    raise UpstreamError(last_err[:500], code="POLL_ERROR")
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("聚梦网关视频任务超时", code="TIMEOUT")


def map_jumengai_task_status(body: dict[str, Any], *, task_id: str = "", api_base: str = "") -> tuple[str, str, str]:
    """把一次查询 JSON 映射为 (state, url, error)。state: pending/running/succeeded/failed/unknown。"""
    status = _status_of(body)
    # 聚梦 NewAPI 偶发把 fail_reason 塞进 result_url；仅接受 http(s) 产物
    url = _extract_video_url(body)
    if status in ("failed", "failure", "error", "cancelled", "canceled"):
        err = _error_message(body, "聚梦网关视频任务失败")
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        for src in (data, nested, body):
            if not isinstance(src, dict):
                continue
            fr = str(src.get("fail_reason") or "").strip()
            if fr and not fr.startswith("http"):
                err = fr[:500]
                break
            err_obj = src.get("error")
            if isinstance(err_obj, dict):
                msg = str(err_obj.get("message") or "").strip()
                if msg:
                    err = msg[:500]
                    break
        return "failed", "", err
    if status in ("succeeded", "success", "completed", "done", "finished"):
        if not url and task_id and api_base:
            url = _content_url(api_base, task_id)
        return "succeeded", url, ""
    if url and status in ("",):
        return "succeeded", url, ""
    if status in ("queued", "queueing", "pending", "created", "not_start"):
        return "pending", url, ""
    if status in ("in_progress", "processing", "running", "generating"):
        return "running", url, ""
    if status:
        return "running", url, ""
    return "unknown", url, "无法解析上游状态"


async def inspect_jumengai_video_task(task_id: str) -> dict[str, Any]:
    """单次查询聚梦视频/图片任务（管理员 sync_upstream），不轮询、不二次 POST。"""
    api_key, api_base = _creds()
    tid = quote(str(task_id).strip(), safe="")
    query_paths = [
        f"{api_base}/video/generations/{tid}",
        f"{api_base}/videos/generations/{tid}",
        f"{api_base}/videos/{tid}",
        f"{api_base}/images/generations/{tid}",
        f"{api_base}/images/{tid}",
    ]
    headers = {"Authorization": f"Bearer {api_key}"}
    last_body: dict[str, Any] = {}
    last_err = "查询失败"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        for qurl in query_paths:
            resp = await client.get(qurl, headers=headers)
            body = _response_json(resp)
            last_body = body
            if resp.status_code == 404:
                continue
            if resp.status_code >= 400:
                last_err = _error_message(body, resp.text or last_err)
                continue
            img_urls = _extract_image_urls(body)
            if img_urls:
                return {"state": "succeeded", "url": img_urls[0], "error": "", "raw": body}
            state, url, err = map_jumengai_task_status(body, task_id=str(task_id), api_base=api_base)
            return {"state": state, "url": url, "error": err, "raw": body}
    return {"state": "unknown", "url": "", "error": last_err, "raw": last_body}


async def poll_jumengai_image_task(
    task_id: str,
    *,
    poll_interval_s: float = 4.0,
    poll_max_s: float = 600.0,
) -> list[str]:
    """图片异步任务：按 task_id 轮询，解析 url / b64_json。禁止二次 POST。"""
    api_key, api_base = _creds()
    tid = quote(str(task_id).strip(), safe="")
    query_paths = [
        f"{api_base}/images/generations/{tid}",
        f"{api_base}/images/{tid}",
        f"{api_base}/video/generations/{tid}",
        f"{api_base}/videos/generations/{tid}",
    ]
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            last_err = ""
            for qurl in query_paths:
                resp = await client.get(qurl, headers=headers)
                body = _response_json(resp)
                if resp.status_code == 404 or resp.status_code in (502, 503, 504):
                    if resp.status_code != 404:
                        last_err = f"HTTP {resp.status_code}"
                    continue
                if resp.status_code >= 400:
                    last_err = _error_message(body, resp.text or "聚梦网关图片查询失败")
                    continue
                urls = _extract_image_urls(body)
                status = _status_of(body)
                if status in ("failed", "error", "cancelled", "canceled"):
                    raise UpstreamError(
                        _error_message(body, "聚梦网关图片任务失败"),
                        code="TASK_FAILED",
                    )
                if urls:
                    note_upstream_billing_from_response(body, provider="jumengai", event="image_poll_succeeded")
                    return urls
                if status in ("succeeded", "success", "completed", "done", "finished"):
                    raise UpstreamError(
                        "聚梦网关图片任务完成但 url 与 b64_json 均为空",
                        code="EMPTY_OUTPUT",
                    )
                break
            else:
                if last_err and not last_err.startswith("HTTP "):
                    raise UpstreamError(last_err[:500], code="POLL_ERROR")
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("聚梦网关图片任务超时", code="TIMEOUT")


async def jumengai_subtitle_erase(
    *,
    model: str,
    video_url: str,
    prompt: str = "",
    seconds: int | str = 5,
    size: str = "720P",
    erase_type: str = "subtitle",
    encode_mode: str = "size",
    erase_ratio_location: Any = None,
) -> str:
    """提交聚梦网关火山字幕擦除（精准版）并轮询，返回结果 URL。"""
    api_key, api_base = _creds()
    payload = build_subtitle_erase_payload(
        model=model,
        video_url=video_url,
        prompt=prompt,
        seconds=seconds,
        size=size,
        erase_type=erase_type,
        encode_mode=encode_mode,
        erase_ratio_location=erase_ratio_location,
    )
    create_url = f"{api_base}/videos"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    note_upstream(
        provider="jumengai",
        event="submit",
        detail={"kind": "subtitle_erase", "model": payload.get("model")},
    )
    logger.info("[upstream] POST %s kind=jumengai-subtitle-erase model=%s", create_url, payload.get("model"))
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        resp = await client.post(create_url, headers=headers, json=payload)
        body = _response_json(resp)
        if resp.status_code >= 400:
            _raise_submit_http_error(resp, body, fallback="聚梦网关去字幕提交失败")

    immediate = _extract_video_url(body)
    task_id = _extract_task_id(body)
    status = _status_of(body)
    if immediate and not task_id and status not in ("failed", "error"):
        note_upstream_billing_from_response(body, provider="jumengai", event="submit_sync_succeeded")
        return immediate
    if not task_id:
        if immediate:
            return immediate
        raise UpstreamError(
            _error_message(body, "聚梦网关未返回视频 URL 或 task_id"),
            code="MISSING_TASK_ID",
        )
    note_upstream(provider="jumengai", provider_task_id=task_id, event="submit")
    await maybe_flush_upstream_trace()
    return await poll_jumengai_video_task(task_id)


def _b64_to_data_url(raw: str) -> str:
    """OpenAI b64_json → data URL，供 download_bytes 解码。"""
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("data:image/"):
        return text
    compact = "".join(text.split())
    sample = b""
    try:
        sample = base64.b64decode(compact[:64] + "====", validate=False)
    except Exception:
        sample = b""
    mime = "image/png"
    if sample.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif sample.startswith(b"RIFF"):
        mime = "image/webp"
    return f"data:{mime};base64,{compact}"


def _image_ref_from_item(item: dict[str, Any]) -> str:
    """OpenAI 标准：优先非空 url；url 为空则解码 b64_json；两者都空才无图。"""
    url = str(item.get("url") or item.get("image_url") or item.get("imageUrl") or "").strip()
    if url.startswith("http") or url.startswith("data:image/"):
        return url
    b64 = str(item.get("b64_json") or item.get("b64Json") or item.get("b64") or "").strip()
    if b64:
        return _b64_to_data_url(b64)
    return ""


def _extract_image_urls(body: dict[str, Any]) -> list[str]:
    """解析 images/generations 响应：data[] 的 url 或 b64_json。"""
    urls: list[str] = []
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and (item.startswith("http") or item.startswith("data:image/")):
                urls.append(item)
            elif isinstance(item, dict):
                ref = _image_ref_from_item(item)
                if ref:
                    urls.append(ref)
    elif isinstance(data, dict):
        ref = _image_ref_from_item(data)
        if ref:
            urls.append(ref)
    for key in ("url", "image_url", "imageUrl"):
        u = str(body.get(key) or "").strip()
        if (u.startswith("http") or u.startswith("data:image/")) and u not in urls:
            urls.append(u)
    top_b64 = str(body.get("b64_json") or "").strip()
    if top_b64 and not urls:
        urls.append(_b64_to_data_url(top_b64))
    return urls


async def jumengai_image_generate(
    *,
    model: str,
    prompt: str,
    reference_urls: list[str] | None = None,
    size: str = "2K",
    quality: str | None = None,
    n: int = 1,
) -> list[str]:
    """聚梦网关图片：POST /v1/images/generations（OpenAI 兼容）。"""
    api_key, api_base = _creds()
    count = max(1, min(int(n or 1), 4))
    payload: dict[str, Any] = {
        "model": (model or "").strip(),
        "prompt": (prompt or "").strip()[:20000],
        "n": count,
        "response_format": "url",
    }
    if size:
        payload["size"] = size
        payload["resolution"] = size
    if quality:
        payload["quality"] = quality
    refs = [u for u in (reference_urls or []) if u and str(u).strip()]
    if refs:
        payload["image"] = refs[0] if len(refs) == 1 else refs
        payload["images"] = refs
        payload["image_urls"] = refs

    note_upstream(provider="jumengai", event="submit", detail={"kind": "image", "model": model})
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_error = "聚梦网关图片提交失败"
    body: dict[str, Any] = {}
    # 优先 OpenAI 标准路径，404 时回退 /images
    for path in ("/images/generations", "/images"):
        url = f"{api_base}{path}"
        logger.info("[upstream] POST %s kind=jumengai-image model=%s", url, model)
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
            resp = await client.post(url, headers=headers, json=payload)
            body = _response_json(resp)
            if resp.status_code == 404 and path == "/images/generations":
                continue
            if resp.status_code >= 400:
                last_error = _error_message(body, resp.text or last_error)
                if _retryable_submit_error(resp.status_code, last_error) and path != "/images":
                    continue
                _raise_submit_http_error(resp, body, fallback=last_error)
            break
    else:
        raise UpstreamError(last_error, code="SUBMIT_ERROR")
    urls = _extract_image_urls(body)
    if urls:
        note_upstream_billing_from_response(body, provider="jumengai", event="image_succeeded")
        return urls
    task_id = _extract_task_id(body)
    if task_id:
        note_upstream(provider="jumengai", provider_task_id=task_id, event="submit")
        await maybe_flush_upstream_trace()
        return await poll_jumengai_image_task(task_id)
    raise UpstreamError("聚梦网关未返回图片（url 与 b64_json 均为空）", code="EMPTY_OUTPUT")


def _ref_url_kind(url: str) -> str:
    """追溯参考媒体 URL 类型：上游能否服务端拉取（presign vs cdn）。"""
    u = (url or "").lower()
    if u.startswith("data:"):
        return "base64"
    if "aliyuncs.com" in u or "ossaccesskeyid=" in u:
        return "presign"
    if "cdn.example.com" in u:
        return "cdn"
    return "external"


async def jumengai_video_generate(
    *,
    model: str,
    prompt: str,
    duration_sec: int = 5,
    resolution: str = "720p",
    ratio: str = "16:9",
    generate_audio: bool = True,
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
    mode: str | None = None,
    video_mode: str = "r2v",
    real_person_mode: bool = False,
    image_labels: list[str] | None = None,
    video_labels: list[str] | None = None,
) -> str:
    """聚梦网关视频：优先 NewAPI ``/video/generations``，再兼容 ``/videos``。

    可灵/Seedance 走 NewAPI 任务通道；打错成 OpenAI ``POST /videos`` 时上游常回
    ``task_id is empty``，须换路径而不是把空 id 当成功。
    """
    api_key, api_base = _creds()
    imgs = [u for u in (image_urls or []) if u and str(u).strip()][:9]
    vids = [u for u in (video_urls or []) if u and str(u).strip()][:3]
    auds = [u for u in (audio_urls or []) if u and str(u).strip()][:3]
    if _is_jumengai_seedance_model(model):
        payload = _build_jumengai_seedance_payload(
            model=model,
            prompt=prompt,
            duration_sec=duration_sec,
            resolution=resolution,
            ratio=ratio,
            generate_audio=generate_audio,
            image_urls=imgs,
            video_urls=vids,
            audio_urls=auds,
            video_mode=video_mode,
            real_person_mode=real_person_mode,
            image_labels=image_labels,
            video_labels=video_labels,
        )
    else:
        payload = _build_jumengai_video_payload(
            model=model,
            prompt=prompt,
            duration_sec=duration_sec,
            resolution=resolution,
            ratio=ratio,
            generate_audio=generate_audio,
            image_urls=imgs,
            video_urls=vids,
            audio_urls=auds,
            mode=mode,
            video_mode=video_mode,
            real_person_mode=real_person_mode,
        )
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    content = payload.get("content") if isinstance(payload.get("content"), list) else []
    has_subject_person = any(
        isinstance(c, dict) and c.get("subject_type") == "person" for c in content
    )
    note_upstream(
        provider="jumengai",
        event="submit",
        detail={
            "kind": "video",
            "model": model,
            "refImages": len(imgs),
            "refVideos": len(vids),
            "refAudios": len(auds),
            "videoMode": video_mode,
            "imageMode": meta.get("imageMode"),
            "conversionSlots": payload.get("conversionSlots"),
            "subjectTypePerson": has_subject_person,
            "seedancePath": _is_jumengai_seedance_model(model),
            "promptHasImageToken": "[Image " in str(payload.get("prompt") or ""),
            "promptRefImages": len(imgs),
            "refUrlKind": _ref_url_kind(imgs[0]) if imgs else "",
            "refVideoUrlKind": _ref_url_kind(vids[0]) if vids else "",
            "multiImageRefs": len(imgs) > 1,
        },
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # 上游已对接官方 POST /v1/video/generations（docs.example.com/api/video）；Seedance 只走该路径
    if _is_jumengai_seedance_model(model):
        submit_paths = ("/video/generations",)
    else:
        submit_paths = ("/video/generations", "/videos/generations", "/videos")
    last_error = "聚梦网关视频提交失败"
    body: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        for path in submit_paths:
            create_url = f"{api_base}{path}"
            logger.info("[upstream] POST %s kind=jumengai-video model=%s", create_url, model)
            resp = await client.post(create_url, headers=headers, json=payload)
            body = _response_json(resp)
            msg = _error_message(body, resp.text or last_error)
            if resp.status_code >= 400:
                last_error = msg
                if _retryable_submit_error(resp.status_code, last_error) and path != submit_paths[-1]:
                    logger.warning(
                        "[upstream] jumengai video submit %s failed (%s), try next path",
                        path,
                        last_error[:120],
                    )
                    continue
                _raise_submit_http_error(resp, body, fallback=last_error)
            # 200 但无 task_id/URL：多半打错适配器，换路径
            if not _extract_task_id(body) and not _extract_video_url(body):
                last_error = msg if msg and msg != last_error else "task_id is empty"
                if path != submit_paths[-1]:
                    logger.warning(
                        "[upstream] jumengai video submit %s empty task_id, try next path",
                        path,
                    )
                    continue
                raise UpstreamError(last_error, code="MISSING_TASK_ID")
            break
        else:
            raise UpstreamError(last_error, code="SUBMIT_ERROR")

    immediate = _extract_video_url(body)
    task_id = _extract_task_id(body)
    status = _status_of(body)
    if immediate and not task_id and status not in ("failed", "error"):
        note_upstream_billing_from_response(body, provider="jumengai", event="submit_sync_succeeded")
        return immediate
    if not task_id:
        if immediate:
            return immediate
        raise UpstreamError(
            _error_message(body, last_error if last_error else "聚梦网关未返回视频 URL 或 task_id"),
            code="MISSING_TASK_ID",
        )
    note_upstream(provider="jumengai", provider_task_id=task_id, event="submit")
    await maybe_flush_upstream_trace()
    return await poll_jumengai_video_task(task_id)


__all__ = [
    "DEFAULT_JUMENGAI_API_BASE",
    "DEFAULT_SUBTITLE_ERASE_MODEL",
    "build_subtitle_erase_payload",
    "fetch_jumengai_video",
    "inspect_jumengai_video_task",
    "is_jumengai_authenticated_url",
    "jumengai_image_generate",
    "jumengai_subtitle_erase",
    "jumengai_video_generate",
    "map_jumengai_task_status",
    "poll_jumengai_image_task",
    "poll_jumengai_video_task",
]
