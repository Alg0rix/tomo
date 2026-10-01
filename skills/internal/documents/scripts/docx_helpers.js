/**
 * Reusable docx-js helpers for assembling a long, multi-section
 * proposal document: consistent headings, bullets, tables, callouts,
 * and figure embedding with captions.
 *
 * Requires the `docx` npm package on the actual execution workplace.
 *
 * Usage:
 *   const H = require("./docx_helpers.js");
 *   const { H1, H2, P, Bullet, Figure, makeTable, Document, Packer, ... } = H;
 *
 * See references/diagrams.md for document assembly and figure guidance.
 */

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType,
  ImageRun, PageBreak, TableOfContents, LevelFormat, convertInchesToTwip,
  Header, Footer, PageNumber, NumberFormat, VerticalAlign, TabStopType,
  TabStopPosition, ExternalHyperlink,
} = require("docx");

// ---------------------------------------------------------------------
// Palette - tweak to match brand. Keep INK/SUBTLE dark enough for body
// text contrast; NAVY/TEAL are used for headings and accents.
// ---------------------------------------------------------------------
const INK      = "1B2A38";
const SUBTLE   = "44515D";
const RULE     = "C9D2DA";
const NAVY     = "1F3A5F";
const TEAL     = "1F8A70";
const AMBER    = "C8860D";
const HEAD_BG  = "1F3A5F";
const HEAD_FG  = "FFFFFF";
const ALT_BG   = "F3F6F9";

const FONT = "Calibri";

// ---------------------------------------------------------------------
// Dependency-free PNG dimension reader (avoids needing the `image-size`
// npm package, which may not be globally installed). PNG stores width
// and height as big-endian uint32 at fixed byte offsets in the IHDR
// chunk: bytes 16-19 = width, bytes 20-23 = height.
// ---------------------------------------------------------------------
function pngSize(buffer) {
  if (buffer.length < 24 || !buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) || buffer.toString("ascii", 12, 16) !== "IHDR") {
    throw new Error("Expected a PNG image with an IHDR header");
  }
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  if (!width || !height) throw new Error("Could not read PNG dimensions - is this a valid PNG?");
  return { w: width, h: height };
}

// ---------------------------------------------------------------------
// Text helpers
// ---------------------------------------------------------------------
function P(text, opts = {}) {
  const { bold, italics, size = 22, color = INK, spacingAfter = 160, alignment } = opts;
  return new Paragraph({
    alignment,
    spacing: { after: spacingAfter, line: 288 },
    children: [new TextRun({ text, font: FONT, size, color, bold, italics })],
  });
}

// paragraph built from mixed runs: array of {text, bold, italics, color}
function PM(parts, opts = {}) {
  const { spacingAfter = 160, alignment } = opts;
  return new Paragraph({
    alignment,
    spacing: { after: spacingAfter, line: 288 },
    children: parts.map(p => new TextRun({ text: p.text, font: FONT, size: p.size || 22,
      color: p.color || INK, bold: p.bold, italics: p.italics })),
  });
}

function H1(text) {
  return new Paragraph({
    keepNext: true,
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 120, after: 200 },
    border: { bottom: { color: NAVY, space: 6, style: BorderStyle.SINGLE, size: 10 } },
    children: [new TextRun({ text, font: FONT, size: 34, bold: true, color: NAVY })],
  });
}

function H2(text) {
  return new Paragraph({
    keepNext: true,
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 260, after: 140 },
    children: [new TextRun({ text, font: FONT, size: 26, bold: true, color: NAVY })],
  });
}

function H3(text) {
  return new Paragraph({
    keepNext: true,
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 180, after: 100 },
    children: [new TextRun({ text, font: FONT, size: 23, bold: true, color: TEAL })],
  });
}

function Bullet(text, opts = {}) {
  const { level = 0, bold } = opts;
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 100, line: 276 },
    children: Array.isArray(text)
      ? text.map(p => new TextRun({ text: p.text, font: FONT, size: 21, color: p.color || INK, bold: p.bold, italics: p.italics }))
      : [new TextRun({ text, font: FONT, size: 21, color: INK, bold })],
  });
}

function Note(text) {
  return new Paragraph({
    spacing: { before: 60, after: 200, line: 264 },
    indent: { left: 260 },
    border: { left: { color: AMBER, space: 8, style: BorderStyle.SINGLE, size: 16 } },
    children: [new TextRun({ text, font: FONT, size: 20, italics: true, color: SUBTLE })],
  });
}

function Caption(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 90, after: 320 },
    children: [new TextRun({ text, font: FONT, size: 19, italics: true, color: SUBTLE })],
  });
}

// ---------------------------------------------------------------------
// Figure embedding - pass the absolute path to a PNG, the desired
// display width in pixels (~560-600 fits a US-Letter/A4 page with 1in
// margins), and a caption. Height is derived from the PNG's own
// aspect ratio so nothing gets stretched or squashed.
// ---------------------------------------------------------------------
function Figure(absPath, widthPx, caption) {
  if (!Number.isFinite(widthPx) || widthPx <= 0) throw new Error("Figure width must be positive");
  const buf = fs.readFileSync(absPath);
  const { w, h } = pngSize(buf);
  const dispW = widthPx;
  const dispH = Math.round(widthPx * h / w);
  return [
    new Paragraph({
      keepNext: true,
      alignment: AlignmentType.CENTER,
      spacing: { before: 160, after: 40 },
      children: [new ImageRun({
        type: "png",
        data: buf,
        transformation: { width: dispW, height: dispH },
      })],
    }),
    Caption(caption),
  ];
}

// ---------------------------------------------------------------------
// Tables
// ---------------------------------------------------------------------
function makeTable(colWidthsDxa, headerRow, bodyRows) {
  if (!colWidthsDxa.length || colWidthsDxa.some(w => !Number.isInteger(w) || w <= 0)) {
    throw new Error("Table widths must be positive DXA integers");
  }
  if (headerRow.length !== colWidthsDxa.length || bodyRows.some(row => row.length !== colWidthsDxa.length)) {
    throw new Error("Each table row must match the column count");
  }
  const total = colWidthsDxa.reduce((a, b) => a + b, 0);
  const rows = [];
  rows.push(new TableRow({
    tableHeader: true,
    children: headerRow.map((h, i) => new TableCell({
      width: { size: colWidthsDxa[i], type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: HEAD_BG },
      verticalAlign: VerticalAlign.CENTER,
      margins: { top: 90, bottom: 90, left: 110, right: 110 },
      children: [new Paragraph({ children: [new TextRun({ text: h, font: FONT, size: 19, bold: true, color: HEAD_FG })] })],
    })),
  }));
  bodyRows.forEach((r, ri) => {
    rows.push(new TableRow({
      children: r.map((c, ci) => new TableCell({
        width: { size: colWidthsDxa[ci], type: WidthType.DXA },
        shading: { type: ShadingType.CLEAR, fill: ri % 2 === 1 ? ALT_BG : "FFFFFF" },
        verticalAlign: VerticalAlign.CENTER,
        margins: { top: 90, bottom: 90, left: 110, right: 110 },
        children: (Array.isArray(c) ? c : [c]).map(t => new Paragraph({
          spacing: { after: 30, line: 240 },
          children: [new TextRun({ text: t, font: FONT, size: 19, color: INK })],
        })),
      })),
    }));
  });
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: colWidthsDxa,
    rows,
    borders: {
      top: { style: BorderStyle.SINGLE, size: 4, color: RULE },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: RULE },
      left: { style: BorderStyle.SINGLE, size: 4, color: RULE },
      right: { style: BorderStyle.SINGLE, size: 4, color: RULE },
      insideHorizontal: { style: BorderStyle.SINGLE, size: 4, color: RULE },
      insideVertical: { style: BorderStyle.SINGLE, size: 4, color: RULE },
    },
  });
}

// ---------------------------------------------------------------------
// Standard numbering config (bullets) - pass this into `new Document({
// numbering: { config: [BULLET_NUMBERING] } })`.
// ---------------------------------------------------------------------
const BULLET_NUMBERING = {
  reference: "bullets",
  levels: [
    { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: convertInchesToTwip(0.28), hanging: convertInchesToTwip(0.18) } } } },
    { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: convertInchesToTwip(0.55), hanging: convertInchesToTwip(0.18) } } } },
  ],
};

// Standard heading paragraphStyles - pass into `new Document({ styles: {
// paragraphStyles: HEADING_STYLES } })` so TableOfContents can resolve
// outline levels correctly (docx-js gotcha: custom heading colors/sizes
// set only via H1()/H2()/H3() runs above are NOT enough - the *style*
// also needs outlineLevel set, or headings silently drop out of the TOC).
const HEADING_STYLES = [
  { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
    run: { font: FONT, size: 34, bold: true, color: NAVY },
    paragraph: { spacing: { before: 120, after: 200 }, outlineLevel: 0 } },
  { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
    run: { font: FONT, size: 26, bold: true, color: NAVY },
    paragraph: { spacing: { before: 260, after: 140 }, outlineLevel: 1 } },
  { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
    run: { font: FONT, size: 23, bold: true, color: TEAL },
    paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 2 } },
];

const PAGE_LETTER = { width: 12240, height: 15840 }; // US Letter, DXA
const PAGE_A4     = { width: 11906, height: 16838 }; // A4, DXA
const MARGIN_1IN  = { top: 1440, bottom: 1440, left: 1440, right: 1440 };

module.exports = {
  // re-exported docx primitives so callers only need one require()
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType,
  ImageRun, PageBreak, TableOfContents, LevelFormat, convertInchesToTwip,
  Header, Footer, PageNumber, NumberFormat, VerticalAlign, TabStopType, TabStopPosition,
  // helpers
  P, PM, H1, H2, H3, Bullet, Note, Caption, Figure, makeTable, pngSize,
  BULLET_NUMBERING, HEADING_STYLES, PAGE_LETTER, PAGE_A4, MARGIN_1IN,
  INK, SUBTLE, RULE, NAVY, TEAL, AMBER, HEAD_BG, HEAD_FG, ALT_BG, FONT,
};
