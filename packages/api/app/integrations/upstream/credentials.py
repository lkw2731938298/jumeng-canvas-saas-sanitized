from __future__ import annotations

from dataclasses import dataclass

from ...core.llm_keys import get_llm_keys


@dataclass(frozen=True)
class DashScopeCreds:
    api_key: str
    api_base: str


@dataclass(frozen=True)
class ArkCreds:
    api_key: str
    api_base: str


@dataclass(frozen=True)
class ViduCreds:
    api_key: str
    api_base: str


@dataclass(frozen=True)
class RunningHubCreds:
    api_key: str
    api_base: str


def dashscope_creds() -> DashScopeCreds:
    cfg = get_llm_keys()
    base = (cfg.dashscope.api_base or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
    return DashScopeCreds(api_key=cfg.dashscope.api_key, api_base=base)


def kling_creds() -> DashScopeCreds:
    cfg = get_llm_keys()
    base = (cfg.kling.api_base or cfg.dashscope.api_base or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
    return DashScopeCreds(api_key=cfg.kling.api_key, api_base=base)


def ark_creds() -> ArkCreds:
    cfg = get_llm_keys()
    base = (cfg.ark.api_base or cfg.doubao.api_base or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    key = cfg.ark.api_key or cfg.doubao.api_key
    return ArkCreds(api_key=key, api_base=base)


def vidu_creds() -> ViduCreds:
    cfg = get_llm_keys()
    return ViduCreds(api_key=cfg.vidu.api_key, api_base=(cfg.vidu.api_base or "https://api.vidu.cn").rstrip("/"))


def huahu_creds() -> ArkCreds:
    """华狐 AI 聚合平台（New API V1 · Seedance /video/generations）。"""
    cfg = get_llm_keys()
    base = (cfg.huahu.api_base or "https://api.example.com/v1").rstrip("/")
    return ArkCreds(api_key=cfg.huahu.api_key, api_base=base)


def jumengai_creds() -> ArkCreds:
    """聚梦 AI 网关（OpenAI 兼容 /v1/videos）。"""
    cfg = get_llm_keys()
    base = (cfg.jumengai.api_base or "https://api.example.com/v1").rstrip("/")
    return ArkCreds(api_key=cfg.jumengai.api_key, api_base=base)


def runninghub_creds(*, use_ltx: bool = False) -> RunningHubCreds:
    """RunningHub 凭证。

    use_ltx=True 表示走「RunningHub 海外版」（AI 站 www.runninghub.ai）：
    使用 ltx_runninghub 独立密钥与海外 API 地址（全能图片 Pro/G、LTX 图生视频）。
    海外版未配置密钥时回退到 CN 主密钥与 CN 地址，避免直接失败。
    use_ltx=False 走 CN 站（www.runninghub.cn）主密钥与地址（如 MJ 文生图）。
    """
    cfg = get_llm_keys()
    if use_ltx and cfg.ltx_runninghub.api_key:
        base = (cfg.ltx_runninghub.api_base or "https://www.runninghub.ai").rstrip("/")
        return RunningHubCreds(api_key=cfg.ltx_runninghub.api_key, api_base=base)
    base = (cfg.runninghub.api_base or "https://www.runninghub.cn").rstrip("/")
    return RunningHubCreds(api_key=cfg.runninghub.api_key, api_base=base)
