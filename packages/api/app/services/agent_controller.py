"""Agent 编排 / 续聊用的控制器 LLM 选型。

可选模型来自模型目录 category=text + chat_completion 能力（含 DeepSeek / 豆包 / GPT / Gemini / Claude）。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..core.config import get_settings
from ..core.llm_keys import is_model_configured
from ..core.model_registry import CANVAS_MODEL_SPECS, get_model_spec
from ..models.agent_session import AgentSession

# 多模态/可带附件的控制器（按 name 前缀或精确 id）
_FILE_CAPABLE_EXACT = frozenset(
    {
        "deepseek_v4_flash",
        "deepseek_v4_pro",
        "doubao_pro",
        "doubao_seed_21_pro",
        "doubao_seed_evolving",
        "rh_gemini_31_flash_lite",
        "rh_gemini_35_flash",
        "rh_gpt_56_sol",
        "rh_gpt_56_terra",
        "rh_gpt_55",
        "rh_claude_fable_5",
        "rh_claude_opus_48",
    }
)

# 推荐排序：豆包 Seed Evolving 为 AI 操控默认 → 豆包 Pro 回退 → 其它国内 → 海外
_PREFERRED_ORDER: tuple[str, ...] = (
    "doubao_seed_evolving",
    "doubao_pro",
    "deepseek_v4_flash",
    "doubao_seed_21_pro",
    "deepseek_v3",
    "deepseek_v4_pro",
    "deepseek_r1",
    "rh_gpt_56_sol",
    "rh_gpt_56_terra",
    "rh_gpt_55",
    "rh_gemini_35_flash",
    "rh_gemini_31_flash_lite",
    "rh_claude_fable_5",
    "rh_claude_opus_48",
)


def _is_controller_spec(spec: Any) -> bool:
    if getattr(spec, "category", None) != "text":
        return False
    if getattr(spec, "model_type", None) != "llm":
        return False
    caps = getattr(spec, "capabilities", ()) or ()
    return "chat_completion" in caps


def list_controller_model_ids(
    allowlist: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """目录中可用作控制器的文本 LLM id（按推荐序）。

    allowlist 非空时仅返回白名单与目录的交集（后台「AI 操控模型」配置）。
    """
    names = [s.name for s in CANVAS_MODEL_SPECS if _is_controller_spec(s)]
    if allowlist:
        allowed = {str(x).strip() for x in allowlist if str(x).strip()}
        names = [n for n in names if n in allowed]
    rank = {mid: i for i, mid in enumerate(_PREFERRED_ORDER)}
    names.sort(key=lambda n: (rank.get(n, 1000), n))
    return tuple(names)


# 兼容旧引用
CONTROLLER_MODEL_IDS: tuple[str, ...] = list_controller_model_ids()


def model_supports_files(model_id: str | None) -> bool:
    mid = (model_id or "").strip()
    if not mid:
        return False
    if mid in _FILE_CAPABLE_EXACT:
        return True
    # 海外 GPT / Gemini / Claude 默认视为可附件
    if mid.startswith("rh_gpt_") or mid.startswith("rh_gemini_") or mid.startswith("rh_claude_"):
        return True
    return False


def _static_controller_spec(model_id: str) -> Any | None:
    """优先读静态目录，避免 DB 快照缺模型时下拉漏项。"""
    for spec in CANVAS_MODEL_SPECS:
        if getattr(spec, "name", None) == model_id and _is_controller_spec(spec):
            return spec
    return None


def normalize_controller_model(
    model_id: str | None,
    *,
    allowlist: Sequence[str] | None = None,
) -> str | None:
    mid = (model_id or "").strip()
    if not mid:
        return None
    if allowlist is not None and len(tuple(x for x in allowlist if str(x).strip())) > 0:
        allowed = {str(x).strip() for x in allowlist if str(x).strip()}
        if mid not in allowed:
            return None
    if _static_controller_spec(mid) is not None:
        return mid
    # 兜底：运行时目录（自定义/运营新增）
    try:
        spec = get_model_spec(mid)
    except Exception:
        return None
    if spec is None or not _is_controller_spec(spec):
        return None
    return mid


def get_session_controller_model(session: AgentSession) -> str | None:
    brief = session.brief_json if isinstance(session.brief_json, dict) else {}
    return normalize_controller_model(str(brief.get("controllerModel") or ""))


def set_session_controller_model(session: AgentSession, model_id: str | None) -> str | None:
    """写入 brief_json.controllerModel；用户显式指定未配置密钥时拒绝（禁止静默回退）。"""
    from ..core.error_codes import ErrorCode
    from ..core.errors import fail

    mid = normalize_controller_model(model_id)
    if mid:
        settings = get_settings()
        if not settings.ai_mock_enabled:
            try:
                ok = bool(is_model_configured(mid))
            except Exception:
                ok = False
            if not ok:
                label = mid
                try:
                    spec = get_model_spec(mid)
                    if spec is not None:
                        label = str(getattr(spec, "display_name", None) or mid)
                except Exception:
                    pass
                fail(
                    ErrorCode.MODEL_UNAVAILABLE,
                    message=f"控制器模型「{label}」未配置密钥，请另选已配置的模型",
                )
    brief: dict[str, Any] = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    if mid:
        brief["controllerModel"] = mid
    else:
        brief.pop("controllerModel", None)
    session.brief_json = brief
    return mid


def pick_controller_model(
    preferred: str | None = None,
    *,
    allowlist: Sequence[str] | None = None,
) -> str | None:
    """优先用户指定（须已配置）；否则按推荐序选第一个已配置密钥的模型。mock 时返回 None。"""
    settings = get_settings()
    if settings.ai_mock_enabled:
        return None
    pref = normalize_controller_model(preferred, allowlist=allowlist)
    # 用户指定且已配置 → 直接用；指定但未配置 → 跳过该候选（不静默顶替，由 set 阶段已拦截新写入）
    if pref:
        try:
            if is_model_configured(pref):
                return pref
        except Exception:
            pass
    for mid in list_controller_model_ids(allowlist):
        if mid == pref:
            continue
        try:
            if is_model_configured(mid):
                return mid
        except Exception:
            continue
    return None


def controller_models_public(
    *,
    only_configured: bool = False,
    allowlist: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """前端下拉：含 GPT / Gemini / Claude / DeepSeek / 豆包（可按后台白名单过滤）。"""
    items: list[dict[str, Any]] = []
    for mid in list_controller_model_ids(allowlist):
        spec = _static_controller_spec(mid)
        if spec is None:
            try:
                spec = get_model_spec(mid)
            except Exception:
                continue
        if spec is None or not _is_controller_spec(spec):
            continue
        configured = False
        try:
            configured = bool(is_model_configured(mid))
        except Exception:
            configured = False
        if only_configured and not configured:
            continue
        items.append(
            {
                "id": mid,
                "label": str(getattr(spec, "display_name", None) or mid),
                "supportsFiles": model_supports_files(mid),
                "configured": configured,
                "provider": str(getattr(spec, "provider", "") or ""),
            }
        )
    return items


def all_controller_catalog_public() -> list[dict[str, Any]]:
    """管理端：目录内全部可选控制器（不受白名单限制）。"""
    return controller_models_public(allowlist=None)
