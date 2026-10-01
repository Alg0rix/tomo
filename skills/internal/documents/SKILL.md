---
name: documents
description: "Create, edit, and style Word/PDF reports, proposals, and printable documents."
---

# Documents

Produce a usable document file with verified content and readable pages. Use for
Word/PDF deliverables and their templates; ordinary chat answers and repository
documentation do not require this workflow. Follow the user's language.

## Choose the deliverable

Honor the requested format and supplied template. Without a specified format,
choose DOCX for a draft intended for further editing, PDF for final distribution,
and Markdown for text-only material. State that choice and proceed. When both
DOCX and PDF are requested, create the DOCX first and export it to PDF so both
have the same content. Slides and spreadsheets are separate tasks.

Establish audience, purpose, available facts, and the requested action from the
conversation and inputs. Ask only when a missing detail changes the substance.
Use explicit placeholders for missing facts; never invent budgets, signatures,
citations, approvals, or company details. Cite externally researched claims in
the document itself and distinguish estimates from confirmed values.

## Route the work

| Task | Load |
| --- | --- |
| Draft a report, proposal, memo, or procedure from a brief | [Content](references/content.md) |
| New Word file; edit/fill a Word template; export Word to PDF | [Word](references/word.md) |
| Standalone PDF; fill forms; extract or restyle PDF content | [PDF](references/pdf.md) |
| New visual layout or requested restyling | [Design](references/design.md) |
| Embedded architecture/flow diagrams, solution comparisons, or visual roadmaps | [Diagrams](references/diagrams.md) |

In Tomo, load support files with
`use_skill(skill_id="documents", file="references/word.md")`. Scripts are
package resources: obtain the script through `use_skill` and write it to a local
working file if the package is not present on the execution workplace. Do not
assume a coordinator's package path exists on a remote workplace.

## Produce and verify

1. Inspect any input before changing it. Retain the original and write a new
   output. For edits, record the requested changes and preserve all other
   content, styles, comments, and document features that matter to the task.
2. Draft the content and resolve missing facts before investing in layout.
   Choose a structure suited to the purpose: a memo may need one page; a long
   report may need a summary and navigation. Do not add a decorative cover or
   table of contents to every document.
3. Use available tools on the actual workplace. Check dependencies rather than
   assuming they are preinstalled. Install needed libraries in an isolated task
   environment; do not add document-only dependencies to Tomo's core environment.
4. Check the produced file opens, then extract its text to verify names, numbers,
   units, dates, required sections, tables, and references. For editing/restyling,
   compare before/after content; explain any intentional removal or reordering.
5. For a new layout, template application, or pagination change, render all pages
   and inspect the images. Use [preview.py](scripts/preview.py) for DOCX/PDF when
   LibreOffice and Poppler are available. Check clipping, missing glyphs, split
   headings, table overflow, page numbers, and blank pages. Fix observed defects
   and re-render. A minor text edit in an unchanged layout can focus visual
   inspection on affected pages. Text extraction alone does not verify layout.

If a renderer is unavailable, attempt a suitable available alternative. Deliver
the actual file with a precise verification limitation if visual inspection
remains impossible; do not call its layout verified. Do not silently substitute
a text file for a requested Word/PDF output.

From the skill directory, generate a preview in a fresh output directory:

```bash
python3 scripts/preview.py /absolute/path/report.docx --out /absolute/path/new-preview
```

The directory contains `preview.pdf` and `page-*.png`. Inspect the images using
the available image-viewing tool. The helper renders pages; it does not judge
their layout. If exporting the verified PDF as a deliverable, copy `preview.pdf`
to the intended final filename and register that file.

## Deliver through Tomo

Register completed files using enabled `save_artifact` with `source_path` and a
useful filename; verify with `list_artifacts` when available and give the returned
link. On Telegram, use enabled `telegram_send_file` for requested file delivery.
If these tools are unavailable, provide the actual accessible file location and
state the delivery limitation. Creating a document does not authorize publishing
it publicly. Finish with the file link, what was verified, and any missing facts
or material limitations.
