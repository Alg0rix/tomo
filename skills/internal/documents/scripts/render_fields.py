"""Export DOCX to a new PDF with fields updated, without saving the DOCX."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


def render(source: Path, output: Path) -> None:
    source = source.resolve(strict=True)
    output = output.resolve()
    if not source.is_file() or source.suffix.lower() != ".docx":
        raise ValueError("Input must be a DOCX file")
    if output.suffix.lower() != ".pdf" or output == source:
        raise ValueError("Output must be a separate PDF file")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if not output.parent.is_dir():
        raise ValueError("Output parent directory must exist")
    if not shutil.which("soffice"):
        raise RuntimeError("Install LibreOffice (soffice) on this workplace")
    try:
        import uno
        from com.sun.star.beans import PropertyValue
    except ImportError as error:
        raise RuntimeError(
            "Use system Python with LibreOffice's python3-uno module"
        ) from error

    def prop(name: str, value: object):
        item = PropertyValue()
        item.Name, item.Value = name, value
        return item

    # Unique local IPC avoids connecting to another process's fixed TCP port.
    pipe = "tomo_docs_" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="tomo-fields-") as temporary:
        profile = (Path(temporary) / "profile").as_uri()
        env = dict(os.environ, SAL_USE_VCLPLUGIN="svp")
        process = subprocess.Popen(
            [
                "soffice",
                f"-env:UserInstallation={profile}",
                "--headless",
                "--norestore",
                "--nodefault",
                "--nofirststartwizard",
                f"--accept=pipe,name={pipe};urp;",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        document = None
        try:
            local = uno.getComponentContext()
            resolver = local.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver",
                local,
            )
            deadline = time.monotonic() + 45
            while True:
                try:
                    context = resolver.resolve(
                        f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext",
                    )
                    break
                except Exception as error:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Could not connect to LibreOffice"
                        ) from error
                    time.sleep(0.2)
            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop",
                context,
            )
            document = desktop.loadComponentFromURL(
                uno.systemPathToFileUrl(str(source)),
                "_blank",
                0,
                (
                    prop("Hidden", True),
                    prop("ReadOnly", True),
                    prop(
                        "MacroExecutionMode",
                        uno.getConstantByName(
                            "com.sun.star.document.MacroExecMode.NEVER_EXECUTE",
                        ),
                    ),
                ),
            )
            if document is None:
                raise RuntimeError("LibreOffice could not open the document")
            indexes = document.getDocumentIndexes()
            for index in range(indexes.getCount()):
                indexes.getByIndex(index).update()
            document.getTextFields().refresh()
            # Convert into a temporary file, then atomically claim a fresh output.
            pdf = Path(temporary) / "preview.pdf"
            document.storeToURL(
                uno.systemPathToFileUrl(str(pdf)),
                (prop("FilterName", "writer_pdf_Export"),),
            )
            if not pdf.is_file() or not pdf.stat().st_size:
                raise RuntimeError("LibreOffice did not produce a PDF")
            with output.open("xb") as target, pdf.open("rb") as rendered:
                shutil.copyfileobj(rendered, target)
        finally:
            try:
                if document is not None:
                    document.close(False)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        render(args.source, args.output)
    except Exception as error:
        parser.exit(1, f"Field rendering failed: {error}\n")
    print(f"Rendered: {args.output.resolve()}")


if __name__ == "__main__":
    main()
