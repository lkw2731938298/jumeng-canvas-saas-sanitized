"""通用 ComfyUI prompt：默认 SD 文生图图，或套用用户自己导出的 API 工作流。

视频 / Flux / 各家自定义节点差异极大，不以某一型号为标准。
用户可在模型上粘贴 ComfyUI「另存为 API 格式」JSON，并用占位符：
``{{PROMPT}}`` ``{{NEGATIVE}}`` ``{{MODEL}}`` ``{{IMAGE}}`` ``{{WIDTH}}`` ``{{HEIGHT}}``
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any


_PLACEHOLDER_RE = re.compile(r"\{\{\s*(PROMPT|NEGATIVE|MODEL|CKPT|IMAGE|WIDTH|HEIGHT)\s*\}\}")


def default_sd_txt2img_prompt(
    *,
    ckpt_name: str,
    prompt: str,
    negative: str = "",
    width: int = 1024,
    height: int = 1024,
    seed: int = 42,
    steps: int = 20,
    cfg: float = 7.0,
) -> dict[str, Any]:
    """仅适用于 CheckpointLoaderSimple 能加载的 SD/SDXL 类权重。"""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative or "", "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "jumeng_canvas", "images": ["6", 0]},
        },
    }


def _replace_in_value(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        def _sub(m: re.Match[str]) -> str:
            key = m.group(1).upper()
            if key == "CKPT":
                key = "MODEL"
            return mapping.get(key, m.group(0))

        if value.strip() in ("{{PROMPT}}", "{{NEGATIVE}}", "{{MODEL}}", "{{CKPT}}", "{{IMAGE}}"):
            token = value.strip().strip("{}").strip().upper()
            if token == "CKPT":
                token = "MODEL"
            return mapping.get(token, value)
        return _PLACEHOLDER_RE.sub(_sub, value)
    if isinstance(value, list):
        return [_replace_in_value(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _replace_in_value(v, mapping) for k, v in value.items()}
    return value


def apply_user_workflow(
    workflow: dict[str, Any] | str,
    *,
    prompt: str,
    negative: str = "",
    model_filename: str = "",
    image_name: str = "",
    width: int = 1024,
    height: int = 1024,
) -> dict[str, Any]:
    """把用户 API 工作流里的占位符替换成本次生成参数。"""
    if isinstance(workflow, str):
        text = workflow.strip()
        if not text:
            raise ValueError("工作流 JSON 为空")
        parsed = json.loads(text)
    else:
        parsed = workflow
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("工作流须为 ComfyUI API 格式对象（节点 id → class_type/inputs）")
    # 有的导出带 prompt 包一层
    if "prompt" in parsed and isinstance(parsed.get("prompt"), dict):
        parsed = parsed["prompt"]
    mapping = {
        "PROMPT": prompt or "",
        "NEGATIVE": negative or "",
        "MODEL": model_filename or "",
        "IMAGE": image_name or "",
        "WIDTH": str(int(width)),
        "HEIGHT": str(int(height)),
    }
    return _replace_in_value(copy.deepcopy(parsed), mapping)
