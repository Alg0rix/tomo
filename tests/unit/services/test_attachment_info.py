"""Attachment info — clean history chips + LLM-only expansion."""

from __future__ import annotations

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
