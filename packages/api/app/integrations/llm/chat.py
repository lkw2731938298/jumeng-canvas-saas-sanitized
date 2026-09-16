"""LLM 聊天补全 —— 密钥仅从 config/llm-keys.env / DB 凭证加载。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...core.llm_keys import ProviderKeys, get_llm_keys, get_model_credentials, get_provider_for_model
from ...core.model_registry import get_model_spec
from ..upstream.trace_context import note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)
VISION_CAPABLE_MODELS = frozenset(
    {"doubao_pro", "doubao_seed_21_pro", "doubao_seed_evolving"}
)

# Canvas model id -> provider API model name
REMOTE_MODEL_IDS: dict[str, str] = {
    "deepseek_v3": "deepseek-chat",
    "deepseek_r1": "deepseek-reasoner",
    "deepseek_v4_flash": "deepseek-v4-flash",
    "deepseek_v4_pro": "deepseek-v4-pro",
    "doubao_seed_21_pro": "doubao-seed-2-1-pro-260628",
    "doubao_seed_evolving": "doubao-seed-evolving",
    "rh_gemini_31_flash_lite": "google/gemini-3.1-flash-lite-preview",
    "rh_gemini_35_flash": "google/gemini-3.5-flash",
    "rh_gpt_56_sol": "openai/gpt-5.6-sol",
    "rh_gpt_56_terra": "openai/gpt-5.6-terra",
    "rh_gpt_55": "openai/gpt-5.5",
    "rh_claude_fable_5": "anthropic/claude-fable-5",
    "rh_claude_opus_48": "anthropic/claude-opus-4.8",
}

RH_LLM_API_BASE = "https://llm.runninghub.ai/v1"
BAILIAN_COMPAT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _resolve_remote_model(model_id: str) -> str:
    # 豆包 Pro 走方舟 Endpoint ID；其它豆包文本（Seed 2.1 / Evolving）用模型自身 upstream
    if model_id == "doubao_pro":
        keys = get_llm_keys()
        if keys.doubao.endpoint_id.strip():
            return keys.doubao.endpoint_id.strip()
        if keys.doubao_text_model.strip():
            return keys.doubao_text_model.strip()
        raise ValueError(
            "文本生成需要配置 DOUBAO_ENDPOINT_ID（火山方舟文本接入点 ID）。"
            "请在管理后台配置供应商密钥，或设置 DOUBAO_TEXT_MODEL。"
        )

    spec = get_model_spec(model_id)
    if spec and spec.provider == "doubao" and spec.category == "text" and spec.upstream_model:
        return spec.upstream_model

    remote_model = REMOTE_MODEL_IDS.get(model_id)
    if not remote_model:
        if spec and spec.upstream_model:
            return spec.upstream_model
        raise ValueError(f"Chat completion not implemented for model: {model_id}")
    return remote_model


def _chat_completions_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/api/v3"):
        return f"{base}/chat/completions"
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _resolve_chat_creds(model_id: str) -> ProviderKeys:
    """按模型解析聊天用密钥与 base（RH LLM / 百炼兼容模式特殊处理）。"""
    spec = get_model_spec(model_id)
    extra = (spec.parameters_extra if spec else {}) or {}
    creds = get_model_credentials(model_id)
    keys = get_llm_keys()

    # RunningHub 海外版 LLM：媒体 API 地址与 LLM 网关分离
    if extra.get("rh_llm") or (spec and spec.provider == "ltx_runninghub" and spec.category == "text"):
        api_key = keys.ltx_runninghub.api_key or creds.api_key
        if not api_key:
            raise ValueError("RunningHub 海外版 LLM 未配置 API Key")
        return ProviderKeys(api_key=api_key, api_base=RH_LLM_API_BASE)

    # 百炼兼容模式（DeepSeek-V4-Pro 等）
    if extra.get("bailian_compatible") or model_id == "deepseek_v4_pro":
        api_key = keys.qwen.api_key or keys.dashscope.api_key or creds.api_key
        if not api_key:
            raise ValueError("百炼兼容模式未配置 API Key（QWEN/DASHSCOPE）")
        return ProviderKeys(api_key=api_key, api_base=BAILIAN_COMPAT_API_BASE)

    return creds


async def chat_completion(
    model_id: str,
    messages: list[dict],
    *,
    temperature: float = 1.0,
    max_tokens: int = 4096,
    timeout_s: float = 120.0,
) -> str:
    """调用上游 Chat Completions API，返回 assistant 文本内容。

    timeout_s：读超时秒数；爆款拉片等多图请求应传更大值（如 600）。
    """
    creds = _resolve_chat_creds(model_id)
    remote_model = _resolve_remote_model(model_id)

    url = _chat_completions_url(creds.api_base)

    payload = {
        "model": remote_model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "max_tokens": max_tokens,
    }

    provider = get_provider_for_model(model_id)
    read_timeout = max(30.0, float(timeout_s))
    logger.info(
        "[upstream] POST %s model=%s kind=chat timeout_s=%.0f",
        url,
        remote_model,
        read_timeout,
    )
    note_upstream(provider=provider, event="submit", detail={"kind": "chat", "model": remote_model})

    async with httpx.AsyncClient(timeout=httpx.Timeout(read_timeout, connect=10.0)) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {creds.api_key}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code >= 400:
            logger.error("LLM error %s: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        data = resp.json()

    note_upstream_billing_from_response(
        data,
        provider=get_provider_for_model(model_id),
        event="chat_completion",
    )
    req_id = str(data.get("id") or "").strip()
    if req_id:
        note_upstream(provider_request_id=req_id, event="chat_completion")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned empty choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned empty content")
    return content.strip()


# Doubao-Seed-Evolving：仅该控制器加方舟 thinking / 并行工具字段；其它模型行为不变
EVOLVING_MODEL_ID = "doubao_seed_evolving"
# 快路径：关思考，避免思考 token 挤占 4k～8k 输出
EVOLVING_FAST_MAX_TOKENS = 8192
EVOLVING_FAST_TIMEOUT_S = 180.0
# 深路径：开思考 + medium，给够输出额度
EVOLVING_DEEP_MAX_TOKENS = 24576
EVOLVING_DEEP_TIMEOUT_S = 360.0
# 方舟上下文缓存需另走 Context API（context_id），Chat Completions 无稳定 caching 字段；本批不造私有协议
EVOLVING_CONTEXT_CACHE_DEFERRED = True


def is_evolving_controller(model_id: str) -> bool:
    """是否为 Evolving 助手控制器（才加 thinking / parallel_tool_calls）。"""
    return (model_id or "").strip() == EVOLVING_MODEL_ID


def evolving_tools_call_kwargs(*, deep: bool) -> dict[str, Any]:
    """Evolving 工具调用的 max_tokens / 超时 / 思考开关（由 runtime 传入 chat_completion_tools）。"""
    if deep:
        return {
            "max_tokens": EVOLVING_DEEP_MAX_TOKENS,
            "timeout_s": EVOLVING_DEEP_TIMEOUT_S,
            "thinking_enabled": True,
            "reasoning_effort": "medium",
            "parallel_tool_calls": True,
        }
    return {
        "max_tokens": EVOLVING_FAST_MAX_TOKENS,
        "timeout_s": EVOLVING_FAST_TIMEOUT_S,
        "thinking_enabled": False,
        "reasoning_effort": None,
        "parallel_tool_calls": True,
    }


def apply_evolving_chat_tools_payload(
    payload: dict[str, Any],
    model_id: str,
    *,
    thinking_enabled: bool | None = None,
    reasoning_effort: str | None = None,
    parallel_tool_calls: bool | None = None,
) -> None:
    """仅 Evolving 写入方舟 thinking / parallel_tool_calls；默认关思考，避免官方默认开启挤占输出。"""
    if not is_evolving_controller(model_id):
        return
    enabled = bool(thinking_enabled)
    payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
    if enabled:
        payload["reasoning_effort"] = (reasoning_effort or "medium").strip() or "medium"
    # Evolving 擅长同轮多工具；读工具由 runtime 全部执行完再交回
    payload["parallel_tool_calls"] = True if parallel_tool_calls is None else bool(parallel_tool_calls)


def _message_text(content: Any) -> str:
    """把上游 content（字符串或分段列表）收成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return ""


def visible_text_and_reasoning(message: dict[str, Any] | None) -> tuple[str, str]:
    """拆出用户可见 content 与思考链；思考链不展示给用户。

    方舟可能把思考放在 reasoning_content / reasoning，或 content 分段 type=reasoning。
    """
    if not isinstance(message, dict):
        return "", ""
    reasoning_parts: list[str] = []
    for key in ("reasoning_content", "reasoning"):
        val = message.get(key)
        if isinstance(val, str) and val.strip():
            reasoning_parts.append(val.strip())
        elif isinstance(val, dict):
            inner = val.get("content") or val.get("text")
            if isinstance(inner, str) and inner.strip():
                reasoning_parts.append(inner.strip())
    content = message.get("content")
    visible = ""
    if isinstance(content, str):
        visible = content.strip()
    elif isinstance(content, list):
        vis_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    vis_parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            typ = str(item.get("type") or "").strip().lower()
            text = str(item.get("text") or item.get("content") or "")
            if typ in ("reasoning", "thinking"):
                if text.strip():
                    reasoning_parts.append(text.strip())
            elif text:
                vis_parts.append(text)
        visible = "\n".join(p for p in vis_parts if p).strip()
    return visible, "\n".join(reasoning_parts)


def cached_prompt_tokens_from_usage(data: Any) -> int | None:
    """从 Chat Completions usage 读取前缀缓存命中 token（若官方带回）。无字段则 None。"""
    if not isinstance(data, dict):
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        for key in ("cached_tokens", "cache_tokens", "cachedTokens"):
            raw = details.get(key)
            if isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)) and raw >= 0:
                return int(raw)
    for key in ("prompt_cache_hit_tokens", "cached_tokens", "cache_tokens"):
        raw = usage.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)) and raw >= 0:
            return int(raw)
    return None


def _note_prompt_cache_from_response(data: Any) -> None:
    """有 cached_tokens 时写入 trace，便于对照账单；无字段则静默（缓存本批延期）。"""
    n = cached_prompt_tokens_from_usage(data)
    if n is None:
        return
    note_upstream(event="prompt_cache", detail={"cached_tokens": n})


class ChatToolsResult:
    """带 tool_calls 的聊天结果（content 为用户可见文本，可为空）。"""

    def __init__(
        self,
        *,
        content: str,
        tool_calls: list[dict[str, Any]],
        raw_message: dict[str, Any],
        reasoning: str = "",
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.raw_message = raw_message
        # 思考链仅供日志/追溯，禁止写入用户可见 assistant 文案
        self.reasoning = reasoning


async def chat_completion_tools(
    model_id: str,
    messages: list[dict],
    *,
    tools: list[dict[str, Any]],
    temperature: float = 0.4,
    max_tokens: int = 4096,
    timeout_s: float = 180.0,
    thinking_enabled: bool | None = None,
    reasoning_effort: str | None = None,
    parallel_tool_calls: bool | None = None,
) -> ChatToolsResult:
    """Chat Completions + OpenAI 兼容 function calling。

    有 tool_calls 时 content 允许为空；两者都空则报错。
    Evolving 可传 thinking_enabled / parallel_tool_calls；思考链不进入 content。
    """
    creds = _resolve_chat_creds(model_id)
    remote_model = _resolve_remote_model(model_id)
    url = _chat_completions_url(creds.api_base)
    payload: dict[str, Any] = {
        "model": remote_model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": "auto",
    }
    apply_evolving_chat_tools_payload(
        payload,
        model_id,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        parallel_tool_calls=parallel_tool_calls,
    )
    provider = get_provider_for_model(model_id)
    read_timeout = max(30.0, float(timeout_s))
    logger.info(
        "[upstream] POST %s model=%s kind=chat_tools timeout_s=%.0f tools=%s thinking=%s",
        url,
        remote_model,
        read_timeout,
        len(tools),
        payload.get("thinking"),
    )
    note_upstream(
        provider=provider,
        event="submit",
        detail={
            "kind": "chat_tools",
            "model": remote_model,
            "thinking": payload.get("thinking"),
            "max_tokens": max_tokens,
        },
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(read_timeout, connect=10.0)) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {creds.api_key}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code >= 400:
            logger.error("LLM tools error %s: %s", resp.status_code, resp.text[:800])
            resp.raise_for_status()
        data = resp.json()

    note_upstream_billing_from_response(
        data,
        provider=get_provider_for_model(model_id),
        event="chat_completion",
    )
    _note_prompt_cache_from_response(data)
    req_id = str(data.get("id") or "").strip()
    if req_id:
        note_upstream(provider_request_id=req_id, event="chat_completion")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned empty choices")
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        raise RuntimeError("LLM returned invalid message")
    content, reasoning = visible_text_and_reasoning(message)
    raw_calls = message.get("tool_calls") or []
    tool_calls: list[dict[str, Any]] = []
    if isinstance(raw_calls, list):
        for item in raw_calls:
            if isinstance(item, dict) and item.get("id"):
                tool_calls.append(item)
    if not content and not tool_calls:
        raise RuntimeError("LLM returned empty content and no tool_calls")
    return ChatToolsResult(
        content=content,
        tool_calls=tool_calls,
        raw_message=message,
        reasoning=reasoning,
    )


def supports_vision(model_id: str) -> bool:
    """判断模型是否支持多模态（图片）输入。"""
    return model_id in VISION_CAPABLE_MODELS


def build_vision_user_content(
    text: str,
    image_urls: list[str] | None = None,
    *,
    video_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """构建多模态 user message content（文本 + 图片/视频 URL 块）。"""
    parts: list[dict[str, Any]] = []
    if text.strip():
        parts.append({"type": "text", "text": text.strip()})
    for url in image_urls or []:
        cleaned = url.strip()
        if cleaned:
            parts.append({"type": "image_url", "image_url": {"url": cleaned}})
    for url in video_urls or []:
        cleaned = url.strip()
        if cleaned:
            parts.append({"type": "video_url", "video_url": {"url": cleaned}})
    return parts
