"""SEC-042 unified API error responses: { code, message, content? }."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .error_codes import (
    ErrorCode,
    get_error_entry,
    get_error_message_zh,
    normalize_error_code,
    resolve_error_message,
)

_STATUS_FALLBACK_CODES: dict[int, str] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    402: ErrorCode.INSUFFICIENT_CREDITS,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.DOWNLOAD_TOO_LARGE,
    422: ErrorCode.VALIDATION_ERROR,
    423: ErrorCode.PHONE_LOCKED,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL_ERROR,
    502: ErrorCode.UPSTREAM_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
    504: ErrorCode.TIMEOUT,
}


class AppError(Exception):
    """Application error with stable code for clients and monitoring."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        content: dict[str, Any] | list[Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = normalize_error_code(code) or ErrorCode.UNKNOWN
        entry = get_error_entry(self.code)
        self.message = (
            message
            or (entry.message_zh if entry else None)
            or get_error_message_zh(self.code)
            or "操作失败，请稍后重试"
        )
        self.content = content
        self.http_status = http_status or (entry.http_status if entry else None) or 400


def fail(
    code: str,
    *,
    message: str | None = None,
    content: dict[str, Any] | list[Any] | None = None,
    http_status: int | None = None,
) -> NoReturn:
    raise AppError(code, message, content=content, http_status=http_status)


def ok(content: Any = None, *, message: str = "OK") -> dict[str, Any]:
    body: dict[str, Any] = {"code": ErrorCode.OK, "message": message}
    if content is not None:
        body["content"] = content
    return body


def error_response_body(
    code: str,
    message: str,
    *,
    content: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if content is not None:
        body["content"] = content
    return body


def _status_fallback_code(status_code: int) -> str:
    return _STATUS_FALLBACK_CODES.get(status_code, ErrorCode.UNKNOWN)


def _split_detail_payload(detail: dict[str, Any]) -> tuple[str, str, dict[str, Any] | list[Any] | None]:
    code = normalize_error_code(str(detail.get("code") or "")) or ErrorCode.UNKNOWN
    message = resolve_error_message(detail)
    extra = {k: v for k, v in detail.items() if k not in ("code", "message")}
    content: dict[str, Any] | list[Any] | None = extra or None
    return code, message, content


def _http_status_for_code(code: str, fallback: int) -> int:
    entry = get_error_entry(code)
    if entry and entry.http_status is not None:
        return entry.http_status
    return fallback


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response_body(exc.code, exc.message, content=exc.content),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code, message, content = _split_detail_payload(detail)
            status = _http_status_for_code(code, exc.status_code)
            return JSONResponse(
                status_code=status,
                content=error_response_body(code, message, content=content),
            )
        if isinstance(detail, list):
            code = ErrorCode.VALIDATION_ERROR
            message = get_error_message_zh(code) or "请求参数无效"
            return JSONResponse(
                status_code=exc.status_code,
                content=error_response_body(code, message, content={"errors": detail}),
            )
        message = str(detail).strip() if detail else get_error_message_zh(_status_fallback_code(exc.status_code)) or "操作失败"
        code = _status_fallback_code(exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response_body(code, message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        code = ErrorCode.VALIDATION_ERROR
        message = get_error_message_zh(code) or "请求参数无效"
        return JSONResponse(
            status_code=422,
            content=error_response_body(
                code,
                message,
                content={"errors": exc.errors()},
            ),
        )
