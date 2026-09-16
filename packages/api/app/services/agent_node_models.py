"""Agent 操控：节点生图/生视频/文本模型目录与口语别名解析。

供说明书注入与 updateNodeParams 时把「即梦 / Seedance」等说法落到真实 model id。
"""

from __future__ import annotations

from typing import Any

from ..core.model_registry import CANVAS_MODEL_SPECS
from .agent_default_models import (
    AGENT_DEFAULT_IMAGE_I2I,
    AGENT_DEFAULT_IMAGE_T2I,
    AGENT_DEFAULT_VIDEO_R2V,
)

# 常用可切换模型（优先展示给 LLM；完整目录仍可按 category 从 registry 取）
_AGENT_SWITCHABLE: tuple[tuple[str, str, str], ...] = (
    # (model_id, category hint for node type, display)
    (AGENT_DEFAULT_IMAGE_T2I, "image", "全能图片 Pro 文生图"),
    (AGENT_DEFAULT_IMAGE_I2I, "image", "全能图片 Pro 图生图"),
    ("nano_g_t2i", "image", "全能图片 G 文生图"),
    ("nano_g_i2i", "image", "全能图片 G 图生图"),
    ("doubao_image", "image", "即梦 图片"),
    ("jimeng50_image", "image", "即梦 5.0 Lite"),
    ("jimeng50_pro_image", "image", "即梦 5.0 Pro"),
    ("qwen_image_30_pro", "image", "千问图像 3.0 Pro"),
    ("qwen_image_30", "image", "千问图像 3.0"),
    (AGENT_DEFAULT_VIDEO_R2V, "video", "Seedance 2.0 多模态"),
    ("huahu_seedance_20_fast_r2v", "video", "Seedance 2.0 Fast"),
    ("huahu_seedance_20_mini_r2v", "video", "Seedance 2.0 Mini"),
    ("vidu_q3_turbo_r2v", "video", "Vidu q3-turbo"),
    ("vidu_q2_pro_r2v", "video", "Vidu q2-pro"),
    ("wan30_r2v", "video", "万相 3.0 全能参考"),
    ("rh_seedance_25_r2v", "video", "Seedance 2.5 多模态 Token"),
    ("rh_seedance_25_t2v", "video", "Seedance 2.5 文生视频 Token"),
    ("rh_seedance_25_i2v", "video", "Seedance 2.5 图生视频 Token"),
    ("wan27_r2v", "video", "万相 2.7 参考生"),
    ("doubao_pro", "text", "豆包 Pro"),
    ("doubao_seed_evolving", "text", "豆包 Seed Evolving"),
    ("deepseek_v4_flash", "text", "DeepSeek V4 Flash"),
)

# 口语/简称 → model id（小写匹配）
_ALIAS_TO_MODEL: dict[str, str] = {
    "全能图片": AGENT_DEFAULT_IMAGE_T2I,
    "全能图片pro": AGENT_DEFAULT_IMAGE_T2I,
    "全能pro": AGENT_DEFAULT_IMAGE_T2I,
    "nano pro": AGENT_DEFAULT_IMAGE_T2I,
    "nano_pro": AGENT_DEFAULT_IMAGE_T2I,
    "文生图": AGENT_DEFAULT_IMAGE_T2I,
    "图生图": AGENT_DEFAULT_IMAGE_I2I,
    "即梦": "doubao_image",
    "即梦图片": "doubao_image",
    "豆包图片": "doubao_image",
    "jimeng": "doubao_image",
    "seedance": AGENT_DEFAULT_VIDEO_R2V,
    "seedance 2.0": AGENT_DEFAULT_VIDEO_R2V,
    "seedance2.0": AGENT_DEFAULT_VIDEO_R2V,
    "seedance多模态": AGENT_DEFAULT_VIDEO_R2V,
    "多模态视频": AGENT_DEFAULT_VIDEO_R2V,
    "vidu": "vidu_q3_turbo_r2v",
    "万相": "wan30_r2v",
    "万相3": "wan30_r2v",
    "万相3.0": "wan30_r2v",
    "wan3": "wan30_r2v",
    "wan30": "wan30_r2v",
    "seedance 2.5": "rh_seedance_25_r2v",
    "seedance2.5": "rh_seedance_25_r2v",
    "seedance25": "rh_seedance_25_r2v",
    "万相2.7": "wan27_r2v",
    "即梦5": "jimeng50_image",
    "即梦5.0": "jimeng50_image",
    "即梦pro": "jimeng50_pro_image",
    "千问": "qwen_image_30_pro",
    "千问图像": "qwen_image_30_pro",
    "千问3.0": "qwen_image_30",
    "千问pro": "qwen_image_30_pro",
    "qwen image": "qwen_image_30_pro",
    "qwen-image": "qwen_image_30_pro",
    "seedance fast": "huahu_seedance_20_fast_r2v",
    "seedance mini": "huahu_seedance_20_mini_r2v",
    "豆包": "doubao_pro",
    "豆包pro": "doubao_pro",
    "seed evolving": "doubao_seed_evolving",
    "seedevolving": "doubao_seed_evolving",
    "豆包evolving": "doubao_seed_evolving",
    "豆包 seed evolving": "doubao_seed_evolving",
    "deepseek": "deepseek_v4_flash",
}


def _spec_by_name(name: str) -> Any | None:
    for s in CANVAS_MODEL_SPECS:
        if getattr(s, "name", None) == name:
            return s
    return None


def list_agent_switchable_models() -> list[dict[str, str]]:
    """下拉/说明书用：id + 类别 + 展示名。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for mid, cat, label in _AGENT_SWITCHABLE:
        if mid in seen:
            continue
        seen.add(mid)
        spec = _spec_by_name(mid)
        out.append(
            {
                "id": mid,
                "category": cat,
                "label": str(getattr(spec, "display_name", None) or label),
            }
        )
    return out


def agent_switchable_models_prompt_block() -> str:
    """注入 LLM：可切换模型清单。"""
    lines = ["可选节点模型（updateNodeParams.params.model 必须用 id）："]
    for item in list_agent_switchable_models():
        lines.append(f"- [{item['category']}] `{item['id']}` = {item['label']}")
    lines.append(
        "用户说「换成即梦/Seedance/全能图片」等时：先对照画布快照选中目标节点（按 label/type），"
        "再 updateNodeParams 写入对应 id；可一次改多个同类节点。"
    )
    return "\n".join(lines)


def resolve_model_mention(text: str, *, prefer_category: str | None = None) -> str | None:
    """从用户话术解析模型 id；优先精确 id，再别名，再 display_name 包含。"""
    raw = (text or "").strip()
    if not raw:
        return None
    low = raw.lower().replace(" ", "")

    # 直接是已知 id
    for item in list_agent_switchable_models():
        if item["id"] == raw or item["id"].lower() == low:
            if prefer_category and item["category"] != prefer_category:
                continue
            return item["id"]

    # 别名表（长键优先）
    aliases = sorted(_ALIAS_TO_MODEL.items(), key=lambda x: len(x[0]), reverse=True)
    for alias, mid in aliases:
        a = alias.lower().replace(" ", "")
        if a and a in low:
            if prefer_category:
                spec_cat = next(
                    (x["category"] for x in list_agent_switchable_models() if x["id"] == mid),
                    None,
                )
                if spec_cat and spec_cat != prefer_category:
                    continue
            return mid

    # display_name 子串
    for item in list_agent_switchable_models():
        label = item["label"].lower().replace(" ", "")
        if label and label in low:
            if prefer_category and item["category"] != prefer_category:
                continue
            return item["id"]
        # registry 全名
        spec = _spec_by_name(item["id"])
        dn = str(getattr(spec, "display_name", "") or "").lower().replace(" ", "")
        if dn and dn in low:
            if prefer_category and item["category"] != prefer_category:
                continue
            return item["id"]

    return None


def category_for_node_type(node_type: str | None) -> str | None:
    t = (node_type or "").strip()
    if t == "image_input":
        return "image"
    if t == "video_input":
        return "video"
    if t in ("text_input", "prompt"):
        return "text"
    return None
