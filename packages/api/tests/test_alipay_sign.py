"""支付宝签名单测。"""

from app.integrations.alipay.client import _canonical_sign_content, _rsa2_sign, _load_private_key
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


def test_sign_content_includes_sign_type():
  params = {
    "app_id": "2021006155614244",
    "biz_content": '{"out_trade_no":"JMTEST","total_amount":"79.00","subject":"test","timeout_express":"15m"}',
    "charset": "utf-8",
    "format": "JSON",
    "method": "alipay.trade.precreate",
    "notify_url": "https://legacy-v.example.com/api/v1/admin/recharge-orders/alipay-notify",
    "sign_type": "RSA2",
    "timestamp": "2026-07-09 18:36:45",
    "version": "1.0",
  }
  content = _canonical_sign_content(params, for_verify=False)
  assert "sign_type=RSA2" in content
  assert content.index("app_id=") < content.index("biz_content=")
  assert "sign=" not in content


def test_verify_content_excludes_sign_type():
  params = {
    "app_id": "1",
    "sign_type": "RSA2",
    "sign": "abc",
    "total_amount": "1.00",
  }
  content = _canonical_sign_content(params, for_verify=True)
  assert "sign_type" not in content
  assert "sign=" not in content


def test_rsa2_sign_roundtrip():
  key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  pem = key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
  ).decode()
  loaded = _load_private_key(pem)
  sig = _rsa2_sign("hello", loaded)
  assert isinstance(sig, str) and len(sig) > 50
