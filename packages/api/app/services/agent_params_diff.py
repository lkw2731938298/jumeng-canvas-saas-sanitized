"""update_node_params 只传 diff：对齐 Codex apply_patch，禁止整份重发 generationOptions。"""

from __future__ import annotations

from typing import Any

# 允许写入的顶层参数键（其它键忽略，防模型乱塞）
_ALLOWED_PARAM_KEYS = frozenset(
    {
        "prompt",
        "content",
        "model",
        "assetId",
        "label",
        "generationOptions",
        "title",
        "duration",
        "aspectRatio",
        "clarity",
        "cinematicShotIndex",
        "cinematicBatchId",
        "negativePrompt",
    }
)

# generationOptions 内常见字段；未知键仍保留（模型各异），但去掉空值
_GO_DROP_EMPTY = True


def _is_empty_value(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and not val.strip():
        return True
    if isinstance(val, (list, dict)) and len(val) == 0:
        return True
    return False


def slim_generation_options_patch(raw: Any) -> dict[str, Any] | None:
    """generationOptions 只保留本轮要改的键；去掉空值。"""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key, val in raw.items():
        k = str(key or "").strip()
        if not k or k.startswith("_"):
            continue
        if _GO_DROP_EMPTY and _is_empty_value(val):
            continue
        # 禁止把整段超大嵌套当一次 patch（防误塞完整节点副本）
        if isinstance(val, dict) and len(str(val)) > 4000:
            continue
        if isinstance(val, str) and len(val) > 8000:
            out[k] = val[:8000]
        else:
            out[k] = val
        if len(out) >= 40:
            break
    return out or None


def slim_update_node_params_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """从工具参数抽出「只要改的字段」params。

    支持模型把 prompt/content 写在顶层；合并进 params 后去空、限键。
    返回空 dict 表示无可投影变更。
    """
    raw = args if isinstance(args, dict) else {}
    params_in = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    merged: dict[str, Any] = dict(params_in)

    for key in (
        "prompt",
        "content",
        "model",
        "assetId",
        "label",
        "generationOptions",
        "title",
        "negativePrompt",
    ):
        if key in raw and raw[key] is not None and key not in merged:
            merged[key] = raw[key]

    out: dict[str, Any] = {}
    for key, val in merged.items():
        k = str(key or "").strip()
        if k not in _ALLOWED_PARAM_KEYS:
            continue
        if _is_empty_value(val):
            continue
        if k == "generationOptions":
            go = slim_generation_options_patch(val)
            if go:
                out[k] = go
            continue
        if isinstance(val, str) and len(val) > 20000:
            out[k] = val[:20000]
        else:
            out[k] = val
    return out
