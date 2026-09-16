"""Agent 操控画布时的默认生图 / 生视频模型。

与节点模型选择器中用户指定项对齐：
- 生图：全能图片 Pro 低价版
- 生视频：Seedance 2.0 多模态（doubao-seedance-2-0-260128）
"""

from __future__ import annotations

from .agent_generation_options import sanitize_agent_node_params

# 文生图：编排镜头 / 提示词出图
AGENT_DEFAULT_IMAGE_T2I = ""

# 图生图：已有图参考时的编辑
AGENT_DEFAULT_IMAGE_I2I = ""

# 多模态参考生视频
AGENT_DEFAULT_VIDEO_R2V = ""

# BGM / 音效占位默认模型（开源副本已清空，请自行配置）
AGENT_DEFAULT_MUSIC_MODEL = ""


def agent_audio_node_params(extra: dict | None = None) -> dict:
    """音频节点 params：写入默认音乐模型；不复用图片/视频的 generationOptions 归一逻辑。"""
    out: dict = {"model": AGENT_DEFAULT_MUSIC_MODEL}
    if extra:
        out.update(extra)
    if not str(out.get("model") or "").strip():
        out["model"] = AGENT_DEFAULT_MUSIC_MODEL
    return out


def agent_image_node_params(extra: dict | None = None) -> dict:
    """图片节点 params：写入默认文生图模型；归一 generationOptions。"""
    out: dict = {"model": AGENT_DEFAULT_IMAGE_T2I}
    if extra:
        cleaned, _notes = sanitize_agent_node_params(extra)
        out.update(cleaned)
    # 默认模型不被 extra 空 model 覆盖掉
    if not str(out.get("model") or "").strip():
        out["model"] = AGENT_DEFAULT_IMAGE_T2I
    return out


def agent_video_node_params(extra: dict | None = None) -> dict:
    """视频节点 params：写入默认多模态 Seedance；归一 generationOptions（含时长）。"""
    out: dict = {"model": AGENT_DEFAULT_VIDEO_R2V}
    if extra:
        cleaned, _notes = sanitize_agent_node_params(extra)
        out.update(cleaned)
    if not str(out.get("model") or "").strip():
        out["model"] = AGENT_DEFAULT_VIDEO_R2V
    # 按最终模型钳制时长（extra 里的 model 可能覆盖默认 Seedance）
    mid = str(out.get("model") or "").strip()
    go = out.get("generationOptions")
    if mid and isinstance(go, dict):
        from .agent_generation_options import clamp_generation_options_for_model

        clamped, _notes = clamp_generation_options_for_model(mid, dict(go))
        if clamped:
            out["generationOptions"] = clamped
    return out


def preferred_models_from_snapshot(
    snapshot: dict | None,
) -> tuple[str | None, str | None]:
    """从画布快照读取用户画布操控偏好的生图/生视频模型 id。"""
    if not isinstance(snapshot, dict):
        return None, None
    img = str(snapshot.get("preferredImageModel") or "").strip() or None
    vid = str(snapshot.get("preferredVideoModel") or "").strip() or None
    return img, vid


def agent_image_node_params_for_snapshot(
    snapshot: dict | None, extra: dict | None = None
) -> dict:
    """生图模型以画布操控选择器为准（覆盖 LLM / extra 里的 model）。"""
    pref_img, _ = preferred_models_from_snapshot(snapshot)
    base = dict(extra or {})
    if pref_img:
        # 用户在面板选定的模型优先于编排模型自选
        base["model"] = pref_img
    return agent_image_node_params(base or None)


def agent_video_node_params_for_snapshot(
    snapshot: dict | None, extra: dict | None = None
) -> dict:
    """生视频模型以画布操控选择器为准（覆盖 LLM / extra 里的 model）。"""
    _, pref_vid = preferred_models_from_snapshot(snapshot)
    base = dict(extra or {})
    if pref_vid:
        base["model"] = pref_vid
    return agent_video_node_params(base or None)
