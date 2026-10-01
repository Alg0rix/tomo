"""Image byte preparation for vision paths: MIME sniffing, data-URL
encoding, and optional Pillow downscale / region crop.

Pillow is an optional dependency: without it, already-small images still
encode fine — only oversize payloads and ``region`` crops are refused, so
the caller can fall back instead of crashing.
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

# Provider ceilings are partial and evolving (Anthropic: 5 MB, 8000px);
# 8 MiB of base64 ≈ 6 MiB decoded — comfortably under every major cap while
# leaving detail intact. Downscale only kicks in above this.
MAX_DATA_URL_BYTES = 8 * 1024 * 1024
_MAX_LONG_EDGE = 4096

# Magic-byte signatures; platforms lie about content-type, so bytes win.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)
_RIFF_WEBP = (b"RIFF", b"WEBP")

_SUFFIX_MIMES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heic",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
}


def sniff_image_mime(raw: bytes) -> str | None:
    """Image MIME from magic bytes; None when unrecognised."""
    if not raw:
        return None
    for sig, mime in _MAGIC:
        if raw.startswith(sig):
            return mime
    if raw.startswith(_RIFF_WEBP[0]) and raw[8:12] == _RIFF_WEBP[1]:
        return "image/webp"
    if raw[4:8] == b"ftyp":
        brand = raw[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "image/heic"
        if brand in {b"avif", b"avis"}:
            return "image/avif"
    head = raw[:512].lstrip().lower()
    if head.startswith((b"<?xml", b"<svg")) and b"<svg" in head:
        return "image/svg+xml"
    return None


def guess_image_mime(path: str | Path, raw: bytes | None = None) -> str:
    """Best MIME for *path*: magic bytes → suffix → jpeg default."""
    if raw is not None:
        sniffed = sniff_image_mime(raw)
        if sniffed:
            return sniffed
    return _SUFFIX_MIMES.get(Path(path).suffix.lower(), "image/jpeg")


def _to_data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def pillow_available() -> bool:
    try:
        import PIL.Image  # noqa: F401

        return True
    except ImportError:
        return False


def _pillow_prepare(
    raw: bytes, region: list[int] | None, max_data_url_bytes: int
) -> tuple[bytes, str, str | None]:
    """Crop/resize with Pillow → (raw, mime, note). Raises ValueError on
    undecodable input."""
    from PIL import Image

    img = Image.open(BytesIO(raw))
    orig_size = img.size
    note: str | None = None
    if img.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
        img = img.convert("RGBA")
    if region:
        w, h = img.size
        x1, y1, x2, y2 = (int(v) for v in region[:4])
        x1, x2 = max(0, min(x1, w)), max(0, min(x2, w))
        y1, y2 = max(0, min(y1, h)), max(0, min(y2, h))
        if x2 <= x1 or y2 <= y1:
            raise ValueError("region is empty after clamping to image bounds")
        img = img.crop((x1, y1, x2, y2))
        note = f"cropped to region [{x1}, {y1}, {x2}, {y2}] of original {w}x{h}"
    out_mime = "image/png"
    out = _encode_png(img)
    if len(out) <= max_data_url_bytes and max(img.size) <= _MAX_LONG_EDGE:
        return out, out_mime, note
    # Progressive downscale: halve until under the byte cap and long edge.
    while True:
        w, h = img.size
        if len(out) <= max_data_url_bytes and max(w, h) <= _MAX_LONG_EDGE:
            break
        new_w, new_h = max(int(w * 0.5), 64), max(int(h * 0.5), 64)
        if (new_w, new_h) == (w, h):
            break
        img = img.resize((new_w, new_h), Image.LANCZOS)
        out = _encode_png(img)
    if len(out) > max_data_url_bytes:
        # JPEG as last resort — much denser for photo content.
        jpeg = img.convert("RGB")
        for quality in (85, 70, 55, 40):
            buf = BytesIO()
            jpeg.save(buf, format="JPEG", quality=quality)
            if len(buf.getvalue()) <= max_data_url_bytes:
                note = (note + "; " if note else "") + f"downscaled to {img.size[0]}x{img.size[1]} jpeg q{quality}"
                return buf.getvalue(), "image/jpeg", note
        raise ValueError(
            f"image is still too large after downscaling ({len(out) / 1024 / 1024:.1f} MB); "
            "crop a region or provide a smaller file"
        )
    if img.size != orig_size:
        note = (note + "; " if note else "") + f"downscaled to {img.size[0]}x{img.size[1]}"
    return out, out_mime, note


def _encode_png(img) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def encode_image_data_url(
    raw: bytes,
    mime: str | None = None,
    *,
    region: list[int] | None = None,
    max_data_url_bytes: int = MAX_DATA_URL_BYTES,
) -> tuple[str, str | None]:
    """``(data_url, scale_note)`` — downscales/crops via Pillow when present.

    Raises ``ValueError`` when the bytes are unusable or too large to
    deliver (no Pillow, or still over cap after downscaling).
    """
    if not raw:
        raise ValueError("empty image data")
    if pillow_available():
        try:
            cooked, cooked_mime, note = _pillow_prepare(raw, region, max_data_url_bytes)
            return _to_data_url(cooked, cooked_mime), note
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"could not decode image ({exc})") from exc
    if region:
        raise ValueError("region crops require Pillow (pip install pillow)")
    if len(raw) * 4 // 3 > max_data_url_bytes:
        raise ValueError(
            f"image too large ({len(raw) / 1024 / 1024:.1f} MB) and Pillow is not "
            "installed to downscale it — resize below ~6 MB or pip install pillow"
        )
    return _to_data_url(raw, mime or "image/png"), None


def attachment_bytes(att: dict[str, Any]) -> bytes | None:
    """Raw bytes of an attachment row, or None when unreadable."""
    from pathlib import Path as _P

    path = _P(att.get("file_path") or "")
    try:
        data = path.read_bytes() if path.is_file() else None
    except OSError:
        return None
    return data or None


__all__ = [
    "sniff_image_mime",
    "guess_image_mime",
    "encode_image_data_url",
    "pillow_available",
    "attachment_bytes",
    "MAX_DATA_URL_BYTES",
]
