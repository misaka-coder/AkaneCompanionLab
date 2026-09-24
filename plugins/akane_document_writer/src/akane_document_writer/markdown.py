"""Small documented Markdown subset, shared by Word/PDF/table export."""

from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class Block:
    kind: str
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)
    marker: str = ""
    literal: bool = False


def cells(line: str) -> list[str]:
    return [
        part.strip().replace(r"\|", "|")
        for part in re.split(r"(?<!\\)\|", line.strip().removeprefix("|").removesuffix("|"))
    ]


def blocks(content: str) -> list[Block]:
    lines = content.splitlines()
    result: list[Block] = []
    index = 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            result.append(Block("paragraph", "\n".join(paragraph)))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            code = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            result.append(Block("code", "\n".join(code)))
        elif not stripped:
            flush()
        elif heading := re.match(r"^(#{1,6})\s+(.+)$", line):
            flush()
            result.append(Block("heading", heading[2], len(heading[1])))
        elif (
            index + 1 < len(lines)
            and "|" in line
            and all(re.fullmatch(r":?-{3,}:?", part) for part in cells(lines[index + 1]))
        ):
            flush()
            rows = [cells(line)]
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                rows.append(cells(lines[index]))
                index += 1
            result.append(Block("table", rows=rows))
            continue
        elif item := re.match(r"^(\s*)([-*+] |[0-9]+[.)] )(.+)$", line):
            flush()
            result.append(
                Block("bullet" if item[2][0] in "-*+" else "number", item[3], len(item[1]) // 2, marker=item[2].strip())
            )
        else:
            paragraph.append(line)
        index += 1
    flush()
    return result


def table_from_content(content: str) -> list[list[str]]:
    tables = [block.rows for block in blocks(content) if block.kind == "table"]
    if len(tables) > 1:
        from .validation import DocumentError

        raise DocumentError("document_multiple_tables_require_explicit_rows")
    return tables[0] if tables else [["内容"], *[[line] for line in content.splitlines()]]
