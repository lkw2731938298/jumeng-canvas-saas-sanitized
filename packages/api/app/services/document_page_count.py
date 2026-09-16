"""文档页数估算：上传时校验「页数 ≤ 50」（可解析格式强制，无法解析则放行）。"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# 产品约定：单文档页数上限
CANVAS_DOCUMENT_MAX_PAGES = 50

# 纯文本按约 3000 字符一页估算
_TEXT_CHARS_PER_PAGE = 3000

_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page(?!\s*s)\b")
_OOXML_PAGES_RE = re.compile(
    rb"<(?:[A-Za-z0-9_]+:)?Pages(?:\s[^>]*)?>(\d+)</(?:[A-Za-z0-9_]+:)?Pages>",
    re.IGNORECASE,
)


def estimate_document_pages(data: bytes, filename: str | None) -> int | None:
    """估算文档页数；无法可靠解析时返回 None（上传放行，仅依赖大小限制）。"""
    if not data:
        return 0
    ext = Path(filename or "").suffix.lower()
    try:
        if ext == ".pdf":
            return _count_pdf_pages(data)
        if ext == ".docx":
            return _count_docx_pages(data)
        if ext == ".pptx":
            return _count_pptx_slides(data)
        if ext == ".xlsx":
            return _count_xlsx_sheets(data)
        if ext in {".md", ".txt"}:
            return _count_text_pages(data)
        if ext in {".key", ".pages", ".numbers"}:
            return _count_iwork_pages(data, ext)
        # .doc / .xls / .ppt 二进制旧格式：无轻量解析器时不拦页数
        return None
    except Exception:
        return None


def assert_document_page_limit(data: bytes, filename: str | None) -> None:
    """超过页数上限时抛 fail；由调用方捕获前先 import fail。"""
    pages = estimate_document_pages(data, filename)
    if pages is None:
        return
    if pages > CANVAS_DOCUMENT_MAX_PAGES:
        from ..core.error_codes import ErrorCode
        from ..core.errors import fail

        fail(
            ErrorCode.DOCUMENT_TOO_MANY_PAGES,
            message=f"文档不能超过 {CANVAS_DOCUMENT_MAX_PAGES} 页（当前约 {pages} 页）",
            content={"pageCount": pages, "maxPages": CANVAS_DOCUMENT_MAX_PAGES},
        )


def _count_pdf_pages(data: bytes) -> int | None:
    hits = _PDF_PAGE_RE.findall(data)
    if not hits:
        return None
    return len(hits)


def _count_docx_pages(data: bytes) -> int | None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if "docProps/app.xml" in zf.namelist():
            xml = zf.read("docProps/app.xml")
            m = _OOXML_PAGES_RE.search(xml)
            if m:
                return max(1, int(m.group(1)))
        if "word/document.xml" in zf.namelist():
            doc = zf.read("word/document.xml")
            # 显式分页符 + 首页
            breaks = len(re.findall(rb'w:type="page"', doc))
            return max(1, breaks + 1)
    return None


def _count_pptx_slides(data: bytes) -> int | None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slides = [
            n
            for n in zf.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
        ]
        if slides:
            return len(slides)
    return None


def _count_xlsx_sheets(data: bytes) -> int | None:
    """表格以工作表数作为「页」近似，与产品页数上限对齐。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if "xl/workbook.xml" not in zf.namelist():
            return None
        root = ET.fromstring(zf.read("xl/workbook.xml"))
        sheets = [
            el
            for el in root.iter()
            if el.tag.endswith("}sheet") or el.tag == "sheet"
        ]
        if sheets:
            return len(sheets)
    return None


def _count_text_pages(data: bytes) -> int:
    # 去 BOM，按 UTF-8 / 回退 latin-1
    text = data.decode("utf-8", errors="ignore") or data.decode("latin-1", errors="ignore")
    n = max(1, len(text.strip()))
    return max(1, (n + _TEXT_CHARS_PER_PAGE - 1) // _TEXT_CHARS_PER_PAGE)


def _count_iwork_pages(data: bytes, ext: str) -> int | None:
    """Apple iWork 包多为 zip；尽量数预览/幻灯片条目，失败则放行。"""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return None
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if ext == ".key":
            slides = [n for n in names if "/slides/" in n.lower() or n.lower().endswith(".iwa")]
            # Keynote 结构差异大：有 Index/Document.iwa 时无法精确页数
            preview = [n for n in names if "preview" in n.lower() and n.lower().endswith((".jpg", ".png", ".jpeg"))]
            if preview:
                return len(preview)
            return None
        if ext == ".pages":
            preview = [n for n in names if "preview" in n.lower() and n.lower().endswith((".jpg", ".png", ".jpeg"))]
            if preview:
                return len(preview)
            return None
        if ext == ".numbers":
            # Numbers：工作表数难解析，有预览则按预览张数
            preview = [n for n in names if "preview" in n.lower() and n.lower().endswith((".jpg", ".png", ".jpeg"))]
            if preview:
                return len(preview)
            return None
    return None
