"""Strict, lossless input validation shared by every renderer."""

from __future__ import annotations

import math
import re
from typing import Any


class DocumentError(ValueError):
    """Stable public reason; never contains input paths or document text."""


FORMATS = frozenset({"txt", "md", "html", "json", "csv", "xlsx", "docx", "pdf", "srt", "lrc", "vtt"})
STYLE_KEYS = frozenset({"bold", "italic", "font_color", "fill_color", "highlight_color"})
SELECTORS = {
    "columns": {"column", "column_index", "letter", "match_header", "header", "index"},
    "rows": {"row", "row_index", "index", "start", "end", "from", "to", "row_start", "row_end"},
    "cells": {"row", "row_index", "column", "column_index", "letter", "match_header", "header"},
    "paragraphs": {"index", "paragraph_index", "contains", "text"},
    "highlights": {"contains", "text"},
    "row_rules": {"where"},
}
COLORS = {
    "red": "FF0000",
    "红": "FF0000",
    "红色": "FF0000",
    "blue": "0000FF",
    "蓝": "0000FF",
    "蓝色": "0000FF",
    "green": "00AA00",
    "绿": "00AA00",
    "绿色": "00AA00",
    "yellow": "FFFF00",
    "黄": "FFFF00",
    "黄色": "FFFF00",
    "orange": "FFC000",
    "橙": "FFC000",
    "橙色": "FFC000",
    "gray": "D9D9D9",
    "grey": "D9D9D9",
    "灰": "D9D9D9",
    "灰色": "D9D9D9",
    "pink": "F4CCCC",
    "粉": "F4CCCC",
    "粉色": "F4CCCC",
    "black": "000000",
    "white": "FFFFFF",
}


def text(value: Any) -> str:
    return "" if value is None else str(value)


def scalar(value: Any) -> Any:
    if value is not None and type(value) not in (str, int, float, bool):
        raise DocumentError("document_cell_type_invalid")
    if isinstance(value, float) and not math.isfinite(value):
        raise DocumentError("document_number_nonfinite")
    if isinstance(value, str):
        if len(value) > 32767:
            raise DocumentError("document_cell_too_long")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
            raise DocumentError("document_control_character")
    return value


def table_rows(value: Any) -> list[list[Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 1000:
        raise DocumentError("document_rows_invalid")
    result = []
    characters = 0
    for row in value:
        if not isinstance(row, list) or not 1 <= len(row) <= 50:
            raise DocumentError("document_columns_invalid")
        result.append([scalar(cell) for cell in row])
        characters += sum(len(text(cell)) for cell in row)
        if characters > 1_000_000:
            raise DocumentError("document_table_too_large")
    return result


def positive_int(value: Any) -> int:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    raise DocumentError("document_selector_index_invalid")


def color(value: Any) -> str:
    if not isinstance(value, str):
        raise DocumentError("document_color_invalid")
    candidate = COLORS.get(value.lower(), value.removeprefix("#"))
    if not re.fullmatch(r"[0-9a-fA-F]{6}", candidate):
        raise DocumentError("document_color_invalid")
    return candidate.upper()


def style(value: Any, selectors: set[str] | frozenset[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or set(value) - STYLE_KEYS - selectors:
        raise DocumentError("document_style_invalid")
    result = dict(value)
    for key in STYLE_KEYS & value.keys():
        if key in {"bold", "italic"}:
            if type(value[key]) is not bool:
                raise DocumentError("document_style_boolean_invalid")
        else:
            result[key] = color(value[key])
    if not STYLE_KEYS & value.keys():
        raise DocumentError("document_style_empty")
    return result


def formatting(value: Any, output_format: str) -> dict:
    if value is None or value == {}:
        return {}
    if output_format not in {"docx", "xlsx"}:
        raise DocumentError("document_formatting_unsupported")
    options = {"sheet_name", "auto_width"} if output_format == "xlsx" else {"table_index"}
    if not isinstance(value, dict) or set(value) - set(SELECTORS) - {"header", "table_header"} - options:
        raise DocumentError("document_formatting_invalid")
    if "header" in value and "table_header" in value:
        raise DocumentError("document_selector_ambiguous")
    result = {}
    for key, item in value.items():
        if key in {"header", "table_header"}:
            result["header"] = style(item)
        elif key in SELECTORS:
            if output_format == "xlsx" and key == "paragraphs":
                raise DocumentError("document_paragraphs_unsupported")
            if not isinstance(item, list) or not 1 <= len(item) <= 120:
                raise DocumentError("document_style_rules_invalid")
            result[key] = [style(rule, SELECTORS[key]) for rule in item]
        elif key == "auto_width":
            if type(item) is not bool:
                raise DocumentError("document_style_boolean_invalid")
            result[key] = item
        elif key == "table_index":
            result[key] = positive_int(item)
        elif key == "sheet_name":
            if not isinstance(item, str) or not item or len(item) > 31:
                raise DocumentError("document_sheet_invalid")
            result[key] = item
    return result


def one(rule: dict, names: tuple[str, ...], *, required: bool = True) -> Any:
    present = [name for name in names if name in rule]
    if len(present) > 1:
        raise DocumentError("document_selector_ambiguous")
    if not present:
        if required:
            raise DocumentError("document_selector_missing")
        return None
    return rule[present[0]]


def column(rule: dict, headers: list[Any]) -> int:
    names = ("column_index", "index", "column", "letter", "match_header", "header")
    selected = one(rule, names)
    key = next(key for key in names if key in rule)
    if key in {"column_index", "index"} or type(selected) is int:
        index = positive_int(selected)
    elif key in {"match_header", "header"}:
        matches = [n for n, cell in enumerate(headers, 1) if text(cell) == selected]
        if not isinstance(selected, str) or not selected or len(matches) != 1:
            raise DocumentError("document_header_not_unique")
        index = matches[0]
    elif isinstance(selected, str) and re.fullmatch(r"[A-Za-z]{1,3}", selected):
        index = 0
        for letter in selected.upper():
            index = index * 26 + ord(letter) - ord("A") + 1
    elif isinstance(selected, str) and selected.isdigit():
        index = positive_int(selected)
    else:
        return column({"match_header": selected}, headers)
    if not 1 <= index <= len(headers):
        raise DocumentError("document_column_out_of_range")
    return index


def row_range(rule: dict, count: int) -> range:
    single = one(rule, ("row", "row_index", "index"), required=False)
    first = one(rule, ("start", "from", "row_start"), required=False)
    last = one(rule, ("end", "to", "row_end"), required=False)
    if single is not None:
        if first is not None or last is not None:
            raise DocumentError("document_selector_ambiguous")
        first = last = single
    first = positive_int(first)
    last = positive_int(last) if last is not None else first
    if not 1 <= first <= last <= count:
        raise DocumentError("document_row_out_of_range")
    return range(first, last + 1)


def row_matches(rule: dict, values: list[Any], headers: list[Any]) -> bool:
    where = rule.get("where")
    operators = {"eq", "ne", "lt", "lte", "gt", "gte", "contains"}
    if not isinstance(where, dict) or set(where) - operators - {"column", "match_header"}:
        raise DocumentError("document_condition_invalid")
    ops = set(where) & operators
    if len(ops) != 1:
        raise DocumentError("document_condition_invalid")
    col = column({k: v for k, v in where.items() if k not in operators}, headers)
    actual, op = values[col - 1], next(iter(ops))
    expected = scalar(where[op])
    if op == "contains":
        if not isinstance(expected, str) or not expected:
            raise DocumentError("document_condition_invalid")
        return expected in text(actual)
    if op in {"eq", "ne"}:
        same = (
            type(actual) is type(expected) or type(actual) in (int, float) and type(expected) in (int, float)
        ) and actual == expected
        return same if op == "eq" else not same
    if type(expected) not in (int, float):
        raise DocumentError("document_condition_invalid")
    if type(actual) not in (int, float):
        return False
    return {"lt": actual < expected, "lte": actual <= expected, "gt": actual > expected, "gte": actual >= expected}[op]
