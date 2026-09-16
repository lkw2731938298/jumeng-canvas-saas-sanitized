"""登录弹窗法务文档：用户协议 / 隐私政策（支持 .md / .docx 上传至 OSS）。"""

from __future__ import annotations

import io
import logging
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..core.datetime_util import cst_iso_now
from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage
from .document_page_count import assert_document_page_limit

logger = logging.getLogger(__name__)

LEGAL_DOC_MAX_BYTES = 2 * 1024 * 1024
LEGAL_DOC_TYPES = {
    "user_agreement": {"title": "用户协议", "slug": "user-agreement"},
    "privacy_policy": {"title": "隐私政策", "slug": "privacy-policy"},
}
_ALLOWED_EXT = {".md", ".docx"}
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def normalize_legal_doc_type(raw: str) -> str | None:
    """将 API 路径 slug 或内部 key 规范化为 user_agreement / privacy_policy。"""
    key = str(raw or "").strip().lower().replace("-", "_")
    aliases = {
        "user_agreement": "user_agreement",
        "useragreement": "user_agreement",
        "privacy_policy": "privacy_policy",
        "privacypolicy": "privacy_policy",
    }
    if key in LEGAL_DOC_TYPES:
        return key
    slug_map = {meta["slug"].replace("-", "_"): doc_key for doc_key, meta in LEGAL_DOC_TYPES.items()}
    slug_map.update({meta["slug"]: doc_key for doc_key, meta in LEGAL_DOC_TYPES.items()})
    return aliases.get(key) or slug_map.get(key)


def normalize_legal_documents(raw: Any) -> dict[str, dict[str, Any]]:
    """规范化 platform_settings.legal_documents JSON。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for doc_key in LEGAL_DOC_TYPES:
        entry = raw.get(doc_key)
        if not isinstance(entry, dict):
            continue
        markdown_key = str(entry.get("markdownOssKey") or entry.get("markdown_oss_key") or "").strip()
        if not markdown_key:
            continue
        source_format = str(entry.get("sourceFormat") or entry.get("source_format") or "md").strip().lower()
        if source_format not in {"md", "docx"}:
            source_format = "md"
        out[doc_key] = {
            "title": str(entry.get("title") or LEGAL_DOC_TYPES[doc_key]["title"]).strip()
            or LEGAL_DOC_TYPES[doc_key]["title"],
            "sourceFormat": source_format,
            "sourceOssKey": str(entry.get("sourceOssKey") or entry.get("source_oss_key") or "").strip(),
            "markdownOssKey": markdown_key,
            "updatedAt": str(entry.get("updatedAt") or entry.get("updated_at") or "").strip(),
        }
    return out


def serialize_legal_documents_for_admin(raw: Any) -> dict[str, dict[str, Any]]:
    """管理端摘要：不含正文，仅配置状态。"""
    docs = normalize_legal_documents(raw)
    result: dict[str, dict[str, Any]] = {}
    for doc_key, meta in LEGAL_DOC_TYPES.items():
        entry = docs.get(doc_key)
        if not entry:
            result[_camel_doc_key(doc_key)] = {
                "configured": False,
                "title": meta["title"],
                "sourceFormat": "",
                "updatedAt": None,
            }
            continue
        updated_at = entry.get("updatedAt") or None
        result[_camel_doc_key(doc_key)] = {
            "configured": True,
            "title": entry["title"],
            "sourceFormat": entry["sourceFormat"],
            "updatedAt": updated_at,
        }
    return result


def _camel_doc_key(doc_key: str) -> str:
    if doc_key == "user_agreement":
        return "userAgreement"
    if doc_key == "privacy_policy":
        return "privacyPolicy"
    return doc_key


def _decode_text(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _docx_to_markdown(data: bytes) -> str:
    """从 docx 提取段落文本，转为简易 Markdown（双换行分段）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("无效的 Word 文档")
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for p in root.iter(f"{_W_NS}p"):
        parts: list[str] = []
        for node in p.iter(f"{_W_NS}t"):
            if node.text:
                parts.append(node.text)
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    if not paragraphs:
        raise ValueError("Word 文档未解析到有效正文")
    return "\n\n".join(paragraphs)


def _normalize_markdown_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("文档内容为空")
    # 压缩连续空行，避免前端渲染过长空白
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized + "\n"


def parse_legal_document(filename: str, data: bytes) -> tuple[str, str]:
    """解析上传文件，返回 (source_format, markdown_text)。"""
    if not data:
        raise ValueError("文件为空")
    if len(data) > LEGAL_DOC_MAX_BYTES:
        raise ValueError("文档不能超过 2MB")
    ext = Path(filename or "").suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError("仅支持 .md 或 .docx 文件")
    assert_document_page_limit(data, filename)
    if ext == ".md":
        markdown = _normalize_markdown_text(_decode_text(data))
        return "md", markdown
    markdown = _normalize_markdown_text(_docx_to_markdown(data))
    return "docx", markdown


def upload_legal_document(
    doc_type: str,
    filename: str,
    data: bytes,
) -> dict[str, Any]:
    """上传法务文档：源文件 + 规范化 Markdown 均写入平台 OSS。"""
    normalized_type = normalize_legal_doc_type(doc_type)
    if not normalized_type:
        raise ValueError("未知的文档类型")
    source_format, markdown = parse_legal_document(filename, data)
    meta = LEGAL_DOC_TYPES[normalized_type]
    doc_id = uuid.uuid4().hex
    ext = Path(filename or "").suffix.lower() or (".md" if source_format == "md" else ".docx")
    slug = meta["slug"]

    storage = get_canvas_storage()
    source_mime = (
        "text/markdown"
        if ext == ".md"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    try:
        source_stored = storage.put_platform(
            f"homepage/legal/{slug}/{doc_id}-source{ext}",
            data,
            source_mime,
        )
        md_stored = storage.put_platform(
            f"homepage/legal/{slug}/{doc_id}.md",
            markdown.encode("utf-8"),
            "text/markdown; charset=utf-8",
        )
    except StorageWriteError as exc:
        raise ValueError(str(exc)) from exc

    updated_at = cst_iso_now()
    return {
        "docKey": normalized_type,
        "title": meta["title"],
        "sourceFormat": source_format,
        "sourceOssKey": source_stored.oss_key,
        "markdownOssKey": md_stored.oss_key,
        "updatedAt": updated_at,
    }


def load_legal_document_markdown(entry: dict[str, Any] | None) -> str:
    """从 OSS 读取已配置的 Markdown 正文。"""
    if not entry:
        return ""
    key = str(entry.get("markdownOssKey") or "").strip()
    if not key:
        return ""
    storage = get_canvas_storage()
    raw = storage.get_bytes(key)
    if not raw:
        logger.warning("legal document markdown missing key=%s", key)
        return ""
    return _decode_text(raw).strip()


def build_legal_document_entry(uploaded: dict[str, Any]) -> dict[str, Any]:
    """构造写入 platform_settings.legal_documents 的单项。"""
    return {
        "title": uploaded["title"],
        "sourceFormat": uploaded["sourceFormat"],
        "sourceOssKey": uploaded["sourceOssKey"],
        "markdownOssKey": uploaded["markdownOssKey"],
        "updatedAt": uploaded["updatedAt"],
    }
