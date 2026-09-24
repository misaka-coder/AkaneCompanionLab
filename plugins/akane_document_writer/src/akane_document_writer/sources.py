"""Adapt the public content representation to the one rendering implementation.

No attachment paths/resolver/host parser imports. This exports complete textual
content, not source layout, images, comments, formulas or spreadsheet behavior.
"""

from __future__ import annotations

import csv
from html import escape
import io
import json
from pathlib import Path

from .markdown import Block, blocks
from .render import render_document
from .validation import DocumentError, table_rows, text


def _rows(value, notices):
    if not isinstance(value, list):
        raise DocumentError("document_material_invalid")
    result = []
    for row in value:
        if not isinstance(row, list):
            raise DocumentError("document_material_invalid")
        cells = []
        for cell in row:
            if isinstance(cell, dict):
                if cell.get("type") == "formula" and isinstance(cell.get("expression"), str):
                    cell = cell["expression"]
                    notices.add("source_formulas_exported_as_text_not_evaluated")
                elif cell.get("type") == "datetime" and isinstance(cell.get("value"), str):
                    cell = cell["value"]
                    notices.add("source_dates_exported_as_iso_text")
                else:
                    raise DocumentError("document_material_cell_unsupported")
            cells.append(cell)
        # An empty CSV record still represents a row, not a missing record.
        result.append(cells or [None])
    return table_rows(result)


def _plain(block: Block) -> str:
    if block.kind == "table":
        output = io.StringIO(newline="")
        csv.writer(output, delimiter="\t", lineterminator="\n").writerows(block.rows)
        return output.getvalue().removesuffix("\n")
    if block.kind in {"bullet", "number"}:
        return block.marker + " " + block.text
    return block.text


def render_sources(
    *, materials: list[dict], output_path: Path, output_format: str, title: str = "", styles=None
) -> dict:
    if not isinstance(materials, list) or not 1 <= len(materials) <= 20:
        raise DocumentError("document_material_invalid")
    adapted: list[Block] = []
    notices = {"source_text_export_not_original_layout"}
    for material in materials:
        if not isinstance(material, dict) or material.get("schema") != "akane.document-material.v1":
            raise DocumentError("document_material_invalid")
        if material.get("complete") is not True or material.get("limitations"):
            raise DocumentError("document_source_incomplete")
        source_blocks = material.get("blocks")
        if not isinstance(source_blocks, list) or len(source_blocks) > 10000:
            raise DocumentError("document_material_invalid")
        for block in source_blocks:
            if not isinstance(block, dict):
                raise DocumentError("document_material_invalid")
            kind = block.get("kind")
            if kind in {"text", "paragraph", "page"}:
                value = block.get("text")
                if not isinstance(value, str):
                    raise DocumentError("document_material_invalid")
                if (
                    kind == "text"
                    and material.get("source_format") in {"md", "markdown"}
                    and output_format in {"docx", "pdf"}
                ):
                    adapted.extend(blocks(value))
                else:
                    adapted.append(Block("paragraph", value, literal=True))
            elif kind in {"table", "sheet"}:
                if kind == "sheet" and output_format not in {"xlsx", "csv"}:
                    name = block.get("name")
                    if not isinstance(name, str):
                        raise DocumentError("document_material_invalid")
                    adapted.append(Block("heading", name, level=2, literal=True))
                adapted.append(Block("table", rows=_rows(block.get("rows"), notices), literal=True))
            else:
                raise DocumentError("document_material_block_unsupported")
    if output_format in {"docx", "pdf"}:
        result = render_document(
            output_path=output_path, output_format=output_format, title=title, source_blocks=adapted, styles=styles
        )
    elif output_format in {"csv", "xlsx"}:
        tables = [block.rows for block in adapted if block.kind == "table"]
        if len(tables) > 1 or (tables and any(block.text for block in adapted if block.kind != "table")):
            raise DocumentError("document_source_table_selection_required")
        rows = tables[0] if tables else [["内容"], *[[block.text] for block in adapted]]
        result = render_document(
            output_path=output_path, output_format=output_format, title=title, rows=rows, styles=styles
        )
    else:
        content = "\n\n".join(_plain(block) for block in adapted)
        if output_format == "json" and not (len(materials) == 1 and materials[0].get("source_format") == "json"):
            content = json.dumps(materials, ensure_ascii=False, indent=2, allow_nan=False)
            notices.add("source_structured_json_export")
        elif output_format == "html" and not (
            len(materials) == 1 and materials[0].get("source_format") in {"html", "htm"}
        ):
            content = '<!doctype html><meta charset="utf-8"><pre>' + escape(content) + "</pre>"
        elif output_format in {"srt", "lrc", "vtt"} and not (
            len(materials) == 1 and materials[0].get("source_format") == output_format
        ):
            raise DocumentError("document_source_timing_required")
        result = render_document(
            output_path=output_path, output_format=output_format, title=title, content=content, styles=styles
        )
    return {**result, "notices": sorted(set(result["notices"]) | notices)}
