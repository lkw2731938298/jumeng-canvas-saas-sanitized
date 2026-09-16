"""文本节点卡片生成：隐性字数上限（不展示计数器，结果超长则截断）。"""

from __future__ import annotations

# 文本节点卡片「生成」产出上限（汉字/字符按 Python len 计）
TEXT_NODE_GENERATION_MAX_CHARS = 5000

# 分镜/画布工具产出结构化长文，不受卡片 5000 字限制
_UNLIMITED_KIND_PREFIXES = ("storyboard_",)

_HIDDEN_OUTPUT_LIMIT_INSTRUCTION = (
    f"输出正文不超过{TEXT_NODE_GENERATION_MAX_CHARS}字，超出请自行精炼；只输出结果正文。"
)


def text_node_output_limited(
    text_prompt_kind: str | None,
    *,
    canvas_tool: str | None = None,
) -> bool:
    """是否对本次文本生成施加卡片 5000 字上限。"""
    if str(canvas_tool or "").strip():
        return False
    kind = str(text_prompt_kind or "").strip()
    if any(kind.startswith(prefix) for prefix in _UNLIMITED_KIND_PREFIXES):
        return False
    return True


def with_hidden_output_limit(
    system: str,
    text_prompt_kind: str | None,
    *,
    canvas_tool: str | None = None,
) -> str:
    """在系统提示中追加隐性字数约束（用户侧不展示）。"""
    if not text_node_output_limited(text_prompt_kind, canvas_tool=canvas_tool):
        return system
    sys = (system or "").rstrip()
    marker = f"不超过{TEXT_NODE_GENERATION_MAX_CHARS}字"
    if marker in sys:
        return sys
    return f"{sys}\n{_HIDDEN_OUTPUT_LIMIT_INSTRUCTION}".strip()


def clip_text_node_generated(
    text: str,
    text_prompt_kind: str | None,
    *,
    canvas_tool: str | None = None,
) -> str:
    """生成结果超过上限时静默截断，不失败、不提示。"""
    if not text_node_output_limited(text_prompt_kind, canvas_tool=canvas_tool):
        return text
    if len(text) <= TEXT_NODE_GENERATION_MAX_CHARS:
        return text
    return text[:TEXT_NODE_GENERATION_MAX_CHARS]


def clip_job_text_output(job: object, text: str) -> str:
    """按任务 input_params 判断是否截断生成正文。"""
    params = getattr(job, "input_params", None) or {}
    if not isinstance(params, dict):
        params = {}
    kind = params.get("textPromptKind") or params.get("text_prompt_kind")
    tool = params.get("canvasTool") or params.get("canvas_tool")
    return clip_text_node_generated(
        text,
        str(kind) if kind else None,
        canvas_tool=str(tool) if tool else None,
    )
