"""Agent 写入节点 generationOptions 时的键值归一化。

画布选择器的选项 id 均为字符串（时长 "5"、清晰度 "720"、画幅 "16:9"）。
LLM 常写成 "4s" / 4 / "4秒" / "720P" / "16x9"，若不归一化，
前端 resolvePresetItem 匹配失败会回落到 defaultId（视频时长常见为 5s）。
时长还必须按当前模型合法区间钳制（如 MiniMax-H3 仅 5–15 秒，写 4 会被丢弃）。
"""

from __future__ import annotations

import re
from typing import Any

# 顶层误写的时长/画幅等，应并入 generationOptions
_LIFT_KEYS = {
    "duration": "duration",
    "durationSec": "duration",
    "duration_sec": "duration",
    "seconds": "duration",
    "时长": "duration",
    "aspectRatio": "aspectRatio",
    "aspect_ratio": "aspectRatio",
    "ratio": "aspectRatio",
    "画幅": "aspectRatio",
    "resolution": "resolution",
    "clarity": "resolution",
    "清晰度": "resolution",
    "realPerson": "realPerson",
    "真人": "realPerson",
}

_KEY_ALIASES = {
    "durationsec": "duration",
    "duration_sec": "duration",
    "seconds": "duration",
    "时长": "duration",
    "aspect_ratio": "aspectRatio",
    "ratio": "aspectRatio",
    "画幅": "aspectRatio",
    "clarity": "resolution",
    "清晰度": "resolution",
    "res": "resolution",
}


def _digits(value: Any) -> str | None:
    m = re.search(r"(\d+(?:\.\d+)?)", str(value).strip())
    if not m:
        return None
    raw = m.group(1)
    try:
        n = float(raw)
    except ValueError:
        return None
    if n <= 0:
        return None
    # 时长/清晰度选项 id 多为整数秒或 720
    if abs(n - round(n)) < 1e-6:
        return str(int(round(n)))
    return raw


def _norm_ratio(value: Any) -> str:
    s = str(value).strip().lower().replace("×", "x").replace(" ", "")
    s = s.replace("x", ":")
    # 已是 a:b
    if re.fullmatch(r"\d+:\d+", s):
        return s
    return str(value).strip()


def _norm_on_off(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on", "开启", "开", "是"):
        return "on"
    if s in ("0", "false", "no", "off", "关闭", "关", "否"):
        return "off"
    return str(value).strip()


def normalize_generation_options(raw: Any) -> dict[str, str]:
    """把 LLM/启发式给出的 generationOptions 归一成选择器可用的 string map。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in list(raw.items())[:32]:
        if v is None:
            continue
        key = str(k).strip()
        if not key:
            continue
        alias = _KEY_ALIASES.get(key.lower()) or _KEY_ALIASES.get(key)
        if alias:
            key = alias
        if key == "duration":
            dig = _digits(v)
            if dig:
                out["duration"] = dig
            continue
        if key in ("aspectRatio", "ratio"):
            # 双写：Agent/LLM 常用 aspectRatio；视频选择器 group id 多为 ratio
            ratio = _norm_ratio(v)
            out["aspectRatio"] = ratio
            out["ratio"] = ratio
            continue
        if key == "resolution":
            dig = _digits(v)
            # 保留 720p → 720；若原值本就是档位 id 则优先 digits
            if dig:
                out["resolution"] = dig
            else:
                out["resolution"] = str(v).strip().lower().replace("p", "")
            continue
        if key in ("realPerson", "watermark"):
            out[key] = _norm_on_off(v)
            continue
        if key in ("audio", "instrumental"):
            out[key] = str(v).strip()
            continue
        if isinstance(v, bool):
            out[key] = "on" if v else "off"
        elif isinstance(v, (int, float)):
            if isinstance(v, float) and abs(v - round(v)) < 1e-6:
                out[key] = str(int(round(v)))
            else:
                out[key] = str(v)
        elif isinstance(v, str):
            s = v.strip()
            if s:
                out[key] = s
        # 忽略嵌套对象/数组
    return out


def clamp_generation_options_for_model(
    model_name: str | None,
    go: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """按模型合法时长钳制 duration；越界则改到最近合法秒并给出中文说明。"""
    if not isinstance(go, dict) or not go:
        return go, []
    name = str(model_name or "").strip()
    if not name:
        return dict(go), []
    from ..core.model_registry import CANVAS_MODEL_SPECS, video_duration_limits

    limits = video_duration_limits(name)
    if not limits:
        return dict(go), []
    raw = str(go.get("duration") or "").strip()
    if not raw:
        return dict(go), []
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return dict(go), []
    lo, hi = int(limits[0]), int(limits[1])
    clamped = min(max(n, lo), hi)
    if clamped == n:
        return dict(go), []
    out = dict(go)
    out["duration"] = str(clamped)
    disp = name
    for spec in CANVAS_MODEL_SPECS:
        if getattr(spec, "name", None) == name:
            disp = str(getattr(spec, "display_name", None) or name)
            break
    note = (
        f"时长已按「{disp}」合法范围改为 {clamped}s"
        f"（该模型 {lo}–{hi} 秒，不支持 {n}s）"
    )
    return out, [note]


def sanitize_agent_node_params(
    params: dict[str, Any] | None,
    *,
    model_name: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """清洗节点 params：归一 generationOptions，并把顶层误写的时长等抬进去。

    若能确定模型，按该模型时长上下限钳制。返回 (清洗后的 params, 钳制说明)。
    """
    if not isinstance(params, dict):
        return {}, []
    out: dict[str, Any] = {}
    lifted: dict[str, Any] = {}
    for k, v in list(params.items())[:48]:
        key = str(k).strip()
        if not key:
            continue
        if key == "generationOptions":
            continue
        if key in _LIFT_KEYS:
            lifted[_LIFT_KEYS[key]] = v
            continue
        out[key] = v

    go: dict[str, str] = {}
    raw_go = params.get("generationOptions")
    if isinstance(raw_go, dict):
        go.update(normalize_generation_options(raw_go))
    if lifted:
        go.update(normalize_generation_options(lifted))
    notes: list[str] = []
    mid = str(model_name or out.get("model") or "").strip() or None
    if go:
        go, notes = clamp_generation_options_for_model(mid, go)
        out["generationOptions"] = go
    return out, notes
