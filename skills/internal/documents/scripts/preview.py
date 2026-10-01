"""Render DOCX/PDF into a fresh preview directory; no Python packages required."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def render(source: Path, output: Path) -> list[Path]:
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {".docx", ".pdf"}:
        raise ValueError("Input must be a DOCX or PDF file")
    if not shutil.which("pdftoppm"):
        raise RuntimeError("Install Poppler (pdftoppm) on this workplace")
    if source.suffix.lower() == ".docx" and not shutil.which("soffice"):
        raise RuntimeError("Install LibreOffice (soffice) on this workplace")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Never reuse the running user's office profile or replace their source.
    with tempfile.TemporaryDirectory(prefix="tomo-document-") as temp:
        work = Path(temp)
        pdf = source
        if source.suffix.lower() == ".docx":
            subprocess.run(
                ["soffice", f"-env:UserInstallation={(work / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(work), str(source)],
                check=True, timeout=120, capture_output=True, text=True,
            )
            pdf = work / (source.stem + ".pdf")
            if not pdf.is_file() or not pdf.stat().st_size:
                raise RuntimeError("LibreOffice did not produce a PDF")
        preview_pdf = output / "preview.pdf"
        shutil.copyfile(pdf, preview_pdf)
        subprocess.run(
            ["pdftoppm", "-png", "-r", "110", str(preview_pdf), str(output / "page")],
            check=True, timeout=300, capture_output=True, text=True,
        )
    pages = sorted(output.glob("page-*.png"))
    if not pages:
        raise RuntimeError("Renderer produced no page images")
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="New preview directory")
    args = parser.parse_args()
    try:
        pages = render(args.source, args.out)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Preview failed: {error}\n")
    print(f"Rendered {len(pages)} pages into {args.out.resolve()}")
    for page in pages:
        print(page)


if __name__ == "__main__":
    main()
