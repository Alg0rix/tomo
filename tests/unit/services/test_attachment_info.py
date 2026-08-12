"""Attachment info — clean history chips + LLM-only expansion."""

from __future__ import annotations

import sys
from pathlib import Path

from app.services.chat import (
    attachment_info_lines,
    attachment_meta_for_ids,
    expand_user_content_for_llm,
    prepend_attachment_info,
)


def test_attachment_meta_for_chips(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.chat.store.get_attachment",
        lambda aid: {
            "id": aid,
            "original_name": "example_report.html",
            "filename": "att_x.html",
            "mime_type": "text/html",
            "size_bytes": 13842,
            "file_path": "/secret/home/.tomo/attachments/s1/att_x.html",
        },
    )
    meta = attachment_meta_for_ids(["att_1"])
    assert meta == [
        {
            "id": "att_1",
            "name": "example_report.html",
            "size": 13842,
            "mime": "text/html",
        }
    ]


def test_llm_expansion_inlines_html(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "report.html"
    f.write_text("<html><body><h1>Report</h1></body></html>\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.chat.store.get_attachment",
        lambda aid: {
            "id": aid,
            "original_name": "example_report.html",
            "filename": "att_x.html",
            "mime_type": "text/html",
            "size_bytes": f.stat().st_size,
            "file_path": str(f),
        },
    )
    block = attachment_info_lines(["att_html"])
    assert "```html" in block
    assert "<h1>Report</h1>" in block
    assert str(f) not in block

    expanded = expand_user_content_for_llm(
        {"content": "what is this", "attachment_ids": ["att_html"]}
    )
    assert expanded.startswith("[Attached:")
    assert "what is this" in expanded
    assert "<h1>Report</h1>" in expanded


def test_prepend_attachment_info() -> None:
    assert prepend_attachment_info("hi", []) == "hi"
    assert prepend_attachment_info("hi", None) == "hi"


def test_llm_expansion_inlines_docx_via_anydoc(monkeypatch, tmp_path: Path) -> None:
    """Office docs (docx/pdf/xlsx/...) convert to Markdown instead of being
    treated as opaque binary — extends the text-attachment path via anydoc.
    """
    f = tmp_path / "report.docx"
    f.write_bytes(b"not a real docx, converter is mocked")
    monkeypatch.setattr(
        "app.services.chat.store.get_attachment",
        lambda aid: {
            "id": aid,
            "original_name": "quarterly_report.docx",
            "filename": "att_x.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": f.stat().st_size,
            "file_path": str(f),
        },
    )

    class _FakeAnydoc:
        @staticmethod
        def to_markdown(path):
            assert path == str(f)
            return "# Quarterly Report\n\nRevenue is up."

    monkeypatch.setitem(sys.modules, "anydoc", _FakeAnydoc)

    block = attachment_info_lines(["att_docx"])
    assert "# Quarterly Report" in block
    assert "Revenue is up." in block
    assert "Binary file" not in block
    assert str(f) not in block


def _fake_image_attachment(tmp_path: Path, name: str = "photo.png") -> tuple[Path, dict]:
    f = tmp_path / name
    # Tiny valid 1x1 PNG (not that content matters — code never decodes it).
    f.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108"
            "0600000031e39b1e0000000a49444154789c6360000002000155"
            "3f5e920000000049454e44ae426082"
        )
    )
    att = {
        "id": "att_img1",
        "original_name": name,
        "filename": "att_x.png",
        "mime_type": "image/png",
        "size_bytes": f.stat().st_size,
        "file_path": str(f),
    }
    return f, att


def test_expand_user_content_stays_plain_string_without_vision(
    monkeypatch, tmp_path: Path
) -> None:
    """Default (vision_capable=False) behavior is unchanged — plain string,
    image treated as an opaque binary attachment like before."""
    _f, att = _fake_image_attachment(tmp_path)
    monkeypatch.setattr("app.services.chat.store.get_attachment", lambda aid: att)

    result = expand_user_content_for_llm(
        {"content": "what is this", "attachment_ids": ["att_img1"]}
    )
    assert isinstance(result, str)
    assert "Binary file" in result
    assert "what is this" in result


def test_expand_user_content_builds_multimodal_parts_when_vision_capable(
    monkeypatch, tmp_path: Path
) -> None:
    _f, att = _fake_image_attachment(tmp_path)
    monkeypatch.setattr("app.services.chat.store.get_attachment", lambda aid: att)

    result = expand_user_content_for_llm(
        {"content": "what is this", "attachment_ids": ["att_img1"]},
        vision_capable=True,
    )
    assert isinstance(result, list)
    assert result[0] == {"type": "text", "text": result[0]["text"]}
    assert "what is this" in result[0]["text"]
    assert "Binary file" not in result[0]["text"]
    image_parts = [p for p in result if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    url = image_parts[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_expand_user_content_multiple_images_all_included(
    monkeypatch, tmp_path: Path
) -> None:
    _f1, att1 = _fake_image_attachment(tmp_path, "a.png")
    _f2, att2 = _fake_image_attachment(tmp_path, "b.png")
    atts = {"att_a": att1, "att_b": att2}
    monkeypatch.setattr(
        "app.services.chat.store.get_attachment", lambda aid: atts.get(aid)
    )

    result = expand_user_content_for_llm(
        {"content": "compare these", "attachment_ids": ["att_a", "att_b"]},
        vision_capable=True,
    )
    image_parts = [p for p in result if p.get("type") == "image_url"]
    assert len(image_parts) == 2


def test_expand_user_content_vision_capable_no_images_stays_string(
    monkeypatch, tmp_path: Path
) -> None:
    """vision_capable=True with only non-image attachments doesn't force a list."""
    f = tmp_path / "notes.txt"
    f.write_text("plain notes", encoding="utf-8")
    att = {
        "id": "att_txt",
        "original_name": "notes.txt",
        "filename": "att_x.txt",
        "mime_type": "text/plain",
        "size_bytes": f.stat().st_size,
        "file_path": str(f),
    }
    monkeypatch.setattr("app.services.chat.store.get_attachment", lambda aid: att)

    result = expand_user_content_for_llm(
        {"content": "read this", "attachment_ids": ["att_txt"]},
        vision_capable=True,
    )
    assert isinstance(result, str)
    assert "plain notes" in result


def test_llm_expansion_office_doc_conversion_failure_falls_back_to_binary_note(
    monkeypatch, tmp_path: Path
) -> None:
    """anydoc failures (corrupt/encrypted/unsupported) must not crash the turn."""
    f = tmp_path / "broken.pdf"
    f.write_bytes(b"%PDF-not-really-valid")
    monkeypatch.setattr(
        "app.services.chat.store.get_attachment",
        lambda aid: {
            "id": aid,
            "original_name": "broken.pdf",
            "filename": "att_y.pdf",
            "mime_type": "application/pdf",
            "size_bytes": f.stat().st_size,
            "file_path": str(f),
        },
    )

    class _FakeAnydoc:
        class ConvertError(Exception):
            pass

        @staticmethod
        def to_markdown(path):
            raise _FakeAnydoc.ConvertError("malformed PDF")

    monkeypatch.setitem(sys.modules, "anydoc", _FakeAnydoc)

    block = attachment_info_lines(["att_pdf"])
    assert "Binary file" in block or "Could not read" in block
    assert "broken.pdf" in block
