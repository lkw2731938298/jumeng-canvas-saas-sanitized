"""Error code registry tests."""

from app.core.error_codes import (
    format_upstream_job_error_message,
    get_error_message_zh,
    normalize_error_code,
    resolve_error_message,
)


def test_normalize_error_code():
    assert normalize_error_code("revision_conflict") == "REVISION_CONFLICT"
    assert normalize_error_code("PRICING_CHANGED") == "PRICING_CHANGED"


def test_get_error_message_zh():
    assert get_error_message_zh("TIMEOUT") == "上游生成任务超时"
    assert get_error_message_zh("UNKNOWN", fallback="自定义") == "自定义"


def test_format_upstream_job_error_message_prefers_detail():
    detail = "format mp4 is not supported. Supported formats: ['jpeg', 'jpg', 'png', 'bmp', 'webp']"
    assert (
        format_upstream_job_error_message(code="TASK_FAILED", message=detail)
        == detail
    )
    assert (
        format_upstream_job_error_message(code="TASK_FAILED", message="")
        == "上游生成任务失败"
    )


def test_resolve_error_message_from_detail():
    msg = resolve_error_message({"code": "PRICING_CHANGED", "message": "旧文案"})
    assert msg == "价格已更新，请确认后重试"
