# Embedded diagrams and technical proposals

Use when the brief needs real diagrams, architecture comparisons, flows, or a
visual roadmap. Ordinary letters and reports do not need diagrams by default.
Use the included diagram and Word assembly helpers to render figures and embed
them with captions. Choose the document structure from the brief and supplied
template.

## Plan only the diagrams that help

Identify entities, boundaries, connections, direction, and the decision each
figure explains. For a solution comparison, use a shared overview when useful
and one diagram per option so readers can see the actual differences. Add a
roadmap when phases/dependencies matter; never invent durations to fill it.

Use comparable labels and levels of detail between options. Distinguish existing,
proposed, and external components explicitly. Do not label a design as the current
system without evidence. A demo must identify its assumptions and illustrative
content. A table is useful for tradeoffs but does not replace a requested diagram.

## Render and inspect

Check Python `matplotlib` on the execution workplace. Use the package's
`scripts/diagram_helpers.py` for consistent boxes, semantic colors, grouping
bands, and arrows. Import it from the actual local scripts directory; in Tomo,
load the file through `use_skill` and save a local copy if needed.

```python
from diagram_helpers import canvas, box, arrow, right, left, save

fig, ax = canvas(10.6, 4.8, (0, 10.6), (0, 4.8), "Request processing")
source = box(ax, 0.5, 1.5, 3.0, 1.4, "Input", ["User request"],
             tsize=15, bsize=12)
target = box(ax, 6.5, 1.5, 3.0, 1.4, "Processing", ["Create result"],
             tsize=15, bsize=12)
arrow(ax, right(source), left(target), label="request", loff=(0, 0.3))
save(fig, "processing", output_dir="./diagrams")
```

Helpers export 200-DPI PNGs. Keep a consistent role/color mapping and add text
labels so color is not the sole signal. Render, inspect the image, and adjust
geometry before embedding. Wrapping is approximate: box height does not grow
automatically. Long labels or arrow-label backgrounds can overlap boxes even
when the diagram renders successfully. Check again at its final document size;
a readable full-screen diagram can become unreadable on a page.

## Embed in the chosen document engine

Continue using the document's existing engine/template. For Python Word generation,
`python-docx` can add a PNG with an explicit width and preserved aspect ratio.
For ReportLab PDF, size the image within the frame and retain the caption.

For a new complex Word proposal, the supplied `scripts/docx_helpers.js` is an
optional Node `docx` path with heading/list styles, callouts, comparison tables,
and `Figure(path, widthPx, caption)`. Check `require('docx')` first and install
only in a task directory if needed. It exports primitives for cover pages, TOC,
headers/footers, and A4/Letter pages. Load only this helper when choosing Node.

Set column widths from usable page width (page width minus margins). Display
width is pixels at 96 pixels/inch: 560 px is about 5.83 inches. Keep image width
inside the page's content area and account for remaining height. `Figure` keeps
aspect ratio and the image/caption together; still inspect pagination. Diagrams
are embedded PNGs, not editable Word shapes. Disclose this if editable shapes
are part of the brief; preserve generation sources when requested.

## TOC, fields, and final verification

Only include a TOC when it improves navigation. For Node `docx`, use built-in
heading levels, exported `HEADING_STYLES`, and the library's supported
`features: { updateFields: true }` option. Check the installed API if that option
is unavailable. It requests field updates; it does not guarantee every Word
client updates automatically or that the saved DOCX has a populated TOC cache.
Do not inject a settings element at a guessed XML position.

To see populated fields in a PDF, use the optional `scripts/render_fields.py`:

```bash
python3 scripts/render_fields.py /absolute/path/proposal.docx /absolute/path/new-preview.pdf
python3 scripts/preview.py /absolute/path/new-preview.pdf --out /absolute/path/new-pages
```

This requires system Python's LibreOffice `uno` module plus `soffice`; an isolated
Python environment may not expose `uno`. It updates indexes and fields in memory,
exports a new PDF, and never saves back to the input DOCX. Read every rendered
page for a multi-section proposal, including TOC, each diagram/caption, tables,
section transitions, and the final page. Check actual page references after
layout changes. A populated PDF TOC does not prove the DOCX's cached TOC is
populated. If an immediately visible DOCX TOC is required, use a tested
cache-population workflow and validate the final DOCX or disclose the limitation.

For OOXML structural edits, use a proper schema validator when available; visual
QA is a separate check and cannot establish schema validity. Register requested
deliverables through the main skill's artifact workflow. Do not include working
PNGs or QA PDFs unless requested or useful for the requested preview.
