"""Count-free patch parsing and unique-context hunk application."""
from __future__ import annotations

import re
from typing import Any

from .definitions import ProjectWorkspaceError, _PATCH_BEGIN, _PATCH_END, _PATCH_FILE_PREFIXES


class ProjectPatchSyntaxMixin:
    @staticmethod
    def _parse_model_patch(text: str) -> list[dict[str, Any]]:
        """Parse the count-free patch language exposed to the model.

        The model states file operations and supplies old/new context, while
        the host derives locations and line counts.  This deliberately avoids
        traditional unified-diff range bookkeeping such as
        ``@@ -12,7 +12,8 @@``: those numbers are useful to ``patch(1)`` but
        are brittle, non-semantic work for a language model.
        """

        lines = text.splitlines(keepends=True)
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines or lines[0].rstrip("\r\n") != _PATCH_BEGIN or lines[-1].rstrip("\r\n") != _PATCH_END:
            raise ProjectWorkspaceError(
                "patch_format_invalid",
                expected="*** Begin Patch ... *** End Patch",
                recommended_action="use_the_count_free_workspace_patch_format",
            )

        patches: list[dict[str, Any]] = []
        index = 1
        end_index = len(lines) - 1
        while index < end_index:
            line = lines[index].rstrip("\r\n")
            if not line.strip():
                index += 1
                continue
            operation, source_path, path = _parse_file_header(line, index)
            hunks: list[dict[str, Any]] = []
            content = ""
            index += 1

            if operation == "update" and index < end_index and lines[index].startswith("*** Move to:"):
                path = lines[index].rstrip("\r\n")[len("*** Move to:") :].strip()
                if not path:
                    raise ProjectWorkspaceError("patch_path_missing", line=index + 1)
                operation = "rename"
                index += 1

            if operation == "create":
                content, index = _parse_added_content(lines, index, end_index, path=path)
            elif operation == "delete":
                if index < end_index and not lines[index].startswith(_PATCH_FILE_PREFIXES):
                    raise ProjectWorkspaceError(
                        "patch_delete_has_body",
                        path=path,
                        line=index + 1,
                        recommended_action="delete_file_headers_do_not_need_file_contents",
                    )
            else:
                hunks, index = _parse_update_hunks(lines, index, end_index, source_path=source_path)
                if operation == "update" and not hunks:
                    raise ProjectWorkspaceError(
                        "patch_update_missing_hunk",
                        path=source_path,
                        recommended_action="add_an_at_at_hunk_or_use_a_move_for_a_content_preserving_rename",
                    )
            patches.append(
                {
                    "operation": operation,
                    "old_path": source_path,
                    "path": path,
                    "hunks": hunks,
                    **({"content": content} if operation == "create" else {}),
                }
            )
        if not patches:
            raise ProjectWorkspaceError(
                "patch_has_no_file_changes",
                recommended_action="add_at_least_one_file_operation_between_the_patch_markers",
            )
        paths = [item["path"] for item in patches]
        paths.extend(
            item["old_path"]
            for item in patches
            if item.get("old_path") and item.get("old_path") != item.get("path")
        )
        if len(paths) != len(set(paths)):
            raise ProjectWorkspaceError("duplicate_patch_target")
        return patches

    @staticmethod
    def _apply_hunks(original: str, hunks: list[dict[str, Any]], *, path: str) -> str:
        source = original.splitlines(keepends=True)
        newline = "\r\n" if "\r\n" in original else "\n"
        output: list[str] = []
        cursor = 0

        def matches_at(start: int, expected_lines: list[str]) -> bool:
            if start < cursor or start + len(expected_lines) > len(source):
                return False
            return all(
                source[start + offset].rstrip("\r\n") == expected
                for offset, expected in enumerate(expected_lines)
            )

        for hunk_index, hunk in enumerate(hunks, start=1):
            old_lines = [
                raw[1:].rstrip("\r\n")
                for raw in hunk["lines"]
                if raw[0] in {" ", "-"}
            ]
            anchor = str(hunk.get("anchor") or "")
            search_start = cursor
            if anchor:
                anchor_candidates = [
                    candidate
                    for candidate in range(cursor, len(source))
                    if source[candidate].rstrip("\r\n") == anchor
                ]
                if len(anchor_candidates) != 1:
                    raise ProjectWorkspaceError(
                        "hunk_not_applicable",
                        path=path,
                        hunk=hunk_index,
                        mismatch="anchor_not_found" if not anchor_candidates else "anchor_ambiguous",
                        anchor=anchor,
                        candidate_lines=[candidate + 1 for candidate in anchor_candidates[:8]],
                        recommended_action=(
                            "reread_the_target_region_and_use_an_existing_anchor"
                            if not anchor_candidates
                            else "use_a_more_specific_anchor_or_add_unique_unchanged_context"
                        ),
                    )
                search_start = anchor_candidates[0] + 1

            if old_lines:
                candidates = [
                    candidate
                    for candidate in range(search_start, len(source) - len(old_lines) + 1)
                    if matches_at(candidate, old_lines)
                ]
                if len(candidates) != 1:
                    raise ProjectWorkspaceError(
                        "hunk_not_applicable",
                        path=path,
                        hunk=hunk_index,
                        mismatch="context_not_found" if not candidates else "context_ambiguous",
                        candidate_lines=[candidate + 1 for candidate in candidates[:8]],
                        recommended_action=(
                            "reread_the_target_region_and_copy_the_current_text_exactly"
                            if not candidates
                            else "add_unchanged_context_or_an_at_at_anchor_to_make_the_hunk_unique"
                        ),
                    )
                start = candidates[0]
            else:
                start = search_start if anchor else len(source)
            if start < cursor or start > len(source):
                raise ProjectWorkspaceError(
                    "hunk_not_applicable",
                    path=path,
                    hunk=hunk_index,
                    mismatch="invalid_insertion_point",
                    recommended_action="reread_the_target_region_and_regenerate_the_hunk",
                )
            output.extend(source[cursor:start])
            cursor = start
            for raw in hunk["lines"]:
                marker = raw[0]
                body = raw[1:]
                body_without_eol = body.rstrip("\r\n")
                if marker in {" ", "-"}:
                    if marker == " ":
                        output.append(source[cursor])
                    cursor += 1
                elif marker == "+":
                    has_eol = body.endswith(("\n", "\r"))
                    output.append(body_without_eol + (newline if has_eol else ""))
        output.extend(source[cursor:])
        return "".join(output)


def _parse_file_header(line: str, index: int) -> tuple[str, str | None, str]:
    operation = ""
    source_path: str | None = None
    path = ""
    if line.startswith("*** Add File:"):
        operation = "create"
        path = line[len("*** Add File:") :].strip()
    elif line.startswith("*** Delete File:"):
        operation = "delete"
        source_path = line[len("*** Delete File:") :].strip()
        path = source_path
    elif line.startswith("*** Update File:"):
        operation = "update"
        source_path = line[len("*** Update File:") :].strip()
        path = source_path
    else:
        raise ProjectWorkspaceError(
            "patch_file_header_expected",
            line=index + 1,
            found=line[:160],
            expected="*** Add File, *** Delete File, or *** Update File",
            recommended_action="start_each_file_change_with_a_patch_file_header",
        )
    if not path:
        raise ProjectWorkspaceError("patch_path_missing", line=index + 1)
    return operation, source_path, path


def _parse_added_content(
    lines: list[str], index: int, end_index: int, *, path: str,
) -> tuple[str, int]:
    content_lines: list[str] = []
    while index < end_index and not lines[index].startswith(_PATCH_FILE_PREFIXES):
        raw = lines[index]
        if not raw.startswith("+"):
            raise ProjectWorkspaceError(
                "patch_add_line_prefix_missing",
                path=path,
                line=index + 1,
                found=raw.rstrip("\r\n")[:160],
                expected="prefix every new file line with +",
                recommended_action="prefix_each_added_line_with_plus",
            )
        content_lines.append(raw[1:])
        index += 1
    content = "".join(content_lines)
    return content, index


def _parse_update_hunks(
    lines: list[str], index: int, end_index: int, *, source_path: str | None,
) -> tuple[list[dict[str, Any]], int]:
    hunks: list[dict[str, Any]] = []
    while index < end_index and not lines[index].startswith(_PATCH_FILE_PREFIXES):
        header = lines[index].rstrip("\r\n")
        if not header.startswith("@@"):
            raise ProjectWorkspaceError(
                "patch_hunk_header_expected",
                path=source_path,
                line=index + 1,
                found=header[:160],
                expected="@@ or @@ <unique anchor line>",
                recommended_action="start_each_update_hunk_with_at_at",
            )
        if re.match(r"^@@\s+-\d", header):
            raise ProjectWorkspaceError(
                "patch_numeric_range_not_allowed",
                path=source_path,
                line=index + 1,
                found=header[:160],
                expected="@@ or @@ <unique anchor line>",
                recommended_action="remove_the_old_and_new_line_ranges_from_the_hunk_header",
            )
        anchor = header[2:].strip()
        hunk = {"anchor": anchor, "lines": []}
        index += 1
        while (
            index < end_index
            and not lines[index].startswith(_PATCH_FILE_PREFIXES)
            and not lines[index].startswith("@@")
        ):
            raw = lines[index]
            if not raw.startswith((" ", "+", "-")):
                raise ProjectWorkspaceError(
                    "patch_line_prefix_invalid",
                    path=source_path,
                    line=index + 1,
                    found=raw.rstrip("\r\n")[:160],
                    expected="space for context, - for removed text, or + for added text",
                    recommended_action="prefix_every_hunk_line_with_space_minus_or_plus",
                )
            hunk["lines"].append(raw)
            index += 1
        if not hunk["lines"]:
            raise ProjectWorkspaceError(
                "patch_hunk_empty",
                path=source_path,
                line=index + 1,
                recommended_action="include_context_and_changed_lines_after_the_hunk_header",
            )
        if not any(raw.startswith(("+", "-")) for raw in hunk["lines"]):
            raise ProjectWorkspaceError(
                "patch_hunk_has_no_changes",
                path=source_path,
                hunk=len(hunks) + 1,
                recommended_action="include_at_least_one_added_or_removed_line",
            )
        hunks.append(hunk)
    return hunks, index
