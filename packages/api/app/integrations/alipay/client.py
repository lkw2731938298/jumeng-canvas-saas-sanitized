"""支付宝 RSA2 签名、当面付预下单与查单。"""

from __future__ import annotations

import base64
import json
import logging
import urllib.parse
from datetime import datetime
from functools import lru_cache
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from ...core.config import get_settings
from ...core.datetime_util import CST

logger = logging.getLogger(__name__)


def _normalize_key_material(raw: str) -> str:
    """清理 env 中的密钥：去引号、空白与字面 \\n。"""
    text = (raw or "").strip().strip('"').strip("'")
    if "BEGIN" in text:
        return text.replace("\\n", "\n")
    return "".join(text.split())


def _wrap_pem_body(raw: str, label: str) -> str:
    """将单行 Base64 密钥包装为 PEM 多行格式。"""
    body = _normalize_key_material(raw)
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----"


def _load_private_key(raw: str) -> RSAPrivateKey:
    """加载应用私钥（支付宝工具生成的 PKCS#8 单行 Base64 或 PEM）。"""
    text = _normalize_key_material(raw)
    if not text:
        raise ValueError("支付宝应用私钥为空")

    if "BEGIN" in text:
        key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("支付宝应用私钥类型无效")
        return key

    der = base64.b64decode(text)
    # 支付宝开放平台工具默认导出 PKCS#8
    try:
        key = serialization.load_der_private_key(der, password=None)
        if isinstance(key, RSAPrivateKey):
            return key
    except Exception:
        pass

    for label in ("PRIVATE KEY", "RSA PRIVATE KEY"):
        try:
            pem = _wrap_pem_body(text, label).encode("utf-8")
            key = serialization.load_pem_private_key(pem, password=None)
            if isinstance(key, RSAPrivateKey):
                return key
        except Exception:
            continue
    raise ValueError("无法解析支付宝应用私钥")


def _load_public_key(raw: str) -> RSAPublicKey:
    """加载支付宝平台公钥（非应用公钥）。"""
    text = _normalize_key_material(raw)
    if not text:
        raise ValueError("支付宝平台公钥为空")

    if "BEGIN" in text:
        key = serialization.load_pem_public_key(text.encode("utf-8"))
    else:
        try:
            der = base64.b64decode(text)
            key = serialization.load_der_public_key(der)
        except Exception:
            pem = _wrap_pem_body(text, "PUBLIC KEY").encode("utf-8")
            key = serialization.load_pem_public_key(pem)
    if not isinstance(key, RSAPublicKey):
        raise ValueError("支付宝公钥类型无效")
    return key


def _canonical_sign_content(params: dict[str, str], *, for_verify: bool = False) -> str:
    """拼接待签名字符串。

    请求签名：除 sign 外所有非空参数均参与（含 sign_type），与网关验签一致。
    异步通知验签：剔除 sign、sign_type。
    """
    excluded = {"sign"} if not for_verify else {"sign", "sign_type"}
    items = sorted(
        (k, v)
        for k, v in params.items()
        if k not in excluded and v is not None and str(v) != ""
    )
    return "&".join(f"{k}={v}" for k, v in items)


def _rsa2_sign(content: str, private_key: RSAPrivateKey) -> str:
    signature = private_key.sign(
        content.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def _rsa2_verify(content: str, signature_b64: str, public_key: RSAPublicKey) -> bool:
    try:
        public_key.verify(
            base64.b64decode(signature_b64),
            content.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def alipay_configured() -> bool:
    """是否已配置支付宝正式/沙箱参数。"""
    s = get_settings()
    return bool(
        s.alipay_app_id.strip()
        and s.alipay_private_key.strip()
        and s.alipay_public_key.strip()
        and s.alipay_gateway.strip()
        and s.alipay_notify_url.strip()
    )


class AlipayClient:
    """支付宝网关客户端（当面付扫码）。"""

    def __init__(
        self,
        *,
        app_id: str,
        private_key: RSAPrivateKey,
        public_key: RSAPublicKey,
        gateway: str,
        notify_url: str,
    ) -> None:
        self.app_id = app_id
        self._private_key = private_key
        self._public_key = public_key
        self.gateway = gateway.rstrip("/")
        self.notify_url = notify_url

    def _signed_params(self, method: str, biz_content: dict[str, Any]) -> dict[str, str]:
        params: dict[str, str] = {
            "app_id": self.app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": self.notify_url,
            "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
        }
        content = _canonical_sign_content(params, for_verify=False)
        params["sign"] = _rsa2_sign(content, self._private_key)
        logger.debug("Alipay sign string length=%s method=%s", len(content), method)
        return params

    async def _post(self, method: str, biz_content: dict[str, Any]) -> dict[str, Any]:
        from ...common.utils.file_log import write_file_log

        params = self._signed_params(method, biz_content)
        out_trade_no = str(biz_content.get("out_trade_no") or "").strip() or None
        log_extra: dict[str, Any] = {"gateway": self.gateway, "method": method}
        if out_trade_no:
            log_extra["out_trade_no"] = out_trade_no

        # 原文：与 POST body 一致的 application/x-www-form-urlencoded 字符串
        post_body = urllib.parse.urlencode(params)
        write_file_log("payment", post_body, tag="SUBMIT", extra=log_extra)

        status_code = 0
        raw_text = ""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.gateway,
                    data=params,
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
                )
                status_code = response.status_code
                raw_text = response.text
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            write_file_log(
                "payment",
                raw_text or str(exc),
                tag="RESPONSE",
                extra={**log_extra, "http_status": status_code or None},
            )
            raise

        # 原文：支付宝网关 HTTP 响应体
        write_file_log(
            "payment",
            raw_text,
            tag="RESPONSE",
            extra={**log_extra, "http_status": status_code},
        )

        response_key = method.replace(".", "_") + "_response"
        body = payload.get(response_key) or {}
        if not isinstance(body, dict):
            logger.warning("Alipay unexpected response for %s: %s", method, payload)
            return {"code": "INVALID_RESPONSE", "msg": "支付宝响应格式异常", "raw": payload}

        if body.get("code") != "10000":
            logger.warning("Alipay %s failed: %s", method, body)
        return body

    async def trade_precreate(
        self,
        *,
        out_trade_no: str,
        total_amount_yuan: str,
        subject: str,
        timeout_express: str = "15m",
    ) -> dict[str, Any]:
        """当面付预下单，返回 qr_code 等字段。"""
        return await self._post(
            "alipay.trade.precreate",
            {
                "out_trade_no": out_trade_no,
                "total_amount": total_amount_yuan,
                "subject": subject,
                "timeout_express": timeout_express,
            },
        )

    async def trade_query(self, *, out_trade_no: str) -> dict[str, Any]:
        """官方查单（加算力前的唯一支付成功依据）。"""
        return await self._post(
            "alipay.trade.query",
            {"out_trade_no": out_trade_no},
        )

    def verify_notify(self, form_data: dict[str, str]) -> bool:
        """验签支付宝异步通知参数。"""
        sign = form_data.get("sign")
        if not sign:
            return False
        content = _canonical_sign_content({k: str(v) for k, v in form_data.items()}, for_verify=True)
        return _rsa2_verify(content, sign, self._public_key)

    @staticmethod
    def fen_to_yuan_str(pay_amount_fen: int) -> str:
        """分 → 支付宝 total_amount 字符串（元，两位小数）。"""
        return f"{pay_amount_fen / 100:.2f}"

    @staticmethod
    def parse_notify_form(raw_body: bytes) -> dict[str, str]:
        """解析 application/x-www-form-urlencoded 通知体。"""
        parsed = urllib.parse.parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
        return {k: v[0] if v else "" for k, v in parsed.items()}


@lru_cache
def get_alipay_client() -> AlipayClient | None:
    if not alipay_configured():
        return None
    s = get_settings()
    try:
        return AlipayClient(
            app_id=s.alipay_app_id.strip(),
            private_key=_load_private_key(s.alipay_private_key),
            public_key=_load_public_key(s.alipay_public_key),
            gateway=s.alipay_gateway.strip(),
            notify_url=s.alipay_notify_url.strip(),
        )
    except Exception as exc:
        logger.error("Alipay client init failed: %s", exc)
        return None
