"""法务文档解析单测。"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services.legal_documents import parse_legal_document


def _make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_parse_markdown_file():
    raw = "# 用户协议\n\n第一条".encode("utf-8")
    fmt, md = parse_legal_document("agreement.md", raw)
    assert fmt == "md"
    assert "用户协议" in md
    assert "第一条" in md


def test_parse_docx_file():
    data = _make_docx(["用户协议", "第一条"])
    fmt, md = parse_legal_document("agreement.docx", data)
    assert fmt == "docx"
    assert "用户协议" in md
    assert "第一条" in md


def test_reject_unsupported_ext():
    with pytest.raises(ValueError, match="仅支持"):
        parse_legal_document("agreement.pdf", b"%PDF")
