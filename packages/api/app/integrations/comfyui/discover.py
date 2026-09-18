"""从用户自己的 ComfyUI 实例发现本机模型文件。

不绑定任何厂商或固定路径：只读取该实例 ``/models/{folder}`` 与 ``/object_info``
里实际列出的文件名，由调用方决定导入哪些、归类为图片还是视频。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .client import ComfyUIClient

logger = logging.getLogger(__name__)

# 常见 Comfy 模型目录（用户 extra_model_paths 里有的才会返回非空）
_MODEL_FOLDERS = (
    "checkpoints",
    "diffusion_models",
    "unet",
    "clip",
    "text_encoders",
    "vae",
)

# 文件名/目录里出现这些词时默认归为视频；用户导入前仍可改分类
_VIDEO_HINTS = (
    "video",
    "i2v",
    "t2v",
    "r2v",
    "fl2va",
    "ref2va",
    "svd",
    "cogvideo",
    "wan2",
    "wan3",
    "ltx",
    "hunyuan",
    "mochi",
    "animate",
    "hailuo",
    "minimax",
)

_COMBO_KEYS = (
    "ckpt_name",
    "unet_name",
    "model_name",
    "checkpoint",
    "diffusion_model",
    "ckpt",
)


def guess_media_category(filename: str, folder: str = "") -> str:
    """根据文件名与目录猜 image/video，仅作默认值。"""
    blob = f"{folder}/{filename}".lower().replace("\\", "/")
    if any(h in blob for h in _VIDEO_HINTS):
        return "video"
    if "checkpoint" in folder.lower():
        return "image"
    return "image"


def slug_catalog_name(prefix: str, filename: str, *, max_len: int = 80) -> str:
    """把任意文件名收成目录 name：小写字母开头，仅字母数字下划线。"""
    stem = re.sub(r"\.(safetensors|ckpt|pt|pth|sft|gguf)$", "", filename, flags=re.I)
    stem = re.sub(r"[^a-z0-9]+", "_", stem.lower())
    stem = re.sub(r"_+", "_", stem).strip("_") or "model"
    if stem[0].isdigit():
        stem = "m" + stem
    raw = f"{prefix}_{stem}"
    return raw[:max_len].rstrip("_") or f"{prefix}_model"


def _combo_values(raw: Any) -> list[str]:
    """解析 object_info 里 combo 控件的候选列表。"""
    values: list[str] = []
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, (list, tuple)):
            values = [str(x) for x in first if x is not None and str(x).strip()]
        elif isinstance(first, str):
            values = [str(x) for x in raw if str(x).strip()]
    elif isinstance(raw, tuple) and raw:
        inner = raw[0]
        if isinstance(inner, (list, tuple)):
            values = [str(x) for x in inner if x is not None and str(x).strip()]
    return [v for v in values if v and v not in ("None", "none", "")]


def _folder_from_combo_key(key: str) -> str:
    k = (key or "").lower()
    if "unet" in k or "diffusion" in k:
        return "diffusion_models"
    if "ckpt" in k or "checkpoint" in k:
        return "checkpoints"
    if "clip" in k or "text_encoder" in k:
        return "text_encoders"
    if "vae" in k:
        return "vae"
    return "checkpoints"


async def discover_comfyui_models(base_url: str) -> list[dict[str, Any]]:
    """探测用户 ComfyUI 上的权重文件，返回去重后的发现列表。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("请填写 ComfyUI 地址，例如 http://127.0.0.1:8188")

    client = ComfyUIClient(base_url=url)
    found: dict[tuple[str, str], dict[str, Any]] = {}

    try:
        for folder in _MODEL_FOLDERS:
            try:
                names = await client.list_models(folder)
            except Exception as exc:
                logger.debug("ComfyUI /models/%s 不可用: %s", folder, exc)
                continue
            for name in names:
                key = (folder, name)
                if key in found:
                    continue
                found[key] = {
                    "filename": name,
                    "folder": folder,
                    "category": guess_media_category(name, folder),
                    "displayName": name,
                    "source": "models_folder",
                }

        try:
            info = await client.get_object_info()
        except Exception as exc:
            logger.warning("ComfyUI /object_info 失败: %s", exc)
            info = {}

        if isinstance(info, dict):
            for class_type, meta in info.items():
                if not isinstance(meta, dict):
                    continue
                required = ((meta.get("input") or {}).get("required") or {}) if isinstance(meta.get("input"), dict) else {}
                if not isinstance(required, dict):
                    continue
                for field, spec in required.items():
                    fl = str(field or "").lower()
                    if not any(k in fl for k in _COMBO_KEYS):
                        continue
                    folder = _folder_from_combo_key(fl)
                    for name in _combo_values(spec):
                        key = (folder, name)
                        if key in found:
                            continue
                        found[key] = {
                            "filename": name,
                            "folder": folder,
                            "category": guess_media_category(name, folder),
                            "displayName": name,
                            "source": f"object_info:{class_type}",
                        }
    finally:
        await client.close()

    items = list(found.values())
    items.sort(key=lambda x: (x.get("category") or "", x.get("folder") or "", x.get("filename") or ""))
    return items
