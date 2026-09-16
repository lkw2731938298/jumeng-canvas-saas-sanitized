"""管理端供应商密钥 UI 模板：字段、Profile 中文标签、文档提示。

存库仍用 provider_code + profile_key；本模块只服务运营展示与表单。
"""

from __future__ import annotations

from typing import Any

# 通用字段
_FIELD_API_KEY = {
    "key": "apiKey",
    "label": "API Key",
    "required": True,
    "secret": True,
    "placeholder": "粘贴密钥（保存后不明文展示）",
}
_FIELD_API_BASE = {
    "key": "apiBase",
    "label": "API 地址",
    "required": False,
    "secret": False,
    "advanced": True,
    "placeholder": "留空则用供应商默认地址",
}
_FIELD_ENDPOINT = {
    "key": "endpointId",
    "label": "推理接入点 Endpoint ID",
    "required": False,
    "secret": False,
    "placeholder": "ep-xxxxxxxx（豆包文本等可选）",
    "hint": "仅部分豆包/方舟文本模型需要",
}

_PROFILE_DEFAULT = {"key": "default", "label": "主密钥", "hint": "日常生成默认使用"}
_PROFILE_LTX = {
    "key": "ltx",
    "label": "LTX 专用密钥",
    "hint": "仅 LTX 图生视频等模型使用；未配置时回退主密钥",
}

# 各供应商展示模板（未列出的用默认模板）
_PROVIDER_UI: dict[str, dict[str, Any]] = {
    "doubao": {
        "docsUrl": "https://www.volcengine.com/docs/82379",
        "summary": "火山方舟豆包；文本模型可填 Endpoint ID",
        "fields": [_FIELD_API_KEY, _FIELD_ENDPOINT, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "ark": {
        "docsUrl": "https://www.volcengine.com/docs/82379",
        "summary": "方舟 Ark 图像/视频等",
        "fields": [_FIELD_API_KEY, _FIELD_ENDPOINT, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "dashscope": {
        "docsUrl": "https://help.aliyun.com/zh/model-studio/",
        "summary": "阿里云百炼（通义万相、可灵代理等）",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "kling": {
        "docsUrl": "https://help.aliyun.com/zh/model-studio/",
        "summary": "可灵（经百炼兼容地址）",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "deepseek": {
        "docsUrl": "https://platform.deepseek.com/",
        "summary": "DeepSeek 文本模型",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "vidu": {
        "docsUrl": "https://platform.vidu.com/",
        "summary": "Vidu 视频生成",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "runninghub": {
        "docsUrl": "https://www.runninghub.cn/",
        "summary": "RunningHub CN 站（www.runninghub.cn）：MJ 文生图、Seedance 2.0 sparkvideo / 2.5 Token、MiniMax-H3、分离音频、去字幕；全能图片 Pro/G、LTX、音乐、LLM 走「RunningHub 海外版」",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT, _PROFILE_LTX],
    },
    "ltx_runninghub": {
        "docsUrl": "https://www.runninghub.ai/",
        "summary": "RunningHub 海外版（AI 站）：全能图片 Pro/G、LTX、音乐、LLM（llm.runninghub.ai）",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "nodyhub": {
        "docsUrl": "https://nodyhub.com/",
        "summary": "NodyHub：图片 /v1/images/generations；视频 SD2.0 /v1/videos/generations",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "huahu": {
        "docsUrl": "http://ai.hwdrama.com/docs",
        "summary": "华狐 AI 聚合平台（New API V1 · Seedance /video/generations）",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "jumengai": {
        "docsUrl": "https://docs.example.com/guide/getting-started",
        "summary": "聚梦 AI 网关（OpenAI 兼容 /v1 · 图片 / 视频 / 去字幕）",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "openai": {
        "docsUrl": "https://platform.openai.com/",
        "summary": "OpenAI 兼容接口",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "qwen": {
        "docsUrl": "https://help.aliyun.com/zh/model-studio/",
        "summary": "通义千问（兼容模式）",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "zhipu": {
        "docsUrl": "https://open.bigmodel.cn/",
        "summary": "智谱大模型",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
    "moonshot": {
        "docsUrl": "https://platform.moonshot.cn/",
        "summary": "Moonshot / Kimi",
        "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
        "profiles": [_PROFILE_DEFAULT],
    },
}

_DEFAULT_UI: dict[str, Any] = {
    "docsUrl": "",
    "summary": "上游 API 密钥（加密存储）",
    "fields": [_FIELD_API_KEY, _FIELD_API_BASE],
    "profiles": [_PROFILE_DEFAULT],
}


def provider_ui_schema(provider_code: str) -> dict[str, Any]:
    """返回某供应商的管理端 UI 模板（深拷贝友好的纯 dict）。"""
    code = (provider_code or "").strip().lower()
    base = _PROVIDER_UI.get(code) or _DEFAULT_UI
    return {
        "docsUrl": base.get("docsUrl") or "",
        "summary": base.get("summary") or "",
        "fields": list(base.get("fields") or []),
        "profiles": list(base.get("profiles") or [_PROFILE_DEFAULT]),
    }


def profile_label(provider_code: str, profile_key: str) -> str:
    """profile_key → 中文展示名。"""
    key = (profile_key or "default").strip() or "default"
    for p in provider_ui_schema(provider_code).get("profiles") or []:
        if isinstance(p, dict) and str(p.get("key")) == key:
            return str(p.get("label") or key)
    if key == "default":
        return "主密钥"
    return key


def credential_status(
    *,
    has_credential: bool,
    last_test_ok: bool | None,
) -> str:
    """总览状态：unconfigured | configured | tested_ok | tested_fail。"""
    if not has_credential:
        return "unconfigured"
    if last_test_ok is True:
        return "tested_ok"
    if last_test_ok is False:
        return "tested_fail"
    return "configured"
