"""助手用模型能力卡：输入类型、参考上限、时长。供 list_models 按需返回。

上限与前端 imageModelReferenceGuide / videoModelReferenceGuide 对齐；
时长来自 model_registry.video_duration_limits。未单列的模型按 capabilities 推断。
"""

from __future__ import annotations

from typing import Any

from ..core.model_registry import (
    CANVAS_MODEL_SPECS,
    CanvasModelSpec,
    video_duration_limits,
)

_CAP_ZH: dict[str, str] = {
    "chat_completion": "文本对话",
    "text_to_image": "文生图",
    "image_to_image": "图生图",
    "reference_to_video": "参考生视频",
    "image_to_video": "图生视频",
    "first_frame_to_video": "首帧生视频",
    "start_end_to_video": "首尾帧生视频",
    "lip_sync": "对口型",
    "voice_clone": "声音复刻",
    "voice_design": "音色设计",
    "text_to_speech": "语音合成",
    "text_to_music": "文生音乐",
    "audio_separate": "人声分离",
    "subtitle_erase": "去字幕",
}

# 显式上限：(最多图, 最多视频, 最多音频)；None 表示该类型不作为参考输入
# 与前端参考说明保持一致
_EXPLICIT_LIMITS: dict[str, dict[str, Any]] = {
    "nano_g_i2i": {"maxImages": 10, "usage": "图生图：最多 10 张参考图，每张≤10MB。"},
    "qwen_image_30": {"maxImages": 3, "usage": "图生图/编辑：最多 3 张参考图，每张≤10MB；参考图按张计费。"},
    "qwen_image_30_pro": {"maxImages": 3, "usage": "图生图/编辑：最多 3 张参考图，每张≤10MB；参考图按张计费。"},
    "vidu_q3_turbo_r2v": {"maxImages": 7, "usage": "参考生：最多 7 张参考图。"},
    "vidu_q2_pro_r2v": {"maxImages": 7, "usage": "参考生：最多 7 张参考图。"},
    "wan30_r2v": {
        "allowImages": True,
        "allowVideos": True,
        "allowAudios": True,
        "allowDocument": True,
        "usage": "全能参考：可传参考图/视频/音频，或连文档/链接；提示词可用「图1」「视频1」。",
    },
    "wan30_i2v": {"maxImages": 2, "usage": "首尾帧：第 1 张为首帧、第 2 张为尾帧（可省）。"},
    "wan30_t2v": {"allowDocument": True, "usage": "文生视频：纯文本；可连文档/链接作素材。"},
    "wan27_r2v": {"maxImages": 5, "usage": "参考生：最多 5 张参考图。"},
    "happyhorse_r2v": {"maxImages": 9, "usage": "参考生：最多 9 张参考图。"},
    "pixverse_r2v": {"maxImages": 7, "usage": "参考生：最多 7 张参考图。"},
    "kling_r2v": {"maxImages": 7, "maxVideos": 1, "usage": "参考生：最多 7 张图，或 1 段参考视频。"},
    "happyhorse_i2v": {"maxImages": 1, "usage": "仅首帧：1 张起始图，不支持尾帧。"},
    "ltx_23_i2v": {"maxImages": 1, "usage": "仅首帧：1 张起始图。"},
    "rh_seedance_25_r2v": {
        "maxImages": 30,
        "maxVideos": 10,
        "maxAudios": 10,
        "usage": "多模态：最多 30 张图 + 10 段视频 + 10 段音频。",
    },
    "rh_minimax_hailuo_h3_r2v": {
        "maxImages": 9,
        "maxVideos": 1,
        "usage": "海螺 H3 多模态：参考图最多 9 张（前 5 张免费）、可接参考视频；时长 5–15 秒。",
    },
}


def _family_limits(name: str) -> dict[str, Any] | None:
    """同一产品线共用上限，避免每个档位重复写。"""
    if name in _EXPLICIT_LIMITS:
        return dict(_EXPLICIT_LIMITS[name])
    if name.endswith("_r2v") and (
        name.startswith("rh_seedance_20") or name.startswith("nodyhub_sd20")
    ):
        return {
            "maxImages": 9,
            "maxVideos": 3,
            "maxAudios": 3,
            "usage": "多模态：最多 9 张图 + 3 段视频 + 3 段音频。",
        }
    if name.startswith("huahu_seedance_20") and name.endswith("_r2v"):
        return {
            "maxImages": 9,
            "maxVideos": 3,
            "maxAudios": 3,
            "usage": "华狐多模态：参考图/视频；含真人肖像的参考可能被隐私审核拦截，真人原片请改用 RH Seedance。",
        }
    if name.startswith("huahu_seedance_20") and name.endswith("_i2v"):
        return {"maxImages": 2, "usage": "华狐图生：首帧/首尾帧。"}
    if name.startswith("huahu_seedance_20") and name.endswith("_t2v"):
        return {"usage": "华狐文生视频：仅文本。"}
    if name.startswith("rh_seedance_20") and name.endswith("_i2v"):
        return {"maxImages": 2, "usage": "图生视频：首帧必填、尾帧可选。"}
    if name.startswith("rh_seedance_20") and name.endswith("_t2v"):
        return {"usage": "文生视频：仅文本。"}
    if name.startswith("rh_seedance_25") and name.endswith("_i2v"):
        return {"maxImages": 2, "usage": "Seedance 2.5 图生：首帧必填、尾帧可选。"}
    if name.startswith("rh_seedance_25") and name.endswith("_t2v"):
        return {"usage": "Seedance 2.5 文生：仅文本。"}
    if name.startswith("nodyhub_sd20"):
        return {
            "maxImages": 9,
            "maxVideos": 3,
            "maxAudios": 3,
            "usage": "NodyHub SD2.0 多模态：最多 9 张图 + 3 段视频 + 3 段音频。",
        }
    if name.startswith("jumengai_sd"):
        return {
            "maxImages": 9,
            "maxVideos": 3,
            "maxAudios": 3,
            "usage": "聚梦 Seedance 多模态：最多 9 张图 + 3 段视频 + 3 段音频。",
        }
    if name == "jumengai_kling_v3":
        return {"maxImages": 7, "maxVideos": 1, "usage": "聚梦可灵：最多 7 张图，或 1 段参考视频。"}
    if name == "jumengai_minimax_h3":
        return {
            "maxImages": 9,
            "maxVideos": 1,
            "usage": "聚梦 MiniMax H3：参考图最多 9 张（前 5 张免费）、可接参考视频；时长 5–15 秒。",
        }
    if name == "jumengai_x_image_video_15":
        return {"maxImages": 1, "usage": "聚梦图生视频：1 张首帧图。"}
    if name.startswith("jumengai_g_image") or name.startswith("jumengai_gemini"):
        return {"maxImages": 10, "usage": "文生图或图生图：无参考走文生；有参考图则图生。"}
    return None


def _infer_from_capabilities(spec: CanvasModelSpec) -> dict[str, Any]:
    caps = set(spec.capabilities or ())
    mode = spec.video_mode
    out: dict[str, Any] = {}
    if spec.category == "text" or "chat_completion" in caps:
        out["usage"] = "文本：写入节点 content/prompt，不接媒体参考。"
        return out
    if "text_to_speech" in caps:
        out["usage"] = "语音合成：文本 + 可选音色；可连音频作参考音色。"
        out["maxAudios"] = 1
        return out
    if "voice_clone" in caps:
        out["usage"] = "声音复刻：需要参考人声音频。"
        out["maxAudios"] = 1
        return out
    if "text_to_music" in caps:
        out["usage"] = "文生音乐：提示词；可连参考音频。"
        return out
    if "audio_separate" in caps:
        out["usage"] = "人声分离：需要音频或视频音轨。"
        out["maxAudios"] = 1
        out["maxVideos"] = 1
        return out
    if "subtitle_erase" in caps:
        out["usage"] = "去字幕：需要源视频；可选框选擦除区域。"
        out["maxVideos"] = 1
        return out
    if mode == "t2v" or (spec.category == "video" and spec.name.endswith("_t2v")):
        out["usage"] = "文生视频：仅需文本提示词。"
        return out
    if mode == "lip_sync" or "lip_sync" in caps:
        out["maxVideos"] = 1
        out["maxAudios"] = 1
        out["usage"] = "对口型：需要参考视频与音频。"
        return out
    if (
        "start_end_to_video" in caps
        or mode == "i2v"
        or spec.name.endswith("_i2v")
    ):
        out["maxImages"] = 2
        out["usage"] = "首尾帧：第 1 张为首帧、第 2 张为尾帧（可省）。"
        return out
    if "first_frame_to_video" in caps:
        out["maxImages"] = 1
        out["usage"] = "图生视频：1 张首帧图。"
        return out
    if "reference_to_video" in caps or mode == "r2v":
        out["usage"] = "参考生视频：通过 @ 或上游连线传入参考图（上限因模型而异，未标明时先 1–4 张）。"
        return out
    if "image_to_image" in caps and "text_to_image" in caps:
        out["usage"] = "文生图或图生图：无参考走文生；有参考图则图生。"
        return out
    if "image_to_image" in caps:
        out["usage"] = "图生图：需要参考图（@ 或上游连线）。"
        return out
    if "text_to_image" in caps:
        out["usage"] = "文生图：仅需提示词；不要硬塞参考图。"
        return out
    out["usage"] = spec.description or "按节点类型与提示词生成。"
    return out


def resolve_input_caps(spec: CanvasModelSpec) -> dict[str, Any]:
    """合并显式上限、产品线默认与 capabilities 推断。"""
    inferred = _infer_from_capabilities(spec)
    family = _family_limits(spec.name) or {}
    caps = {**inferred, **family}
    dur = video_duration_limits(spec.name)
    extra = spec.parameters_extra if isinstance(spec.parameters_extra, dict) else {}
    if dur:
        caps["durationMin"], caps["durationMax"] = dur
    else:
        vmin = extra.get("videoMinDuration")
        vmax = extra.get("videoMaxDuration")
        if vmin is not None and vmax is not None:
            try:
                caps["durationMin"], caps["durationMax"] = int(vmin), int(vmax)
            except (TypeError, ValueError):
                pass
    return caps


def _input_bits(caps: dict[str, Any], spec: CanvasModelSpec) -> list[str]:
    bits = ["提示词"]
    if caps.get("maxImages"):
        bits.append(f"图≤{caps['maxImages']}")
    elif caps.get("allowImages"):
        bits.append("参考图")
    elif spec.category == "image" and "image_to_image" in (spec.capabilities or ()):
        if "text_to_image" not in (spec.capabilities or ()):
            bits.append("参考图")
    if caps.get("maxVideos"):
        bits.append(f"视频≤{caps['maxVideos']}")
    elif caps.get("allowVideos"):
        bits.append("参考视频")
    if caps.get("maxAudios"):
        bits.append(f"音频≤{caps['maxAudios']}")
    elif caps.get("allowAudios"):
        bits.append("参考音频")
    if caps.get("allowDocument"):
        bits.append("文档/链接")
    if spec.category == "video" and caps.get("durationMin") is not None:
        bits.append(f"时长{caps['durationMin']}-{caps['durationMax']}s")
    return bits


def format_model_line(spec: CanvasModelSpec) -> str:
    """目录一行：id + 输入上限。"""
    caps = resolve_input_caps(spec)
    bits = _input_bits(caps, spec)
    usage = str(caps.get("usage") or "").strip()
    extra = f" | {usage}" if usage else ""
    return (
        f"- [{spec.category}] `{spec.name}` {spec.display_name}"
        f" | 输入:{'+'.join(bits)}{extra}"
    )


def _preset_option_summary(spec: CanvasModelSpec) -> str:
    """紧凑列出时长/画幅/清晰度等可选项，不含风格套话。"""
    from .generation_presets import default_presets_for_model

    presets = default_presets_for_model(spec.name, spec.provider, spec.category)
    if not isinstance(presets, dict):
        return ""
    want = ("duration", "resolution", "ratio", "size", "clarity", "realPerson")
    parts: list[str] = []
    for group in presets.get("groups") or []:
        if not isinstance(group, dict):
            continue
        gid = str(group.get("id") or "")
        if gid not in want:
            continue
        items = [
            str(it.get("label") or it.get("id") or "")
            for it in (group.get("items") or [])
            if isinstance(it, dict) and it.get("enabled", True)
        ]
        items = [x for x in items if x][:10]
        if items:
            parts.append(f"{group.get('label') or gid}={'/'.join(items)}")
    return "；".join(parts)


def format_model_detail(spec: CanvasModelSpec) -> str:
    """单个模型完整能力卡。"""
    caps = resolve_input_caps(spec)
    cap_zh = "、".join(_CAP_ZH.get(c, c) for c in (spec.capabilities or ())) or "（未标注）"
    lines = [
        f"【模型】`{spec.name}` {spec.display_name}",
        f"类别={spec.category} 节点可写 params.model；能力={cap_zh}",
    ]
    if spec.description:
        lines.append(f"简介：{spec.description}")
    bits = _input_bits(caps, spec)
    lines.append("支持输入：" + "、".join(bits))
    if caps.get("maxImages") is not None:
        lines.append(f"最多参考图：{caps['maxImages']} 张（@ 或上游连到 ref_in）")
    if caps.get("maxVideos") is not None:
        lines.append(f"最多参考视频：{caps['maxVideos']} 段")
    if caps.get("maxAudios") is not None:
        lines.append(f"最多参考音频：{caps['maxAudios']} 段")
    if caps.get("durationMin") is not None:
        lines.append(f"生成时长：{caps['durationMin']}–{caps['durationMax']} 秒")
    if caps.get("usage"):
        lines.append("用法：" + str(caps["usage"]))
    opts = _preset_option_summary(spec)
    if opts:
        lines.append("选项：" + opts)
    lines.append("连参考：上游媒体接到本节点 ref_in，不要编造 assetId。")
    return "\n".join(lines)


def iter_live_user_models() -> list[CanvasModelSpec]:
    """对用户/助手可见的 live 模型（不含官方副通道、工具类）。"""
    from .model_catalog_runtime import (
        iter_runtime_model_names,
        resolve_model_spec,
        uses_db_catalog_runtime,
    )

    names = iter_runtime_model_names() if uses_db_catalog_runtime() else []
    specs: list[CanvasModelSpec] = []
    if names:
        for n in names:
            s = resolve_model_spec(n)
            if s is not None:
                specs.append(s)
    else:
        specs = list(CANVAS_MODEL_SPECS)
    out: list[CanvasModelSpec] = []
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            continue
        if spec.name.endswith("_official"):
            continue
        if spec.implementation != "live":
            continue
        if spec.category not in ("text", "image", "video", "audio"):
            continue
        seen.add(spec.name)
        out.append(spec)
    out.sort(key=lambda s: (s.category, int(s.sort_order or 0), s.name))
    return out


def find_model_spec(model_id: str) -> CanvasModelSpec | None:
    mid = (model_id or "").strip()
    if not mid:
        return None
    from .model_catalog_runtime import resolve_model_spec

    hit = resolve_model_spec(mid)
    if hit is not None:
        return hit
    low = mid.lower()
    for spec in iter_live_user_models():
        if spec.name.lower() == low:
            return spec
        if (spec.display_name or "").lower().replace(" ", "") == low.replace(" ", ""):
            return spec
    return None


def collect_snapshot_model_ids(snapshot: dict[str, Any] | None) -> list[str]:
    """画布已有 model + 用户首选，去重保序（供本轮注入能力卡）。"""
    if not isinstance(snapshot, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        mid = str(raw or "").strip()
        if not mid or mid in seen:
            return
        seen.add(mid)
        out.append(mid)

    _add(snapshot.get("preferredImageModel"))
    _add(snapshot.get("preferredVideoModel"))
    nodes = snapshot.get("nodes")
    if isinstance(nodes, list):
        for n in nodes:
            if isinstance(n, dict):
                _add(n.get("model"))
    return out


def model_caps_block_for_snapshot(snapshot: dict[str, Any] | None) -> str:
    """本轮注入：只写画布上已出现/首选的模型，避免每轮塞全表。"""
    ids = collect_snapshot_model_ids(snapshot)
    if not ids:
        return (
            "（画布节点尚无 model、也无首选模型。"
            "选模型、写 generationOptions 或连参考前必须 list_models。）"
        )
    lines = [
        "以下为本轮画布已有/首选模型的输入上限。连参考、换模型前对照本块；"
        "未列出的模型必须先 list_models（可传 modelId）。不要猜张数或时长。",
    ]
    # 少量用完整能力卡；太多则一行摘要，避免撑爆上下文
    use_detail = len(ids) <= 8
    for mid in ids[:20]:
        spec = find_model_spec(mid)
        if spec is None:
            lines.append(f"- `{mid}` 目录未收录，请 list_models 核对真实 id。")
            continue
        if use_detail:
            lines.append(format_model_detail(spec))
            lines.append("")
        else:
            lines.append(format_model_line(spec))
    if len(ids) > 20:
        lines.append(f"（还有 {len(ids) - 20} 个模型未列出，请按需 list_models。）")
    return "\n".join(lines).strip()


def list_models_for_agent(*, category: str = "", model_id: str = "") -> str:
    """list_models 工具正文：全表一行能力，或单个详情。"""
    if model_id.strip():
        spec = find_model_spec(model_id)
        if spec is None:
            return f"没有模型 `{model_id.strip()}`。请先 list_models 看真实 id。"
        return format_model_detail(spec)
    items = iter_live_user_models()
    cat = category.strip().lower()
    if cat:
        items = [x for x in items if x.category == cat]
    if not items:
        return "（无可用模型）"
    lines = [
        "可选模型（params.model 必须用 id）。输入=提示词/参考上限/时长；写节点或连参考前对照本表。",
        "查单个详情请再调 list_models 并传 modelId。",
    ]
    for spec in items:
        lines.append(format_model_line(spec))
    return "\n".join(lines)
