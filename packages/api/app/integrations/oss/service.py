from ...core.datetime_util import now_cst_naive
"""
Alibaba Cloud OSS integration for storing generated assets.
Gracefully degrades when oss2 is not installed (local dev mode).
"""

import uuid, logging
from datetime import datetime
from ...core.config import get_settings

logger = logging.getLogger(__name__)


def _ensure_https_url(url: str) -> str:
    """oss2 sign_url may return http:// on public endpoints; browsers on HTTPS require https://."""
    if url.startswith("http://") and ".aliyuncs.com" in url:
        return "https://" + url[7:]
    return url


try:
    import oss2
    HAS_OSS2 = True
except ImportError:
    HAS_OSS2 = False


class OssService:
    """OSS object storage service. Falls back to no-op when oss2 unavailable."""

    def __init__(self):
        settings = get_settings()
        self._bucket_name = settings.oss_bucket
        self._endpoint = settings.oss_endpoint
        self._prefix = settings.oss_object_prefix.rstrip("/")
        self._auth = None
        self._bucket = None
        if HAS_OSS2 and settings.oss_access_key_id:
            self._auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)

    @property
    def bucket(self):
        if not HAS_OSS2 or not self._auth:
            return None
        if self._bucket is None:
            self._bucket = oss2.Bucket(self._auth, self._endpoint, self._bucket_name)
        return self._bucket

    def _object_key(self, user_id: str, category: str, filename: str) -> str:
        date_str = now_cst_naive().strftime("%Y/%m/%d")
        uid = str(user_id).replace("-", "")[:12]
        return f"{self._prefix}/{uid}/{category}/{date_str}/{uuid.uuid4().hex[:8]}_{filename}"

    def upload_bytes(self, data: bytes, user_id: str, category: str, filename: str, content_type: str = "image/png") -> str:
        key = self._object_key(user_id, category, filename)
        if self.bucket:
            self.bucket.put_object(key, data, headers={"Content-Type": content_type})
            logger.info(f"Uploaded: {key} ({len(data)} bytes)")
        else:
            logger.warning(f"OSS not configured, skipping upload: {key}")
        return key

    def upload_file(self, filepath: str, user_id: str, category: str, filename: str, content_type: str = "image/png") -> str:
        with open(filepath, "rb") as f:
            return self.upload_bytes(f.read(), user_id, category, filename, content_type)

    def download_bytes(self, key: str) -> bytes:
        if self.bucket:
            return self.bucket.get_object(key).read()
        return b""

    def object_content_length(self, key: str) -> int:
        """读取对象 Content-Length，供 stream 响应与 Range 校验。"""
        if not self.bucket:
            return 0
        meta = self.bucket.get_object_meta(key)
        raw = meta.headers.get("Content-Length") or meta.headers.get("content-length") or 0
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    def iter_object_chunks(self, key: str, chunk_size: int = 65536):
        """流式读取整对象，避免大视频一次性进内存。"""
        if not self.bucket:
            return
        result = self.bucket.get_object(key)
        while True:
            chunk = result.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def iter_object_byte_range(
        self, key: str, start: int, end: int, chunk_size: int = 65536
    ):
        """流式读取闭区间字节段；Chrome 视频 seek 依赖 206 + Range。"""
        if not self.bucket:
            return
        # oss2 byte_range 为闭区间 [start, end]
        result = self.bucket.get_object(key, byte_range=(start, end))
        while True:
            chunk = result.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def download_to_file(self, key: str, filepath: str) -> None:
        if self.bucket:
            self.bucket.get_object_to_file(key, filepath)

    def presign_get(
        self,
        key: str,
        expires_seconds: int = 3600,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        if not self.bucket:
            return key
        # ECS 读写可走内网 Endpoint；浏览器签名 URL 须用公网域名 + HTTPS（见 ai_read/服务器资源）
        # params 可带 x-oss-process（登录网格缩略等）
        sign_kwargs: dict = {}
        if params:
            sign_kwargs["params"] = params
        endpoint = (self._endpoint or "").replace("-internal", "")
        if endpoint != self._endpoint and self._auth:
            pub_bucket = oss2.Bucket(self._auth, endpoint, self._bucket_name)
            return _ensure_https_url(
                pub_bucket.sign_url("GET", key, expires_seconds, **sign_kwargs)
            )
        return _ensure_https_url(
            self.bucket.sign_url("GET", key, expires_seconds, **sign_kwargs)
        )

    def presign_put(self, user_id: str, category: str, filename: str, expires_seconds: int = 600, content_type: str = "image/png") -> dict:
        key = self._object_key(user_id, category, filename)
        url = ""
        if self.bucket:
            url = _ensure_https_url(
                self.bucket.sign_url("PUT", key, expires_seconds, headers={"Content-Type": content_type})
            )
        return {"url": url, "key": key}

    def delete(self, key: str) -> None:
        if self.bucket:
            self.bucket.delete_object(key)

    def exists(self, key: str) -> bool:
        if self.bucket:
            return self.bucket.object_exists(key)
        return False

    def list_object_keys(self, prefix: str, *, max_keys: int = 5000) -> list[str]:
        if not self.bucket or not HAS_OSS2:
            return []
        keys: list[str] = []
        for obj in oss2.ObjectIterator(self.bucket, prefix=prefix):
            keys.append(obj.key)
            if len(keys) >= max_keys:
                break
        return keys

    def get_public_url(self, key: str) -> str:
        return f"https://{self._bucket_name}.{self._endpoint}/{key}"


_oss_service: OssService | None = None

def get_oss() -> OssService:
    global _oss_service
    if _oss_service is None:
        _oss_service = OssService()
    return _oss_service
