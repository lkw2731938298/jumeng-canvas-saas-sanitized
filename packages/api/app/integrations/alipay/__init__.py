"""支付宝开放平台集成（当面付扫码）。"""

from .client import AlipayClient, alipay_configured, get_alipay_client

__all__ = ["AlipayClient", "alipay_configured", "get_alipay_client"]
