# Akane document writer

Independent document-generation and non-destructive styling library with an
optional public-SDK plugin. The library has no host imports, hidden LLM call,
model credentials, source-preview reconstruction, or in-place edits.

The repository market contains `akane.document-writer`, exposing `compose.v1`,
`revise.v1` and `style.v1` capabilities under that namespace only while installed
and enabled. Each runs as a cancellable long task in a renderer child process.
Only prompt invocation, session resource reading and managed artifact writing
permissions are requested. No installation into a user's profile is implicit.
Install the declared dependencies into the worker interpreter; optional
`AKANE_DOCUMENT_PYTHON` selects it. Health checks really write/read small Word,
Excel and PDF files, including embedded Chinese text. Missing dependencies fail
explicitly; they are never downloaded automatically.

Compose requires `output_format` and complete `content_markdown`/`table_rows`, or
`source_mode=full_text` with `source_ids` and no replacement body. The default
`reference` mode only associates sources; it does not generate content from them.
Full-text mode uses the public resource representation and rejects incomplete
extraction. It preserves paragraph/table order and literal source punctuation,
but does not reproduce page layout. Multiple/mixed tables cannot silently become
one CSV/XLSX. Formula/date exports report their text conversion. Subtitle timing
cannot be invented by format conversion.

Revise requires an explicit generated-file handle and complete replacement
content; style requires a DOCX/XLSX resource handle and actual formatting rules.
Revisions preserve the old bytes and register real version/source lineage through
the host. `send_to_user` defaults to false; true requests host delivery, and a
registered artifact is not evidence of a successful send. Desktop/QQ rendering,
memory projection, delivery and file storage remain host-owned.

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
For example, `{"row_rules":[{"where":{"match_header":"数量","gte":1},"font_color":"red"}]}`
styles rows whose numeric quantity is at least one. A condition uses `column` or
`match_header` and exactly one of `eq/ne/lt/lte/gt/gte/contains`.

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
Installed SDK/discovery/lifecycle/Job tests:
`python -m unittest tests.test_document_writer_plugin -v`.
