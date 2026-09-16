"""读取技能包 agents/openai.yaml（Codex sidecar）：UI 文案 + 隐式调用策略。

忽略 MCP dependencies。
隐式策略兼容两种写法（nested 优先）：
- Codex 常见：policy.allow_implicit_invocation
- 平台简写：顶层 allow_implicit_invocation
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .skill_docs import SKILL_DOCS_DIR, _safe_slug, is_skill_doc_package


def _parse_simple_yaml_map(text: str) -> dict[str, Any]:
    """极简 YAML：支持顶层 key、一层缩进嵌套、bool/str。不引依赖 pyyaml。"""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, root)]
    for raw in (text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if ":" not in raw:
            continue
        key, _, rest = raw.strip().partition(":")
        key = key.strip()
        val = rest.strip().strip("\"'")
        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent + 2, child))
            continue
        low = val.lower()
        if low in ("true", "yes", "on"):
            parent[key] = True
        elif low in ("false", "no", "off"):
            parent[key] = False
        else:
            parent[key] = val
    return root


def normalize_openai_skill_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    """归一化为 {allowImplicitInvocation, displayName, shortDescription, defaultPrompt}。"""
    data = raw if isinstance(raw, dict) else {}
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    interface = data.get("interface") if isinstance(data.get("interface"), dict) else {}
    # nested policy 优先；否则认顶层简写（平台技能包现状）
    allow = policy.get("allow_implicit_invocation")
    if allow is None and "allow_implicit_invocation" in data:
        allow = data.get("allow_implicit_invocation")
    if allow is None:
        allow = True
    display = interface.get("display_name") or data.get("display_name")
    short = interface.get("short_description") or data.get("short_description")
    default_prompt = interface.get("default_prompt") or data.get("default_prompt")
    return {
        "allowImplicitInvocation": bool(allow),
        "displayName": str(display or "").strip()[:128],
        "shortDescription": str(short or "").strip()[:512],
        "defaultPrompt": str(default_prompt or "").strip()[:2000],
    }


def load_openai_yaml_text(text: str) -> dict[str, Any]:
    return normalize_openai_skill_policy(_parse_simple_yaml_map(text))


def load_platform_skill_openai_policy(slug: str) -> dict[str, Any]:
    """从 skill_docs/{slug}/agents/openai.yaml 读取；缺省允许隐式。"""
    safe = _safe_slug(slug)
    if not safe or not is_skill_doc_package(safe):
        return normalize_openai_skill_policy(None)
    path = Path(SKILL_DOCS_DIR) / safe / "agents" / "openai.yaml"
    if not path.is_file():
        return normalize_openai_skill_policy(None)
    try:
        return load_openai_yaml_text(path.read_text(encoding="utf-8"))
    except OSError:
        return normalize_openai_skill_policy(None)


def load_skill_openai_policy_from_package_files(
    package_files: dict[str, str] | None,
) -> dict[str, Any]:
    """用户包：若 package_files 含 agents/openai.yaml 则解析。"""
    if not isinstance(package_files, dict):
        return normalize_openai_skill_policy(None)
    for key, body in package_files.items():
        path = str(key or "").replace("\\", "/").lstrip("/").lower()
        if path == "agents/openai.yaml" or path.endswith("/agents/openai.yaml"):
            return load_openai_yaml_text(str(body or ""))
    return normalize_openai_skill_policy(None)


def format_implicit_policy_hint(entries: list[dict[str, Any]]) -> str:
    """目录附录：列出关闭隐式调用的技能。"""
    closed = [
        str(e.get("name") or e.get("slug") or "").strip()
        for e in entries
        if e.get("allowImplicitInvocation") is False
    ]
    closed = [x for x in closed if x]
    if not closed:
        return ""
    joined = "、".join(closed[:12])
    return (
        "【隐式技能策略】以下技能关闭了隐式调用，"
        f"仅当用户芯片 / $name 显式点名时才 load：{joined}。"
    )
