"""上游服务商调用统一异常类型。"""


class UpstreamError(Exception):
    """上游 API 调用失败时抛出的业务异常，携带稳定错误码与可读消息。"""

    def __init__(self, message: str, *, code: str = "UPSTREAM_ERROR") -> None:
        self.code = code
        self.message = message
        super().__init__(message)
