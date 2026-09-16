"""Render canvas tool prompts from v2 config + runtime params."""

from __future__ import annotations

from typing import Any

import json
import re

SHOT_KEYS = ["close", "medium", "wide"]

DEFAULT_H_FORMAT = "水平环绕{azimuth}度（{azimuthDesc}）"
DEFAULT_E_FORMAT = "俯仰{elevation}度（{elevationDesc}）"
DEFAULT_BRIGHT_FORMAT = "光照强度{brightness}%（{brightnessDesc}）"
DEFAULT_COLOR_FORMAT = "{colorDesc}"

LIGHT_DIRECTION_KEYS = ["front", "back", "left", "right", "top", "bottom"]

LEGACY_H_FORMATS = frozenset(
    {"摄像机水平环绕{azimuth}度", "水平方位{azimuth}度"}
)
LEGACY_E_FORMATS = frozenset({"俯仰角{elevation}度", "俯仰{elevation}度"})


def normalize_azimuth(azimuth: float | int) -> float:
    wrapped = float(azimuth) % 360.0
    if wrapped < 0:
        wrapped += 360.0
    return wrapped if wrapped < 360.0 else 0.0


def normalize_elevation(elevation: float | int) -> float:
    return max(-90.0, min(90.0, float(elevation)))


def format_angle_number(value: float | int) -> str:
    rounded = round(float(value) * 10) / 10
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def _azimuth_delta(a: float, b: float) -> float:
    diff = abs(normalize_azimuth(a) - normalize_azimuth(b))
    return min(diff, 360.0 - diff)


def _is_preset_azimuth(azimuth: float, preset: float, epsilon: float = 0.5) -> bool:
    return _azimuth_delta(azimuth, preset) <= epsilon


def _is_preset_elevation(elevation: float, preset: float, epsilon: float = 0.5) -> bool:
    return abs(normalize_elevation(elevation) - preset) <= epsilon


def describe_azimuth_for_prompt(azimuth: float | int) -> str:
    az = normalize_azimuth(azimuth)
    deg = format_angle_number(az)

    if _is_preset_azimuth(az, 0):
        return "POV在主体正前方，面向主体正面"
    if _is_preset_azimuth(az, 90):
        return "POV在主体正右侧，拍摄主体右侧面"
    if _is_preset_azimuth(az, 180):
        return "POV在主体正后方，拍摄主体背面"
    if _is_preset_azimuth(az, 270):
        return "POV在主体正左侧，拍摄主体左侧面"
    if _is_preset_azimuth(az, 45):
        return "POV在主体右前方45度，斜向拍摄主体"
    if _is_preset_azimuth(az, 135):
        return "POV在主体右后方135度，斜向拍摄主体背侧"
    if _is_preset_azimuth(az, 225):
        return "POV在主体左后方225度，斜向拍摄主体背侧"
    if _is_preset_azimuth(az, 315):
        return "POV在主体左前方315度，斜向拍摄主体"

    if 0 < az < 90:
        return f"POV在主体右前方约{deg}度，斜向拍摄主体"
    if 90 < az < 180:
        return f"POV在主体右后方约{deg}度，斜向拍摄主体背侧"
    if 180 < az < 270:
        return f"POV在主体左后方约{deg}度，斜向拍摄主体背侧"
    if 270 < az < 360:
        return f"POV在主体左前方约{deg}度，斜向拍摄主体"
    return f"POV绕主体水平旋转至{deg}度"


def describe_elevation_for_prompt(elevation: float | int) -> str:
    el = normalize_elevation(elevation)
    deg = format_angle_number(abs(el))

    if abs(el) < 2:
        return "POV与主体视线平齐，平视拍摄"
    if _is_preset_elevation(el, 90):
        return "POV在主体正上方，垂直俯视拍摄"
    if _is_preset_elevation(el, -90):
        return "POV在主体下方，极低角度仰拍"
    if _is_preset_elevation(el, 45):
        return "POV明显高于主体，俯拍视角"
    if _is_preset_elevation(el, -45):
        return "POV低于主体，仰拍视角"
    if el > 60:
        return f"POV在主体上方约{deg}度，大俯角向下拍摄"
    if el > 15:
        return f"POV略高于主体，俯角约{deg}度"
    if el < -60:
        return f"POV在主体下方约{deg}度，大仰角向上拍摄"
    return f"POV略低于主体，仰角约{deg}度"


def _apply_angle_format(pattern: str, runtime: dict[str, Any]) -> str:
    azimuth = format_angle_number(normalize_azimuth(runtime.get("azimuth") or 0))
    elevation = format_angle_number(normalize_elevation(runtime.get("elevation") or 0))
    azimuth_desc = describe_azimuth_for_prompt(runtime.get("azimuth") or 0)
    elevation_desc = describe_elevation_for_prompt(runtime.get("elevation") or 0)
    return (
        pattern.replace("{azimuth}", azimuth)
        .replace("{elevation}", elevation)
        .replace("{azimuthDesc}", azimuth_desc)
        .replace("{elevationDesc}", elevation_desc)
    )


def _resolve_horizontal(tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    formats = tool_config.get("formats") if isinstance(tool_config.get("formats"), dict) else {}
    pattern = str(formats.get("h") or DEFAULT_H_FORMAT).strip() or DEFAULT_H_FORMAT
    return _apply_angle_format(pattern, runtime)


def _resolve_elevation(tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    formats = tool_config.get("formats") if isinstance(tool_config.get("formats"), dict) else {}
    pattern = str(formats.get("e") or DEFAULT_E_FORMAT).strip() or DEFAULT_E_FORMAT
    return _apply_angle_format(pattern, runtime)


def _lookup_shot(lookups: dict[str, Any], runtime: dict[str, Any]) -> str:
    table = lookups.get("s") or {}
    if not isinstance(table, dict):
        return ""
    return str(table.get(str(runtime.get("shot") or "")) or "")


def _get_static_base(static: dict[str, Any]) -> str:
    return str(static.get("base") or static.get("b") or "").strip()


def extract_subject_prompt_for_multi_angle(text: str) -> str:
    trimmed = str(text or "").strip()
    if not trimmed:
        return ""

    section_match = re.search(
        r"(?:^|\n)#{1,3}\s*(?:正向提示词|图片提示词|Image Prompt|Prompt)"
        r"(?:[（(][^）)]*[）)])?[^\n]*\n+([\s\S]*?)(?=\n#{1,3}\s|$)",
        trimmed,
        re.IGNORECASE,
    )
    if section_match and section_match.group(1).strip():
        return section_match.group(1).strip()

    if trimmed.startswith("{"):
        try:
            obj = json.loads(trimmed)
            if isinstance(obj, dict):
                for key in ("imagePrompt", "图片提示词", "image_prompt"):
                    val = obj.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
        except Exception:
            pass

    return trimmed


def normalize_canvas_base_for_composer(tool_config: dict[str, Any], canvas_base: str) -> str:
    static = tool_config.get("static") if isinstance(tool_config.get("static"), dict) else {}
    joiner = str(static.get("j") or "，")
    text = extract_subject_prompt_for_multi_angle(canvas_base)
    admin_base = _get_static_base(static)
    if admin_base and text.startswith(admin_base):
        text = text[len(admin_base) :].lstrip()
        if text.startswith(joiner):
            text = text[len(joiner) :].lstrip()
    static_c = str(static.get("c") or "").strip()
    if static_c and static_c in text:
        text = text.replace(static_c, "").strip()
    return text.strip()


def merge_composer_base(tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    static = tool_config.get("static") if isinstance(tool_config.get("static"), dict) else {}
    joiner = str(static.get("j") or "，")
    mode = str(static.get("baseMode") or static.get("base_mode") or "admin")
    admin_base = _get_static_base(static)
    canvas_base = normalize_canvas_base_for_composer(
        tool_config,
        str(runtime.get("base") or runtime.get("basePrompt") or "").strip(),
    )

    if mode == "admin":
        return admin_base
    if mode == "canvas":
        return canvas_base
    if admin_base and canvas_base:
        return f"{admin_base}{joiner}{canvas_base}"
    return admin_base or canvas_base


def _resolve_segment(segment: str, *, tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    token = segment.strip()
    if not token:
        return ""

    static = tool_config.get("static") if isinstance(tool_config.get("static"), dict) else {}
    lookups = tool_config.get("lookups") if isinstance(tool_config.get("lookups"), dict) else {}

    if token in ("{base}", "{basePrompt}"):
        return merge_composer_base(tool_config, runtime)

    if token in ("{extra}", "{extra?}"):
        extra = str(runtime.get("extra") or runtime.get("extraPrompt") or "").strip()
        if token == "{extra?}" and not extra:
            return ""
        return extra

    if token == "$j":
        return str(static.get("j") or "，")

    if token == "$b":
        return _get_static_base(static)

    if token == "$c":
        return str(static.get("c") or "")

    if token == "$h":
        return _resolve_horizontal(tool_config, runtime)

    if token == "$e":
        return _resolve_elevation(tool_config, runtime)

    if token == "$s":
        return _lookup_shot(lookups, runtime)

    return token


def render_composer(tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    template = str(tool_config.get("template") or "{base} → $h → $e → $s → $c → {extra?}")
    static = tool_config.get("static") if isinstance(tool_config.get("static"), dict) else {}
    joiner = str(static.get("j") or "，")

    segments = [part.strip() for part in template.split("→") if part.strip()]
    parts: list[str] = []
    for segment in segments:
        value = _resolve_segment(segment, tool_config=tool_config, runtime=runtime).strip()
        if value:
            parts.append(value)
    return joiner.join(parts)


def render_append(tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    base = str(runtime.get("base") or runtime.get("basePrompt") or "").strip()
    append_text = str(tool_config.get("appendText") or tool_config.get("append_text") or "").strip()
    joiner = str(tool_config.get("joiner") or "，")
    if not base:
        return append_text
    if not append_text:
        return base
    return f"{base}{joiner}{append_text}"


def render_tool_prompt(tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    kind = str(tool_config.get("kind") or "append")
    if kind == "composer":
        return render_composer(tool_config, runtime)
    if kind == "text_gen":
        return build_text_gen_user_content(tool_config, runtime)
    # creative_tools：预览/兼容路径按 appendText 拼接
    if kind == "creative_tools":
        return render_append(tool_config, runtime)
    return render_append(tool_config, runtime)


def _cfg_str(tool_config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = tool_config.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def build_text_gen_user_content(tool_config: dict[str, Any], runtime: dict[str, Any]) -> str:
    parts: list[str] = []

    prefix = _cfg_str(tool_config, "userPrefix", "user_prefix")
    if prefix:
        parts.append(prefix)

    role_rule = _cfg_str(tool_config, "roleRule", "role_rule")
    scene_rule = _cfg_str(tool_config, "sceneRule", "scene_rule")
    prop_rule = _cfg_str(tool_config, "propRule", "prop_rule")
    if role_rule:
        parts.append(f"## 角色提取规则\n{role_rule}")
    if scene_rule:
        parts.append(f"## 场景提取规则\n{scene_rule}")
    if prop_rule:
        parts.append(f"## 道具提取规则\n{prop_rule}")

    ref_blocks = runtime.get("referenceBlocks") or runtime.get("reference_blocks") or []
    if isinstance(ref_blocks, list):
        for block in ref_blocks:
            text = str(block or "").strip()
            if text:
                parts.append(text)

    content = str(runtime.get("content") or "").strip()
    if content:
        if parts:
            parts.append(f"## 用户输入\n{content}")
        else:
            parts.append(content)

    return "\n\n".join(parts)


def get_text_gen_system_prompt(tool_config: dict[str, Any] | None, fallback: str = "") -> str:
    if not tool_config:
        return fallback
    return _cfg_str(tool_config, "systemPrompt", "system_prompt") or fallback


def validate_composer(tool_config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    lookups = tool_config.get("lookups") if isinstance(tool_config.get("lookups"), dict) else {}
    formats = tool_config.get("formats") if isinstance(tool_config.get("formats"), dict) else {}

    if not str(formats.get("h") or DEFAULT_H_FORMAT).strip():
        errors.append("formats.h 不能为空")
    if not str(formats.get("e") or DEFAULT_E_FORMAT).strip():
        errors.append("formats.e 不能为空")

    shot_table = lookups.get("s") or {}
    if not isinstance(shot_table, dict):
        errors.append("lookups.s 必须是对象")
    else:
        missing = [key for key in SHOT_KEYS if not str(shot_table.get(key) or "").strip()]
        if missing:
            errors.append(f"lookups.s 缺少键: {', '.join(missing)}")

    template = str(tool_config.get("template") or "")
    if not template.strip():
        errors.append("template 不能为空")
    return errors


def validate_append(tool_config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(tool_config.get("menuLabel") or tool_config.get("menu_label") or "").strip():
        errors.append("menuLabel 不能为空")
    if not str(tool_config.get("appendText") or tool_config.get("append_text") or "").strip():
        errors.append("appendText 不能为空")
    return errors


def validate_text_gen(tool_config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _cfg_str(tool_config, "menuLabel", "menu_label"):
        errors.append("menuLabel 不能为空")
    if not _cfg_str(tool_config, "systemPrompt", "system_prompt"):
        errors.append("systemPrompt 不能为空")
    return errors


def validate_visual_styles(tool_config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _cfg_str(tool_config, "menuLabel", "menu_label"):
        errors.append("menuLabel 不能为空")
    items = tool_config.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items 至少包含一个风格")
        return errors
    seen: set[str] = set()
    has_none = False
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            errors.append(f"items[{idx}] 格式无效")
            continue
        style_id = str(raw.get("id") or "").strip()
        if not style_id:
            errors.append(f"items[{idx}].id 不能为空")
            continue
        if style_id in seen:
            errors.append(f"风格 id 重复：{style_id}")
        seen.add(style_id)
        if style_id == "none":
            has_none = True
        if not str(raw.get("label") or "").strip():
            errors.append(f"items[{idx}].label 不能为空")
        if style_id != "none" and not str(raw.get("prompt") or "").strip():
            errors.append(f"风格「{style_id}」的 prompt 不能为空")
    if not has_none:
        errors.append("必须包含固定风格 id=none（无）")
    return errors


def validate_creative_tools(tool_config: dict[str, Any]) -> list[str]:
    """校验九宫格创作工具 items（id/label/prompt）。"""
    errors: list[str] = []
    if not _cfg_str(tool_config, "menuLabel", "menu_label"):
        errors.append("menuLabel 不能为空")
    items = tool_config.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items 至少包含一个创作工具提示词")
        return errors
    seen: set[str] = set()
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            errors.append(f"items[{idx}] 格式无效")
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            errors.append(f"items[{idx}].id 不能为空")
            continue
        if item_id in seen:
            errors.append(f"工具 id 重复：{item_id}")
        seen.add(item_id)
        if not str(raw.get("label") or "").strip():
            errors.append(f"items[{idx}].label 不能为空")
        if raw.get("enabled", True) and not str(raw.get("prompt") or "").strip():
            errors.append(f"工具「{item_id}」的 prompt 不能为空")
    return errors


def validate_tool_config(tool_id: str, tool_config: dict[str, Any]) -> list[str]:
    kind = str(tool_config.get("kind") or "append")
    if kind == "composer":
        return validate_composer(tool_config)
    if kind == "text_gen":
        return validate_text_gen(tool_config)
    if kind == "visual_styles":
        return validate_visual_styles(tool_config)
    if kind == "creative_tools" or tool_id == "grid_9":
        return validate_creative_tools(tool_config)
    return validate_append(tool_config)
