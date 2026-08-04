"""Parse uploaded documents into plain text/markdown for KB ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

MAX_BODY_CHARS = 200_000  # truncate with note if longer
MAX_TITLE_CHARS = 200
ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md", ".markdown"})


@dataclass(frozen=True)
class ParsedDocument:
    title: str  # suggested title (from metadata or filename stem)
    body: str  # markdown/text content
    source_type: str  # "pdf" | "docx" | "text"
    filename: str
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def _clean_title(text: str) -> str:
    return " ".join((text or "").replace("_", " ").replace("-", " ").split()).strip()


def _fallback_title(filename: str) -> str:
    stem = _clean_title(Path(filename).stem)
    if stem:
        return stem[:MAX_TITLE_CHARS]
    # Hidden / extension-only names (e.g. ".txt") — keep something usable.
    raw = (Path(filename).name or "upload").strip() or "upload"
    return raw[:MAX_TITLE_CHARS]


def _finalize_title(*candidates: str, filename: str) -> str:
    for c in candidates:
        cleaned = _clean_title(c)
        if cleaned:
            return cleaned[:MAX_TITLE_CHARS]
    return _fallback_title(filename)


def _truncate(body: str) -> tuple[str, bool]:
    if len(body) <= MAX_BODY_CHARS:
        return body, False
    return body[:MAX_BODY_CHARS] + "\n\n…[truncated]", True


def _empty_text_message(source_type: str) -> str:
    if source_type == "pdf":
        return "No extractable text (PDF may be scanned/image-based)"
    if source_type == "docx":
        return "No extractable text (could not parse DOCX)"
    return "No extractable text (file is empty)"


def parse_document(filename: str, data: bytes) -> ParsedDocument:
    """Parse file bytes. Raises ValueError on empty/unsupported/unparseable.

    Library failures from pdf-inspector / markitdown are wrapped as ValueError
    so the API layer can map them to HTTP 400 consistently.
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ValueError(f"unsupported file type ({ext or 'none'}); allowed: {allowed}")
    if not data:
        raise ValueError("file is empty")

    try:
        if ext == ".pdf":
            parsed = _parse_pdf(data)
        elif ext == ".docx":
            parsed = _parse_docx(filename, data)
        else:
            parsed = _parse_text(data)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"failed to parse {ext}: {exc}") from exc

    body, truncated = _truncate(parsed["body"].strip())
    if not body:
        raise ValueError(_empty_text_message(parsed["source_type"]))

    title = _finalize_title(parsed.get("title") or "", filename=filename)
    return ParsedDocument(
        title=title,
        body=body,
        source_type=parsed["source_type"],
        filename=filename,
        truncated=truncated,
        warnings=list(parsed.get("warnings") or []),
    )


def _pdf_warnings(result: object) -> list[str]:
    warnings: list[str] = []
    pages_ocr = getattr(result, "pages_needing_ocr", None) or []
    if pages_ocr:
        n = len(pages_ocr) if hasattr(pages_ocr, "__len__") else 0
        if n:
            warnings.append(f"{n} page(s) may need OCR (limited text extraction)")
    if getattr(result, "has_encoding_issues", False):
        warnings.append("PDF has encoding issues; some characters may be wrong")
    pdf_type = getattr(result, "pdf_type", None)
    if pdf_type is not None and str(pdf_type).lower() in {
        "scanned",
        "image",
        "image_based",
        "ocr",
    }:
        warnings.append(f"PDF classified as {pdf_type}")
    return warnings


def _parse_pdf(data: bytes) -> dict:
    import pdf_inspector

    result = pdf_inspector.process_pdf_bytes(data)
    markdown = (result.markdown or "").strip()
    if not markdown:
        raise ValueError(_empty_text_message("pdf"))
    title = getattr(result, "title", None) or ""
    return {
        "title": _clean_title(title),
        "body": markdown,
        "source_type": "pdf",
        "warnings": _pdf_warnings(result),
    }


def _parse_docx(filename: str, data: bytes) -> dict:
    from markitdown import MarkItDown

    md = MarkItDown()
    stream = BytesIO(data)
    # convert_stream avoids writing the upload to disk.
    result = md.convert_stream(stream, file_extension=".docx")
    text = (result.text_content or "").strip()
    if not text:
        raise ValueError(_empty_text_message("docx"))
    return {
        "title": _clean_title(Path(filename).stem),
        "body": text,
        "source_type": "docx",
        "warnings": [],
    }


def _parse_text(data: bytes) -> dict:
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError(_empty_text_message("text"))
    return {
        "title": "",
        "body": text,
        "source_type": "text",
        "warnings": [],
    }
