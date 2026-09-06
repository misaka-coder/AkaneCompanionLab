# Akane document writer

Independent document-generation and non-destructive styling library. No host
imports, hidden LLM call, model credentials, source-preview reconstruction, or
in-place edits. Plugin installation/discovery is a separate migration slice;
this library alone does not expose a model capability.

`render_document` writes complete supplied content to txt/md/html/json/srt/lrc/vtt,
CSV, XLSX, DOCX, or PDF. JSON must actually parse. Text-like outputs preserve the
provided string; they are not code execution or subtitle-validation services.
Word/PDF support headings, paragraphs, basic emphasis, lists, fenced code and
pipe tables. This is a documented Markdown subset, not a full HTML/Markdown engine.
Unsupported Markdown syntax stays visible; images/links are not fetched.

Explicit `rows` retain numbers, booleans, nulls, whitespace and empty records.
XLSX string cells are literal text even when starting with `=`. Formula authoring
is not advertised; styling an existing workbook preserves its existing formulas.
CSV preserves raw values and reports a notice for formula-like strings: import
as text in spreadsheet applications. Excel generation rejects simultaneous rows
and prose to avoid silently discarding one. Word/PDF may contain both.

`style_document` creates another DOCX/XLSX without rewriting the supplied text.
Rules use real booleans, exact RGB colors and one-based selectors; invalid,
ambiguous, unsupported, out-of-range, or unmatched rules fail explicitly.
Styles: bold, italic, font_color, fill_color, highlight_color. Color names include
English and Chinese aliases. Selectors: header, columns, rows, cells, row_rules,
highlights; Word additionally supports paragraphs. A highlight styles the entire
matching paragraph/cell, not a substring. Word paragraph indices address top-level
body paragraphs including the title. Run shading uses the requested RGB, not an
approximate yellow fallback. Tables in Word require table_index when ambiguous;
Excel workbooks with multiple sheets require sheet_name. Numeric row conditions
use actual numeric cell values, not numbers extracted from arbitrary text.

New XLSX files size columns using CJK-aware widths and wrap text by default;
existing sheet widths change only with explicit auto_width=true. OOXML editing
uses python-docx/openpyxl and may not preserve unsupported vendor extensions;
complex signed/macro/embedded-object documents are not fidelity-certified.
PDF requires an installed embeddable TrueType font with every requested glyph.
Set `AKANE_DOCUMENT_FONT` explicitly, or use the supported Windows SimSun / Linux
AR PL UMing, WenQuanYi or DejaVu locations. No font file is bundled or downloaded.
ReportLab checks embedding rights and embeds the used font subset; unavailable
fonts/glyphs fail structurally, with no silent viewer-dependent CID fallback.
Only one font face is currently used in PDF (not true bold/italic variants).
Non-BMP content is rejected; arbitrary-script shaping and page-perfect
original-format conversion are not promised.

Every operation validates size bounds and publishes a non-empty temporary file
without overwriting an existing output. The caller owns subprocess cancellation,
input authorization and generated-file registration. No file paths are returned
in public result dictionaries; exceptions carry stable reason codes.

Tests: `python -m unittest discover -s plugins/akane_document_writer/tests -v`
(set PYTHONPATH to `plugins/akane_document_writer/src` for a source checkout).
