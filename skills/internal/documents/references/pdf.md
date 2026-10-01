# PDF creation, forms, and restyling

## New PDFs

For matching Word/PDF deliverables, export the final Word file using the Word
reference. For a standalone report PDF, prefer Python ReportLab's Platypus flow
layout with paragraphs, tables, page templates, and real page breaks. Avoid manual
canvas coordinates for every paragraph in a long report: text lengths and page
breaks vary. Use HTML/CSS plus an available print renderer when browser layout is
better suited to the design; inspect its paginated output too.

Read [design.md](design.md). Set page size, margins, a small style set, and fonts
with language coverage. Use XML `sub`/`super` markup for ReportLab subscripts and
superscripts. Escape user text before inserting it into markup. Keep figures
within the content box and let long tables continue with repeated header rows.
Check font embedding where supported, text selectability, and that metadata
does not include unrelated private paths or credentials.

## Forms and existing PDFs

Use `pypdf` for field inspection, form filling, merge/split, and basic page
operations; use `pdfplumber` for text/table extraction. OCR image-only pages with
an available OCR engine when needed. Check extraction before relying on it:
scanned text, columns, and positioned table cells can lose reading order.

Inspect the actual form field names, types, options, and checkbox export values
before filling. Do not assume checkboxes use a universal Yes/true value. Preserve
the form's geometry and inspect field appearances in a rendered result. Filling
fields is different from flattening; flatten only when requested or justified
for the agreed deliverable, and retain an editable original.

Restyling a PDF usually means extracting content and recreating a document.
Explain any fidelity limitation, inventory figures/tables/footnotes, and compare
content before/after. A searchable text layer does not mean the extracted order
or equations are correct. Prefer an editable source when available. Rewriting a
digitally signed PDF can invalidate signatures; do not represent the changed file
as preserving the original signature.

## Verification

Check page count, metadata, expected text, tables, hyperlinks where required,
and form values. Run `pdftotext -layout` or a suitable Python extractor and compare
critical values to the content source. Render pages with `scripts/preview.py` or
an equivalent available renderer, then inspect the images. Confirm no clipping,
missing glyphs, broken rows, tiny labels, or accidental blank pages. A successful
ReportLab build or PDF parser read does not prove good visual layout.
