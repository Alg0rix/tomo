# Word creation and editing

## Choose an engine

- Default: Python `python-docx` for ordinary reports, proposals, headings, tables,
  pictures, sections, headers/footers, and page setup. It fits Tomo's Python
  workplace tools and does not require a .NET runtime.
- Use `docxtpl` when a supplied template intentionally contains Jinja placeholders;
  do not treat arbitrary Word files as placeholder templates.
- Node `docx` is a suitable alternative for new documents on a Node workplace.
- For fidelity-sensitive editing involving tracked changes, content controls,
  complex fields, or intricate section templates, use targeted OOXML editing or
  an available OpenXML SDK implementation. Do not rebuild the document in a
  simpler engine and silently discard unsupported features.

For unfamiliar library APIs, consult official version-matched documentation.
Use a task virtual environment or `uv run --with python-docx` for generation.
Optional features should not force installation of every engine.

## New documents

Read [design.md](design.md) unless preserving a supplied layout. Set page size,
margins, Normal, Heading styles, and paragraph spacing explicitly. Use semantic
headings and real numbering definitions. `python-docx` supports paragraph styles
but arbitrary automatic numbering, TOC, and dynamic PAGE fields need careful
OOXML support; literal bullet characters or a hand-written TOC are not equivalent.

Determine usable table width from page width minus margins. Set consistent column
and cell widths, allow text wrapping, and inspect long values. Use actual section
breaks for landscape tables or changing page numbering. Set header/footer linkage
deliberately when creating sections. Keep images within usable page width, retain
aspect ratio, and set useful alt text when supported.

For Node `docx`, set explicit page dimensions and DXA table/cell widths. Define
real list numbering and use HeadingLevel or outline levels for TOC inclusion.

## Existing files and templates

Preview the original and inspect paragraphs, runs, styles, tables, fields, section
breaks, relationships, comments, and revision markers relevant to the requested
edit. Text may span multiple runs; a plain string replacement can miss it.
Assigning `paragraph.text` rebuilds its runs and can destroy formatting and fields.
Make replacements at the relevant runs or XML nodes while preserving structure.

For a structured template, keep it as the output base and replace intended content
in place. Preserve section properties, first/even/odd headers, footers, numbering,
and field relationships. For a style-only template, transfer the requested style
rules while retaining source semantics. Do not indiscriminately strip formatting:
emphasis, links, equations, and revision marks can carry meaning.

OOXML edits must maintain namespaces, relationship IDs, content types, and element
ordering. A document-wide `sectPr` must be the final body child; deletion text uses
`w:delText`. A valid ZIP and well-formed XML are only structural smoke checks,
not full OOXML schema validation. Use an available OOXML validator for structural
edits, then open/render the output. Use a proper revision-aware tool when tracked
changes are requested; ordinary edits must not be described as tracked changes.

## PDF export and verification

Use an installed office renderer to export the completed DOCX, then verify the
exported text and page images. Fields and pagination depend on the renderer:
check TOC entries, cross references, and page numbers rather than assuming updates.
Do not overwrite the original during conversion. `scripts/preview.py` generates
an isolated LibreOffice profile, a preview PDF, and an image for every page.

Compare expected text and tables against `python-docx` extraction, and against
`pdftotext -layout` for the export. Paragraph-only extraction omits tables,
headers/footers, and some text boxes: inspect these separately. When both formats
are deliverables, confirm the PDF came from the final DOCX revision.
