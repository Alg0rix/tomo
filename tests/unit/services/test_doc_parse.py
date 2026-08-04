"""Unit tests for the document parser used by KB upload."""

from __future__ import annotations

import pytest

from app.services.doc_parse import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    parse_document,
)


def test_plain_txt() -> None:
    doc = parse_document("notes.txt", b"hello world\nsecond line")
    assert doc.source_type == "text"
    assert doc.title == "notes"
    assert doc.body == "hello world\nsecond line"
    assert doc.truncated is False


def test_markdown() -> None:
    doc = parse_document("guide.markdown", b"# Title\n\nSome body.")
    assert doc.source_type == "text"
    assert doc.title == "guide"
    assert "# Title" in doc.body


def test_title_strips_separators() -> None:
    doc = parse_document("vendor-deadline_report.md", b"body")
    assert doc.title == "vendor deadline report"


def test_title_capped_at_max() -> None:
    long_stem = "a" * (MAX_TITLE_CHARS + 40)
    doc = parse_document(f"{long_stem}.txt", b"body")
    assert len(doc.title) == MAX_TITLE_CHARS


def test_unsupported_ext_raises() -> None:
    with pytest.raises(ValueError, match="allowed"):
        parse_document("image.exe", b"\x00\x01\x02")


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_document("notes.txt", b"")


def test_text_only_empty_raises() -> None:
    with pytest.raises(ValueError, match="No extractable text"):
        parse_document("notes.txt", b"   \n  ")


def test_truncate_when_body_over_max() -> None:
    body = "x" * (MAX_BODY_CHARS + 500)
    doc = parse_document("big.txt", body.encode())
    assert doc.truncated is True
    assert len(doc.body) <= MAX_BODY_CHARS + len("\n\n…[truncated]")
    assert doc.body.endswith("…[truncated]")


def test_pdf_uses_pdf_inspector(monkeypatch) -> None:
    class _Result:
        markdown = "# Extracted\n\nPDF text."
        title = "PDF Title"
        pages_needing_ocr = []
        has_encoding_issues = False
        pdf_type = "digital"

    called = {}

    def _fake(bytes_):
        called["bytes"] = bytes_
        return _Result()

    monkeypatch.setattr("pdf_inspector.process_pdf_bytes", _fake)
    doc = parse_document("report.pdf", b"%PDF-1.4 fake")
    assert doc.source_type == "pdf"
    assert doc.title == "PDF Title"
    assert doc.body == "# Extracted\n\nPDF text."
    assert called["bytes"] == b"%PDF-1.4 fake"
    assert doc.warnings == []


def test_pdf_warnings_from_inspector(monkeypatch) -> None:
    class _Result:
        markdown = "some text"
        title = "T"
        pages_needing_ocr = [1, 2]
        has_encoding_issues = True
        pdf_type = "scanned"

    monkeypatch.setattr(
        "pdf_inspector.process_pdf_bytes", lambda _: _Result()
    )
    doc = parse_document("scan.pdf", b"%PDF-1.4 fake")
    assert any("OCR" in w for w in doc.warnings)
    assert any("encoding" in w.lower() for w in doc.warnings)
    assert any("scanned" in w.lower() for w in doc.warnings)


def test_pdf_no_text_raises(monkeypatch) -> None:
    class _Result:
        markdown = None
        title = ""
        pages_needing_ocr = []
        has_encoding_issues = False
        pdf_type = None

    monkeypatch.setattr(
        "pdf_inspector.process_pdf_bytes", lambda _: _Result()
    )
    with pytest.raises(ValueError, match="PDF may be scanned"):
        parse_document("scan.pdf", b"%PDF-1.4 fake")


def test_pdf_library_error_wrapped_as_valueerror(monkeypatch) -> None:
    def _boom(_):
        raise RuntimeError("corrupt xref")

    monkeypatch.setattr("pdf_inspector.process_pdf_bytes", _boom)
    with pytest.raises(ValueError, match="failed to parse .pdf"):
        parse_document("bad.pdf", b"%PDF-1.4 fake")


def test_docx_uses_markitdown(monkeypatch) -> None:
    class _ConvertResult:
        text_content = "Word doc text"

    class _FakeMarkItDown:
        def __init__(self):
            self.seen_ext = None
            self.seen_bytes = None

        def convert_stream(self, stream, **kwargs):
            self.seen_ext = kwargs.get("file_extension")
            self.seen_bytes = stream.read()
            return _ConvertResult()

    fake = _FakeMarkItDown()
    monkeypatch.setattr("markitdown.MarkItDown", lambda: fake)
    doc = parse_document("memo.docx", b"PK\x03\x04 fake docx")
    assert doc.source_type == "docx"
    assert doc.title == "memo"
    assert doc.body == "Word doc text"
    assert fake.seen_ext == ".docx"
    assert fake.seen_bytes == b"PK\x03\x04 fake docx"


def test_docx_no_text_raises(monkeypatch) -> None:
    class _ConvertResult:
        text_content = ""

    class _FakeMarkItDown:
        def convert_stream(self, stream, **kwargs):
            return _ConvertResult()

    monkeypatch.setattr("markitdown.MarkItDown", lambda: _FakeMarkItDown())
    with pytest.raises(ValueError, match="could not parse DOCX"):
        parse_document("memo.docx", b"PK\x03\x04 fake")


def test_docx_library_error_wrapped(monkeypatch) -> None:
    class _FakeMarkItDown:
        def convert_stream(self, stream, **kwargs):
            raise OSError("zip bomb")

    monkeypatch.setattr("markitdown.MarkItDown", lambda: _FakeMarkItDown())
    with pytest.raises(ValueError, match="failed to parse .docx"):
        parse_document("memo.docx", b"PK\x03\x04 fake")
