"""画布项目级 OSS 存储：文本/媒体/工作流节点对象，支持本地 mirror 回退。"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from ...core.config import get_settings
from ...core.paths import find_project_root, web_public_dir
from ...services.project_scope import project_id_folder
from ...services.storage_urls import storage_object_url
from .service import get_oss

logger = logging.getLogger(__name__)


class StorageWriteError(RuntimeError):
    """存储写入或校验失败时抛出。"""


PROJECT_ROOT = find_project_root(Path(__file__))


@dataclass
class StoredObject:
    """已持久化对象的 OSS key、访问 URL 与字节大小。"""
    oss_key: str
    file_url: str
    size: int


class CanvasOssStorage:
    """画布项目级对象存储门面：优先 OSS，开发环境可仅写本地 mirror。"""
    def __init__(self) -> None:
        self._oss = get_oss()
        self._local_root = PROJECT_ROOT / "data" / "oss-local"
        self._uploads_root = web_public_dir(PROJECT_ROOT) / "uploads"

    @property
    def configured(self) -> bool:
        """是否已配置可用的 OSS Bucket。"""
        return self._oss.bucket is not None

    def _writes_local_only(self) -> bool:
        return bool(get_settings().canvas_storage_local_only)

    def project_key(
        self,
        project_id: str,
        *parts: str,
        storage_folder: str | None = None,
    ) -> str:
        settings = get_settings()
        prefix = (settings.oss_object_prefix or "canvas").rstrip("/")
        pid = project_id_folder(project_id, storage_folder)
        segments = [prefix, "projects", pid, *parts]
        key = "/".join(segments)
        if ".." in key:
            raise ValueError("Invalid storage path")
        return key

    def platform_key(self, *parts: str) -> str:
        settings = get_settings()
        prefix = (settings.oss_object_prefix or "canvas").rstrip("/")
        segments = [prefix, "platform", *parts]
        key = "/".join(segments)
        if ".." in key:
            raise ValueError("Invalid storage path")
        return key

    def _put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject:
        if self._writes_local_only():
            self._write_local_mirror(key, data)
            logger.info("Local storage put %s (%d bytes)", key, len(data))
        elif not self._oss.bucket:
            raise StorageWriteError(
                "OSS is not configured (check OSS_ACCESS_KEY_ID / oss2); "
                "refusing local-only write when CANVAS_STORAGE_LOCAL_ONLY=false"
            )
        else:
            self._put_oss_verified(key, data, content_type)
            logger.info("OSS put %s (%d bytes)", key, len(data))
        url = storage_object_url(key)
        return StoredObject(oss_key=key, file_url=url, size=len(data))

    def put(
        self,
        project_id: str,
        rel_path: str,
        data: bytes,
        content_type: str,
        *,
        storage_folder: str | None = None,
    ) -> StoredObject:
        """写入项目作用域对象并返回存储元数据。"""
        key = self.project_key(project_id, rel_path, storage_folder=storage_folder)
        return self._put_bytes(key, data, content_type)

    def put_platform(self, rel_path: str, data: bytes, content_type: str) -> StoredObject:
        key = self.platform_key(rel_path)
        return self._put_bytes(key, data, content_type)

    def _put_oss_verified(self, oss_key: str, data: bytes, content_type: str) -> None:
        if not self._oss.bucket:
            raise StorageWriteError("OSS is not configured")
        try:
            self._oss.bucket.put_object(oss_key, data, headers={"Content-Type": content_type})
            if not self._oss.exists(oss_key):
                raise StorageWriteError(f"OSS put verification failed for {oss_key}")
        except StorageWriteError:
            raise
        except Exception as exc:
            raise StorageWriteError(f"OSS put failed for {oss_key}: {exc}") from exc

    def _write_local_mirror(self, oss_key: str, data: bytes) -> None:
        local_path = self._local_root / oss_key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)

    def _read_local_bytes(self, oss_key: str) -> bytes:
        local_path = self._local_root / oss_key
        if local_path.is_file():
            return local_path.read_bytes()
        return b""

    def ensure_local_object(self, oss_key: str) -> bool:
        """One-time migration: copy an OSS object into the local mirror."""
        if self._read_local_bytes(oss_key):
            return True
        if not self._oss.bucket:
            return False
        try:
            data = self._oss.download_bytes(oss_key)
            if not data:
                return False
            self._write_local_mirror(oss_key, data)
            logger.info("Mirrored OSS → local %s (%d bytes)", oss_key, len(data))
            return True
        except Exception as exc:
            logger.error("Mirror OSS → local failed for %s: %s", oss_key, exc)
            return False

    def get_local_path(self, oss_key: str) -> Path | None:
        """Return local mirror path when the object exists on disk."""
        local_path = self._local_root / oss_key
        if local_path.is_file():
            return local_path
        return None

    def iter_local_chunks(self, oss_key: str, chunk_size: int = 65536):
        """Stream bytes from the local mirror in fixed-size chunks."""
        local_path = self.get_local_path(oss_key)
        if not local_path:
            return
        with local_path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def _alternate_oss_keys(self, oss_key: str) -> list[str]:
        """Try legacy canvas/ ↔ Ihuabu/ ↔ configured prefix when object was stored under another key."""
        settings = get_settings()
        configured = (settings.oss_object_prefix or "canvas").rstrip("/")
        legacy_prefixes = ("canvas", "Ihuabu")
        keys: list[str] = []
        for prefix in dict.fromkeys((configured, *legacy_prefixes)):
            if oss_key.startswith(f"{prefix}/"):
                keys.append(oss_key)
                for alt_prefix in dict.fromkeys((configured, *legacy_prefixes)):
                    if alt_prefix != prefix:
                        keys.append(oss_key.replace(f"{prefix}/", f"{alt_prefix}/", 1))
                break
        if not keys:
            keys = [oss_key]
        return list(dict.fromkeys(keys))

    def get_bytes(self, oss_key: str) -> bytes:
        """按 OSS key 读取对象字节（含本地 mirror 与历史前缀回退）。"""
        if self._writes_local_only():
            for key in self._alternate_oss_keys(oss_key):
                data = self._read_local_bytes(key)
                if data:
                    return data
            return b""
        if self._oss.bucket:
            for key in self._alternate_oss_keys(oss_key):
                try:
                    data = self._oss.download_bytes(key)
                    if data:
                        return data
                except Exception as exc:
                    logger.debug("OSS get miss for %s: %s", key, exc)
        return b""

    def get_object_size(self, oss_key: str) -> int:
        """对象字节长度；stream / Range 响应需要 Content-Length。"""
        if self._writes_local_only():
            for key in self._alternate_oss_keys(oss_key):
                path = self.get_local_path(key)
                if path:
                    return path.stat().st_size
            return 0
        if self._oss.bucket:
            for key in self._alternate_oss_keys(oss_key):
                try:
                    size = self._oss.object_content_length(key)
                    if size > 0:
                        return size
                except Exception as exc:
                    logger.debug("OSS size miss for %s: %s", key, exc)
        return 0

    def iter_chunks(self, oss_key: str, chunk_size: int = 65536):
        """流式读取整对象（本地 mirror 或 OSS）。"""
        if self._writes_local_only():
            for key in self._alternate_oss_keys(oss_key):
                if self.get_local_path(key):
                    yield from self.iter_local_chunks(key, chunk_size)
                    return
            return
        if self._oss.bucket:
            for key in self._alternate_oss_keys(oss_key):
                try:
                    yielded = False
                    for chunk in self._oss.iter_object_chunks(key, chunk_size):
                        yielded = True
                        yield chunk
                    if yielded:
                        return
                except Exception as exc:
                    logger.debug("OSS iter miss for %s: %s", key, exc)

    def iter_byte_range(
        self, oss_key: str, start: int, end: int, chunk_size: int = 65536
    ):
        """流式读取闭区间字节段，供 HTTP 206 Partial Content。"""
        if self._writes_local_only():
            for key in self._alternate_oss_keys(oss_key):
                path = self.get_local_path(key)
                if path:
                    yield from _iter_local_path_byte_range(path, start, end, chunk_size)
                    return
            return
        if self._oss.bucket:
            for key in self._alternate_oss_keys(oss_key):
                try:
                    yielded = False
                    for chunk in self._oss.iter_object_byte_range(
                        key, start, end, chunk_size
                    ):
                        yielded = True
                        yield chunk
                    if yielded:
                        return
                except Exception as exc:
                    logger.debug("OSS range miss for %s: %s", key, exc)

    def object_exists(self, oss_key: str) -> bool:
        if self._writes_local_only():
            for key in self._alternate_oss_keys(oss_key):
                if self._read_local_bytes(key):
                    return True
            return False
        if self._oss.bucket:
            for key in self._alternate_oss_keys(oss_key):
                try:
                    if self._oss.exists(key):
                        return True
                except Exception as exc:
                    logger.debug("OSS exists miss for %s: %s", key, exc)
        return False

    def resolve_existing_key(self, oss_key: str) -> str | None:
        """Return the first alternate key that exists in storage."""
        if self._writes_local_only():
            for key in self._alternate_oss_keys(oss_key):
                if self._read_local_bytes(key):
                    return key
            return None
        if self._oss.bucket:
            for key in self._alternate_oss_keys(oss_key):
                try:
                    if self._oss.exists(key):
                        return key
                except Exception as exc:
                    logger.debug("OSS exists miss for %s: %s", key, exc)
        return None

    def ensure_oss_object(self, oss_key: str, *, content_type: str = "application/octet-stream") -> bool:
        """Backfill OSS from local mirror when the object is missing upstream."""
        if self._writes_local_only() or not self._oss.bucket:
            return self._read_local_bytes(oss_key) != b""
        if self._oss.exists(oss_key):
            return True
        data = self._read_local_bytes(oss_key)
        if not data:
            return False
        try:
            self._put_oss_verified(oss_key, data, content_type)
        except StorageWriteError as exc:
            logger.error("Backfill failed for %s: %s", oss_key, exc)
            return False
        logger.info("Backfilled OSS object %s (%d bytes)", oss_key, len(data))
        return True

    def delete(self, oss_key: str) -> None:
        """删除单个 OSS 对象（及本地 mirror）。"""
        if self._oss.bucket and not self._writes_local_only():
            self._oss.delete(oss_key)
        local_path = self._local_root / oss_key
        if local_path.is_file():
            local_path.unlink()

    def delete_project_tree(self, project_id: str, storage_folder: str | None = None) -> None:
        """删除项目下全部存储对象（项目 purge 用）。"""
        prefix = self.project_key(project_id, "", storage_folder=storage_folder).rstrip("/")
        local_dir = self._local_root / prefix
        if local_dir.is_dir():
            shutil.rmtree(local_dir, ignore_errors=True)
            logger.info("Removed local project storage tree %s", local_dir)

    def read_json(
        self,
        project_id: str,
        rel_path: str,
        *,
        storage_folder: str | None = None,
    ) -> list | dict | None:
        """读取项目内 JSON 文件并解析为 Python 对象。"""
        key = self.project_key(project_id, rel_path, storage_folder=storage_folder)
        raw = self.get_bytes(key)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def write_json(
        self,
        project_id: str,
        rel_path: str,
        payload: list | dict,
        *,
        storage_folder: str | None = None,
    ) -> StoredObject:
        """将 Python 对象序列化为 JSON 并写入项目存储。"""
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return self.put(
            project_id,
            rel_path,
            data,
            "application/json; charset=utf-8",
            storage_folder=storage_folder,
        )

    def read_platform_json(self, rel_path: str) -> list | dict | None:
        """读取平台级 JSON（跨项目共享，如 prompt 配置）。"""
        key = self.platform_key(rel_path)
        raw = self.get_bytes(key)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def write_platform_json(self, rel_path: str, payload: list | dict) -> StoredObject:
        """写入平台级 JSON 对象。"""
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return self.put_platform(rel_path, data, "application/json; charset=utf-8")


def _iter_local_path_byte_range(
    path: Path, start: int, end: int, chunk_size: int = 65536
):
    """仅流式读取本地文件指定字节段。"""
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


_storage: CanvasOssStorage | None = None


def get_canvas_storage() -> CanvasOssStorage:
    """获取全局单例 CanvasOssStorage 实例。"""
    global _storage
    if _storage is None:
        _storage = CanvasOssStorage()
    return _storage


def is_oss_flow_pointer(flow_json: str) -> bool:
    """判断 flow_json 是否为指向 OSS 大对象的存储指针。"""
    try:
        data = json.loads(flow_json)
        return isinstance(data, dict) and data.get("_storage") == "oss" and bool(data.get("ossKey"))
    except json.JSONDecodeError:
        return False


def resolve_flow_json(flow_json: str, storage: CanvasOssStorage | None = None) -> str:
    """解析 flow_json：若为 OSS 指针则从存储读取实际 JSON 文本。"""
    if not is_oss_flow_pointer(flow_json):
        return flow_json
    store = storage or get_canvas_storage()
    ptr = json.loads(flow_json)
    content = store.get_bytes(ptr["ossKey"])
    if not content:
        return "{}"
    return content.decode("utf-8")


def store_flow_json(
    project_id: str,
    workflow_id: str,
    flow_json: str,
    storage: CanvasOssStorage | None = None,
    storage_folder: str | None = None,
) -> str:
    """将工作流 flow_json 写入 OSS 并返回存储指针 JSON 字符串。"""
    store = storage or get_canvas_storage()
    rel = f"workflows/{workflow_id}/nodes.json"
    obj = store.put(
        project_id,
        rel,
        flow_json.encode("utf-8"),
        "application/json; charset=utf-8",
        storage_folder=storage_folder,
    )
    return json.dumps({"_storage": "oss", "ossKey": obj.oss_key}, ensure_ascii=False)


def new_asset_id() -> str:
    """生成新的素材资产 UUID。"""
    return str(uuid.uuid4())
