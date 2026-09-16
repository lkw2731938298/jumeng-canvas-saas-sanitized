"""Tests for SEC-042 unified API error handlers."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.error_codes import ErrorCode
from app.core.errors import AppError, fail, register_exception_handlers


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/app-error")
    def raise_app_error():
        fail(ErrorCode.UNAUTHORIZED)

    @app.get("/legacy-dict")
    def raise_legacy_dict():
        raise HTTPException(
            status_code=429,
            detail={
                "code": "SMS_COOLDOWN",
                "message": "验证码发送过于频繁，请稍后再试",
                "waitSeconds": 42,
            },
        )

    @app.get("/legacy-string")
    def raise_legacy_string():
        raise HTTPException(status_code=404, detail="资源不存在")

    return app


def test_app_error_handler_shape():
    client = TestClient(_make_app())
    res = client.get("/app-error")
    assert res.status_code == 401
    body = res.json()
    assert body["code"] == "UNAUTHORIZED"
    assert body["message"] == "未登录或登录已失效，请重新登录"
    assert "content" not in body


def test_http_exception_dict_detail():
    client = TestClient(_make_app())
    res = client.get("/legacy-dict")
    assert res.status_code == 429
    body = res.json()
    assert body["code"] == "SMS_COOLDOWN"
    assert body["content"]["waitSeconds"] == 42


def test_http_exception_string_detail():
    client = TestClient(_make_app())
    res = client.get("/legacy-string")
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "资源不存在"


def test_app_error_direct():
    err = AppError(ErrorCode.PRICING_CHANGED, content={"total": 10})
    assert err.code == "PRICING_CHANGED"
    assert err.http_status == 409
    assert err.content == {"total": 10}
