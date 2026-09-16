"""RunningHub 上游集成：Nano 图片、LTX / Seedance 2.0 / MiniMax-H3 视频、音乐、分离音频与字幕擦除。

全能图片 Pro/G（nano_pro / nano_g，含官方稳定副通道）、LTX 图生视频、悠船文生图 v8.1 / Niji 7
已迁移或接入「RunningHub 海外版」（AI 站 www.runninghub.ai），通过 use_ltx/use_overseas 走海外密钥与地址；
CN 站（www.runninghub.cn）保留 MJ 文生图 v7、Seedance 2.0（sparkvideo）/ 2.5 Token、MiniMax-H3 多模态参考生、
分离音频 Vocals/Other、火山字幕擦除（精细化版）等。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from .credentials import runninghub_creds
from .errors import UpstreamError
from .trace_context import maybe_flush_upstream_trace, note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)

# 逻辑路由名 → RunningHub OpenAPI 路径
# 主线路：低价渠道版（无 -official）；官方稳定版路由名带 _official，作副通道托底
ROUTES: dict[str, str] = {
    "nano_pro_i2i": "/openapi/v2/rhart-image-n-pro/edit",
    "nano_pro_t2i": "/openapi/v2/rhart-image-n-pro/text-to-image",
    "nano_g_i2i": "/openapi/v2/rhart-image-g-2/image-to-image",
    "nano_g_t2i": "/openapi/v2/rhart-image-g-2/text-to-image",
    "nano_pro_i2i_official": "/openapi/v2/rhart-image-n-pro-official/edit",
    "nano_pro_t2i_official": "/openapi/v2/rhart-image-n-pro-official/text-to-image",
    "nano_g_i2i_official": "/openapi/v2/rhart-image-g-2-official/image-to-image",
    "nano_g_t2i_official": "/openapi/v2/rhart-image-g-2-official/text-to-image",
    "mj_t2i_v7": "/openapi/v2/youchuan/text-to-image-v7",
    # 悠船文生图（海外站 www.runninghub.ai 与 CN 同路径）
    # v8.1: https://www.runninghub.ai/runninghub-api-doc-en/api-454760430
    # niji7: https://www.runninghub.ai/runninghub-api-doc-en/api-448184528
    "mj_t2i_v81": "/openapi/v2/youchuan/text-to-image-v81",
    "mj_t2i_niji7": "/openapi/v2/youchuan/text-to-image-niji7",
    "ltx_i2v": "/openapi/v2/rhart-video/ltx-2.3/image-to-video",
    # Seedance 2.0（CN 站 sparkvideo 渠道：Standard / Fast / Mini × 多模态 / 图生 / 文生）
    "rh_seedance_20_r2v": "/openapi/v2/rhart-video/sparkvideo-2.0/multimodal-video",
    "rh_seedance_20_fast_r2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/multimodal-video",
    "rh_seedance_20_mini_r2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/multimodal-video",
    "rh_seedance_20_i2v": "/openapi/v2/rhart-video/sparkvideo-2.0/image-to-video",
    "rh_seedance_20_fast_i2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/image-to-video",
    "rh_seedance_20_mini_i2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/image-to-video",
    "rh_seedance_20_t2v": "/openapi/v2/rhart-video/sparkvideo-2.0/text-to-video",
    "rh_seedance_20_fast_t2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/text-to-video",
    "rh_seedance_20_mini_t2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/text-to-video",
    # Seedance 2.5 Token（满血）：多模态 / 文生 / 图生
    "rh_seedance_25_r2v": "/openapi/v2/bytedance/seedance-2.5-token/multimodal-video",
    "rh_seedance_25_t2v": "/openapi/v2/bytedance/seedance-2.5-token/text-to-video",
    "rh_seedance_25_i2v": "/openapi/v2/bytedance/seedance-2.5-token/image-to-video",
    # MiniMax-H3 多模态参考生视频（CN 站）
    "rh_minimax_hailuo_h3_r2v": "/openapi/v2/minimax/hailuo-h3/multimodal-to-video",
    # 海外版音乐
    "minimax_music_26": "/openapi/v2/minimax/music-2.6",
    "suno_custom_v55": "/openapi/v2/rhart-audio/suno-v5.5/custom",
    # CN 站：分离音频 Vocals / Other（配置：ai_read/不参考/音频模型配置文件）
    #   Vocals → POST .../rhart-audio/extract-vocal
    #   Other  → POST .../rhart-audio/extract-background（消除人声 / 提伴奏）
    #   必填 videoUrl；可选 webhookUrl（本系统用 /openapi/v2/query 轮询，不传 webhook）
    # 旧字段 3##file / 2##file 会 errorCode=1007，禁止再用
    "rh_audio_extract_vocals": "/openapi/v2/rhart-audio/extract-vocal",
    "rh_audio_extract_other": "/openapi/v2/rhart-audio/extract-background",
    # CN 站：火山字幕擦除-视频字幕擦除（精细化版）
    # 配置：ai_read/不参考/去字幕模型 → POST .../volc-subtitle-erase-pro/video
    # 必填 videoUrl；可选 eraseType / encodeMode / eraseRatioLocation（框选）
    "rh_volc_subtitle_erase_pro": "/openapi/v2/volc-subtitle-erase-pro/video",
}

# 与 RunningHub 文档一致：分离音频请求体字段名
_AUDIO_EXTRACT_FILE_FIELD = "videoUrl"
_AUDIO_RESULT_EXTS = frozenset({"mp3", "wav", "m4a", "aac", "flac", "ogg", "audio"})


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _extract_task_id(body: dict[str, Any]) -> str:
    for key in ("taskId", "task_id"):
        if body.get(key):
            return str(body[key]).strip()
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("taskId", "task_id"):
            if data.get(key):
                return str(data[key]).strip()
    return ""


def _stringify_failed_reason(raw: Any, *, depth: int = 0) -> str:
    """解析 RunningHub failedReason（ComfyUI / 多模态常见嵌套），尽量抽出可读原因。"""
    if raw is None or raw == "" or raw == {} or raw == []:
        return ""
    if depth > 4:
        return ""
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in ("{}", "[]", "null"):
            return ""
        # 偶发整段 JSON 字符串
        if text.startswith("{") or text.startswith("["):
            try:
                return _stringify_failed_reason(json.loads(text), depth=depth + 1)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return text[:400]
    if isinstance(raw, list):
        parts = [_stringify_failed_reason(item, depth=depth + 1) for item in raw[:8]]
        return "; ".join(p for p in parts if p)[:400]
    if not isinstance(raw, dict):
        return str(raw).strip()[:400]

    for key in (
        "exception_message",
        "exceptionMessage",
        "errorMessage",
        "message",
        "msg",
        "reason",
        "detail",
        "details",
    ):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:400]
        nested = _stringify_failed_reason(val, depth=depth + 1)
        if nested:
            return nested

    errors = raw.get("errors")
    if isinstance(errors, list) and errors:
        nested = _stringify_failed_reason(errors, depth=depth + 1)
        if nested:
            return nested

    # 节点 id → 错误对象
    parts: list[str] = []
    for key, val in raw.items():
        if key in ("traceback", "stack", "stackTrace", "promptTips"):
            continue
        nested = _stringify_failed_reason(val, depth=depth + 1)
        if nested:
            parts.append(nested if str(key).isdigit() else f"{key}: {nested}")
        if len(parts) >= 3:
            break
    return "; ".join(parts)[:400]


def _prompt_tips_error_detail(raw: Any) -> str:
    """从 promptTips（JSON 字符串）提取 node_errors / error。"""
    if isinstance(raw, dict):
        tips = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""
        tips = parsed if isinstance(parsed, dict) else {}
    else:
        return ""

    err = tips.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()[:400]
    nested_err = _stringify_failed_reason(err)
    if nested_err:
        return nested_err

    node_errors = tips.get("node_errors") or tips.get("nodeErrors")
    return _stringify_failed_reason(node_errors)


def _runninghub_error_detail(body: dict[str, Any], *, fallback: str) -> str:
    """从 RunningHub 响应提取可读错误（含 MiniMax-H3 的 failedReason / promptTips）。"""
    if not isinstance(body, dict):
        return (fallback or "RunningHub 错误")[:500]

    # 嵌套 data 内也可能有业务错误
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    sources: list[dict[str, Any]] = [body]
    if data:
        sources.append(data)

    detail = ""
    code = ""
    for src in sources:
        detail = (
            str(
                src.get("errorMessage")
                or src.get("message")
                or src.get("msg")
                or ""
            ).strip()
        )
        code = str(src.get("errorCode") or src.get("code") or "").strip()
        if detail:
            break
        failed = _stringify_failed_reason(src.get("failedReason") or src.get("failed_reason"))
        if failed:
            detail = failed
            break
        tips = _prompt_tips_error_detail(src.get("promptTips") or src.get("prompt_tips"))
        if tips:
            detail = tips
            break

    if not detail:
        detail = (fallback or "RunningHub 任务失败").strip()
    if code and code not in ("0", "200", "null", "None") and code not in detail:
        detail = f"{detail}（errorCode={code}）"
    # 便于区分 MiniMax-H3 / 其它 RH 任务：若原文过短则补前缀
    if detail in ("RunningHub 任务失败", "RunningHub 提交失败", "RunningHub 查询失败"):
        detail = f"{detail}（上游未返回具体原因，请检查参考素材格式/时长或稍后重试）"
    return detail[:500]


def _extract_result_url(body: dict[str, Any], *, prefer_audio: bool = False) -> str:
    """从 query SUCCESS 响应取产物 URL；分离音频优先 mp3/wav 等音频类型。"""
    status = str(body.get("status") or "").upper()
    if status != "SUCCESS":
        return ""
    results = body.get("results") or []
    if not isinstance(results, list) or not results:
        return ""

    def _url_of(item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        return str(item.get("url") or "").strip()

    if prefer_audio:
        for item in results:
            if not isinstance(item, dict):
                continue
            out_type = str(item.get("outputType") or item.get("output_type") or "").strip().lower()
            url = _url_of(item)
            if not url:
                continue
            if out_type in _AUDIO_RESULT_EXTS or any(
                url.lower().split("?", 1)[0].endswith(f".{ext}") for ext in _AUDIO_RESULT_EXTS
            ):
                return url
    first = results[0]
    return _url_of(first)


async def poll_runninghub_task(
    task_id: str,
    *,
    use_ltx: bool = False,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 600.0,
) -> str:
    """仅按 task_id 轮询 RunningHub 任务直至完成，返回产物 URL；不含任何 submit。"""
    creds = runninghub_creds(use_ltx=use_ltx)
    return await _poll_task(task_id, creds=creds, poll_interval_s=poll_interval_s, poll_max_s=poll_max_s)


async def _poll_task(
    task_id: str,
    *,
    creds,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 600.0,
    prefer_audio: bool = False,
) -> str:
    # 与配置文件一致：POST /openapi/v2/query + taskId
    query_url = f"{creds.api_base}/openapi/v2/query"
    payload = {"taskId": task_id}
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            resp = await client.post(query_url, headers=_headers(creds.api_key), content=json.dumps(payload))
            body = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                raise UpstreamError(
                    _runninghub_error_detail(body, fallback=resp.text or "RunningHub 查询失败"),
                    code="POLL_ERROR",
                )
            status = str(body.get("status") or "").upper()
            if status == "SUCCESS":
                note_upstream_billing_from_response(body, provider="runninghub", event="poll_succeeded")
                url = _extract_result_url(body, prefer_audio=prefer_audio)
                if not url:
                    raise UpstreamError("任务成功但未返回 URL", code="EMPTY_OUTPUT")
                if not url.startswith("http"):
                    url = f"{creds.api_base}/{url.lstrip('/')}"
                return url
            if status == "FAILED":
                # MiniMax-H3 等：errorMessage 常空，详情在 failedReason / promptTips
                raise UpstreamError(
                    _runninghub_error_detail(body, fallback="RunningHub 任务失败"),
                    code="TASK_FAILED",
                )
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("RunningHub 任务超时", code="TIMEOUT")


async def _submit(
    path: str,
    payload: dict[str, Any],
    *,
    use_ltx: bool = False,
    prefer_audio: bool = False,
    poll_max_s: float = 600.0,
) -> str:
    creds = runninghub_creds(use_ltx=use_ltx)
    if not creds.api_key:
        raise UpstreamError("未配置 RUNNINGHUB_API_KEY", code="NOT_CONFIGURED")
    url = f"{creds.api_base}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        logger.info("[upstream] POST %s kind=runninghub path=%s", url, path)
        resp = await client.post(url, headers=_headers(creds.api_key), content=json.dumps(payload, ensure_ascii=False))
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise UpstreamError(
                _runninghub_error_detail(body, fallback=resp.text or "RunningHub 提交失败"),
                code="SUBMIT_ERROR",
            )
        task_id = _extract_task_id(body)
        if not task_id:
            # HTTP 200 但业务失败（如 errorCode=1007 参数名错误）时 taskId 为空，透出上游原文
            raise UpstreamError(
                _runninghub_error_detail(body, fallback="未返回 taskId"),
                code="MISSING_TASK_ID",
            )
        # 提交成功后立即落库 task_id，保证崩溃/重试只续轮询，不二次提交
        note_upstream(provider="runninghub", provider_task_id=task_id, event="submit")
        await maybe_flush_upstream_trace()
        # 全新任务：提交后接着轮询；重试任务由上层直接调用 poll_runninghub_task
        return await _poll_task(
            task_id, creds=creds, prefer_audio=prefer_audio, poll_max_s=poll_max_s
        )


# 悠船 / Midjourney 文生图 OpenAPI 合法画幅
_YOUCHUAN_ASPECT_RATIOS = frozenset({"1:1", "4:3", "3:2", "16:9", "3:4", "2:3", "9:16"})


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _youchuan_t2i_payload(
    route: str,
    prompt: str,
    *,
    aspect_ratio: str | None,
    api_params: dict[str, Any] | None,
    image_urls: list[str] | None,
) -> dict[str, Any]:
    """组装悠船文生图 OpenAPI body（v7 / v8.1 / niji7）。"""
    params = api_params if isinstance(api_params, dict) else {}
    payload: dict[str, Any] = {"prompt": (prompt or "").strip()[:8192]}

    ratio = (aspect_ratio or params.get("ratio") or params.get("aspectRatio") or "").strip()
    if ratio in _YOUCHUAN_ASPECT_RATIOS:
        payload["aspectRatio"] = ratio

    # 通用可选项
    if "chaos" in params:
        payload["chaos"] = _clamp_int(params.get("chaos"), 0, 100, 0)
    if "stylize" in params:
        payload["stylize"] = _clamp_int(params.get("stylize"), 0, 1000, 0)
    if "raw" in params:
        payload["raw"] = bool(params.get("raw"))

    # 垫图（首张参考图）
    first_img = ""
    for u in image_urls or []:
        if u and str(u).strip():
            first_img = str(u).strip()
            break
    if first_img:
        payload["imageUrl"] = first_img
        iw_max = 2 if route == "mj_t2i_niji7" else 3
        payload["iw"] = _clamp_int(params.get("iw", 1), 0, iw_max, 1)

    sref = str(params.get("sref") or "").strip()
    if sref:
        payload["sref"] = sref
        payload["sw"] = _clamp_int(params.get("sw", 100), 0, 1000, 100)

    if route == "mj_t2i_v81":
        # v8.1：hd 为必填；quality 仅 1|4；sv 固定 6
        payload["hd"] = bool(params.get("hd", False))
        q = str(params.get("quality") or "1").strip()
        payload["quality"] = q if q in ("1", "4") else "1"
        payload["sv"] = 6
    elif route == "mj_t2i_niji7":
        if "weird" in params:
            payload["weird"] = _clamp_int(params.get("weird"), 0, 3000, 0)
        if "sv" in params:
            payload["sv"] = _clamp_int(params.get("sv"), 1, 4, 4)
        if "aspectRatio" not in payload:
            payload["aspectRatio"] = "1:1"
    elif route == "mj_t2i_v7":
        q = str(params.get("quality") or "").strip()
        if q in ("1", "2", "4"):
            payload["quality"] = q
        if "weird" in params:
            payload["weird"] = _clamp_int(params.get("weird"), 0, 3000, 0)

    return payload


async def runninghub_image_generate(
    *,
    route: str,
    prompt: str,
    image_urls: list[str] | None = None,
    resolution: str = "2k",
    aspect_ratio: str | None = None,
    quality: str | None = None,
    use_overseas: bool = False,
    api_params: dict[str, Any] | None = None,
) -> str:
    """按路由提交 RunningHub 图片生成任务并轮询，返回产物 URL。

    use_overseas=True 时走「RunningHub 海外版」（AI 站 www.runninghub.ai）密钥与地址；
    全能图片 Pro/G 与悠船 v8.1 / niji7 走海外版。
    """
    path = ROUTES.get(route)
    if not path:
        raise UpstreamError(f"未知 RunningHub 路由: {route}", code="INVALID_ROUTE")

    # 无参考图时图生图 edit 必失败：改走对应文生图路由（故事板/设定图纯文字出图）
    urls = [str(u).strip() for u in (image_urls or []) if str(u).strip()]
    if "_i2i" in route and not urls:
        t2i_route = route.replace("_i2i", "_t2i", 1)
        t2i_path = ROUTES.get(t2i_route)
        if t2i_path:
            logger.info("RunningHub i2i 无参考图，改走文生图 %s → %s", route, t2i_route)
            route = t2i_route
            path = t2i_path
        else:
            raise UpstreamError("图生图需要参考图", code="MISSING_SOURCE_IMAGE")

    # 悠船文生图：专用字段，禁止误传 Nano 的 resolution
    if route.startswith("mj_t2i_"):
        merged = dict(api_params or {})
        if quality and "quality" not in merged:
            merged["quality"] = quality
        payload = _youchuan_t2i_payload(
            route,
            prompt,
            aspect_ratio=aspect_ratio,
            api_params=merged,
            image_urls=image_urls,
        )
        return await _submit(path, payload, use_ltx=use_overseas)

    res = (resolution or "2k").lower()
    if res not in ("1k", "2k", "4k"):
        res = "2k"

    # 画质档位：全能图片 G/Pro 均可透传；standard 映射为上游 medium
    q = (quality or "high").lower()
    if q == "standard":
        q = "medium"
    if q not in ("low", "medium", "high"):
        q = "high"

    # 路由名可能带 _official 后缀（副通道），用片段判断文生图/图生图
    is_i2i = "_i2i" in route
    is_g = route.startswith("nano_g_")
    is_nano = route.startswith("nano_")

    if is_i2i:
        payload = {
            "imageUrls": urls[:10],
            "prompt": (prompt or "").strip()[:20000],
            "resolution": res,
        }
        if is_nano:
            payload["quality"] = q
        if aspect_ratio:
            payload["aspectRatio"] = aspect_ratio
        return await _submit(path, payload, use_ltx=use_overseas)

    payload = {"prompt": (prompt or "").strip()[:20000], "resolution": res}
    if aspect_ratio:
        payload["aspectRatio"] = aspect_ratio
    if is_nano:
        payload["quality"] = q
        if is_g and "_t2i" in route:
            payload["aspectRatio"] = aspect_ratio or "1:1"
    return await _submit(path, payload, use_ltx=use_overseas)


async def runninghub_ltx_i2v(
    *,
    prompt: str,
    first_frame_url: str,
    duration_sec: int = 5,
    resolution: str = "720",
    ratio: str = "16:9",
) -> str:
    """提交 RunningHub LTX-2.3 图生视频任务并轮询，返回视频 URL。"""
    res = str(resolution or "720").replace("p", "").replace("P", "")
    if res not in ("480", "720", "1080"):
        res = "720"
    ratio_api = "9:16" if (ratio or "").strip() == "9:16" else "16:9"
    payload = {
        "prompt": (prompt or "").strip()[:20000],
        "imageUrl": first_frame_url,
        "duration": int(max(3, min(10, duration_sec))),
        "resolution": res,
        "aspectRatio": ratio_api,
    }
    return await _submit(ROUTES["ltx_i2v"], payload, use_ltx=True)


def _seedance_resolution(resolution: str | None) -> str:
    """Seedance OpenAPI resolution：480p/720p/native1080p/native4k/1080p/2k/4k。"""
    raw = str(resolution or "720p").strip().lower()
    allowed = {
        "480p",
        "720p",
        "1080p",
        "native1080p",
        "native4k",
        "2k",
        "4k",
    }
    if raw in allowed:
        return raw
    # 兼容旧预设 id：480 / 720 / 1080
    aliases = {
        "480": "480p",
        "720": "720p",
        "1080": "1080p",
        "native1080": "native1080p",
        "4k原生": "native4k",
        "1080p原生": "native1080p",
    }
    if raw in aliases:
        return aliases[raw]
    stripped = raw.replace("p", "")
    if stripped in ("480", "720", "1080"):
        return f"{stripped}p"
    if stripped in ("2k", "4k"):
        return stripped
    return "720p"


def _seedance_duration(duration_sec: int, *, max_sec: int = 15) -> str:
    """Seedance 时长（秒），以字符串提交；2.0 默认 4–15，2.5 可到 30。"""
    hi = max(4, int(max_sec))
    return str(int(max(4, min(hi, duration_sec))))


def _seedance_ratio(ratio: str | None) -> str:
    """画幅：adaptive 或常见比例。"""
    r = (ratio or "adaptive").strip()
    allowed = {"adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
    return r if r in allowed else "adaptive"


def _is_seedance_25_route(route: str) -> bool:
    return str(route or "").startswith("rh_seedance_25")


async def runninghub_seedance_video(
    *,
    route: str,
    prompt: str,
    duration_sec: int = 5,
    resolution: str = "720",
    ratio: str = "adaptive",
    generate_audio: bool = True,
    real_person_mode: bool = False,
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
    first_frame_url: str | None = None,
    last_frame_url: str | None = None,
    bitrate_mode: str | None = None,
    web_search: bool | None = None,
    use_overseas: bool = False,
) -> str:
    """提交 RunningHub Seedance 2.0/2.5（多模态 / 图生 / 文生）并轮询，返回视频 URL。

    默认走 CN 站密钥与地址（www.runninghub.cn）；route 须为 ROUTES 中 rh_seedance_* 之一。
    2.0 走 sparkvideo 渠道；2.5 仍走 seedance-2.5-token。
    real_person_mode=True 时传 realPersonMode，允许参考素材含真人。
    2.5：时长 4–30s；多模态参考上限 30 图 / 10 视频 / 10 音频。
    """
    path = ROUTES.get(route)
    if not path:
        raise UpstreamError(f"未知 RunningHub Seedance 路由: {route}", code="INVALID_ROUTE")

    is_25 = _is_seedance_25_route(route)
    max_dur = 30 if is_25 else 15
    payload: dict[str, Any] = {
        "prompt": (prompt or "").strip()[:20480],
        "resolution": _seedance_resolution(resolution),
        "duration": _seedance_duration(duration_sec, max_sec=max_dur),
        "ratio": _seedance_ratio(ratio),
        "generateAudio": bool(generate_audio),
        # 真人模式：含真人参考图/视频时须为 true，否则上游 errorCode=1505
        "realPersonMode": bool(real_person_mode),
    }
    if is_25:
        # 2.5 支持画质档位；默认 standard
        bm = str(bitrate_mode or "standard").strip().lower()
        payload["bitrateMode"] = bm if bm in ("standard", "high") else "standard"
        payload["outputFormat"] = "mp4"
    if real_person_mode:
        # 将全部图/视频槽转为真人资产（与 OpenAPI 默认示例一致）
        payload["conversionSlots"] = ["all"]

    if route.endswith("_r2v"):
        img_cap, vid_cap, aud_cap = (30, 10, 10) if is_25 else (9, 3, 3)
        imgs = [u for u in (image_urls or []) if u and str(u).strip()][:img_cap]
        vids = [u for u in (video_urls or []) if u and str(u).strip()][:vid_cap]
        auds = [u for u in (audio_urls or []) if u and str(u).strip()][:aud_cap]
        if imgs:
            payload["imageUrls"] = imgs
        if vids:
            payload["videoUrls"] = vids
        if auds:
            payload["audioUrls"] = auds
    elif route.endswith("_i2v"):
        first = (first_frame_url or "").strip()
        if not first:
            raise UpstreamError("Seedance 图生视频需要首帧图片", code="INVALID_MEDIA")
        payload["firstFrameUrl"] = first
        last = (last_frame_url or "").strip()
        if last and last != first:
            payload["lastFrameUrl"] = last
        # 2.5 图生视频 ratio 仅支持 adaptive
        if is_25:
            payload["ratio"] = "adaptive"
    elif route.endswith("_t2v") and is_25 and web_search is not None:
        payload["webSearch"] = bool(web_search)
    # t2v：仅文本 + 公共参数

    return await _submit(path, payload, use_ltx=use_overseas)


def _hailuo_h3_resolution(resolution: str | None) -> str:
    """MiniMax-H3 分辨率：仅 2K / 768P。"""
    raw = str(resolution or "768P").strip().upper().replace(" ", "")
    aliases = {
        "768": "768P",
        "768P": "768P",
        "2K": "2K",
        "2": "2K",
    }
    return aliases.get(raw, "768P")


def _hailuo_h3_duration(duration_sec: int) -> str:
    """MiniMax-H3 时长 5–15 秒，以字符串提交。"""
    return str(int(max(5, min(15, duration_sec))))


def _hailuo_h3_ratio(ratio: str | None) -> str:
    """MiniMax-H3 画幅：adaptive 或常见比例。"""
    r = (ratio or "adaptive").strip()
    allowed = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
    return r if r in allowed else "adaptive"


async def runninghub_hailuo_h3_video(
    *,
    route: str = "rh_minimax_hailuo_h3_r2v",
    prompt: str,
    duration_sec: int = 5,
    resolution: str = "768P",
    ratio: str = "adaptive",
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
) -> str:
    """提交 RunningHub MiniMax-H3 多模态参考生视频并轮询，返回视频 URL。

    走 CN 站密钥；最多 9 图 / 3 视频 / 3 音频；分辨率 2K|768P，时长 5–15 秒。
    """
    path = ROUTES.get(route)
    if not path:
        raise UpstreamError(f"未知 RunningHub MiniMax-H3 路由: {route}", code="INVALID_ROUTE")

    imgs = [u for u in (image_urls or []) if u and str(u).strip()][:9]
    vids = [u for u in (video_urls or []) if u and str(u).strip()][:3]
    auds = [u for u in (audio_urls or []) if u and str(u).strip()][:3]
    if not imgs and not vids:
        raise UpstreamError("MiniMax-H3 需要至少一张参考图或参考视频", code="INVALID_MEDIA")

    payload: dict[str, Any] = {
        "prompt": (prompt or "").strip()[:20480],
        "resolution": _hailuo_h3_resolution(resolution),
        "duration": _hailuo_h3_duration(duration_sec),
        "ratio": _hailuo_h3_ratio(ratio),
        "imageUrls": imgs,
        "videoUrls": vids,
        "audioUrls": auds,
    }
    return await _submit(path, payload, use_ltx=False)


async def runninghub_music_generate(
    *,
    route: str,
    payload: dict[str, Any],
) -> str:
    """提交 RunningHub 海外版音乐任务并轮询，返回音频 URL。"""
    path = ROUTES.get(route)
    if not path:
        raise UpstreamError(f"未知 RunningHub 音乐路由: {route}", code="INVALID_ROUTE")
    return await _submit(path, payload, use_ltx=True)


async def runninghub_audio_extract(
    *,
    route: str,
    video_url: str,
) -> str:
    """提交 RunningHub CN 站分离音频（Vocals / Other）并轮询，返回音频 URL。

    配置：``POST /openapi/v2/rhart-audio/extract-vocal``（或 extract-background），
    请求体必填 ``videoUrl``（单视频，建议 ≤200MB）；完成后 ``/openapi/v2/query`` 取结果。
    使用 CN 站密钥（``use_ltx=False``）。
    """
    path = ROUTES.get(route)
    if not path:
        raise UpstreamError(f"未知 RunningHub 分离音频路由: {route}", code="INVALID_ROUTE")
    url = (video_url or "").strip()
    if not url:
        raise UpstreamError("分离音频需要源视频 URL（videoUrl）", code="INVALID_MEDIA")
    # 与配置文件一致：仅传 videoUrl（不传 webhookUrl，改由本系统轮询）
    payload: dict[str, Any] = {_AUDIO_EXTRACT_FILE_FIELD: url}
    return await _submit(path, payload, use_ltx=False, prefer_audio=True)


_SUBTITLE_ERASE_TYPES = frozenset({"subtitle", "text"})
_SUBTITLE_ENCODE_MODES = frozenset({"size", "quality"})


def _normalize_erase_ratio_location(raw: Any) -> list[dict[str, float]]:
    """将框选区域规范为 RunningHub eraseRatioLocation（0–1 相对坐标）。"""
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
        # 兼容画布 CropRect {x,y,w,h} 与上游 topLeft/bottomRight
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
        # 钳制到 0–1，并保证左上 < 右下
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


async def runninghub_subtitle_erase(
    *,
    route: str,
    video_url: str,
    erase_type: str = "subtitle",
    encode_mode: str = "size",
    erase_ratio_location: Any = None,
) -> str:
    """提交 RunningHub CN 站火山字幕擦除（精细化版）并轮询，返回视频 URL。

    配置：``POST /openapi/v2/volc-subtitle-erase-pro/video``，
    必填 ``videoUrl``；可选 ``eraseType`` / ``encodeMode`` / ``eraseRatioLocation``。
    使用 CN 站密钥（``use_ltx=False``）；不传 webhook，由本系统 ``/openapi/v2/query`` 轮询。
    """
    path = ROUTES.get(route)
    if not path:
        raise UpstreamError(f"未知 RunningHub 去字幕路由: {route}", code="INVALID_ROUTE")
    url = (video_url or "").strip()
    if not url:
        raise UpstreamError("去字幕需要源视频 URL（videoUrl）", code="INVALID_MEDIA")

    et = (erase_type or "subtitle").strip().lower()
    if et not in _SUBTITLE_ERASE_TYPES:
        et = "subtitle"
    em = (encode_mode or "size").strip().lower()
    if em not in _SUBTITLE_ENCODE_MODES:
        em = "size"

    payload: dict[str, Any] = {
        "videoUrl": url,
        "eraseType": et,
        "encodeMode": em,
    }
    boxes = _normalize_erase_ratio_location(erase_ratio_location)
    if boxes:
        payload["eraseRatioLocation"] = boxes

    # 去字幕可能较慢，轮询上限 15 分钟
    return await _submit(path, payload, use_ltx=False, prefer_audio=False, poll_max_s=900.0)
