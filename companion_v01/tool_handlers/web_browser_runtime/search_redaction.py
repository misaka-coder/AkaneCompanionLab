"""Private provider values redacted before rendering or fingerprinting."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping


class SearchRedactionMixin:
    def _redaction_terms_for_server(self, server: Mapping[str, Any]) -> list[str]:
        terms: list[str] = []
        headers = server.get("headers") if isinstance(server.get("headers"), Mapping) else {}
        for key, value in headers.items():
            if any(marker in str(key).lower() for marker in ("authorization", "api-key", "api_key", "password", "secret", "token", "cookie")):
                text = str(value or "")
                terms.append(text)
                if text.lower().startswith(("bearer ", "basic ")):
                    terms.append(text.partition(" ")[2].strip())
        args = [str(item or "") for item in server.get("args") or []]
        wanted = {match.group(1) for arg in args for match in re.finditer(r"\$\{([A-Z_][A-Z0-9_]{0,79})\}", arg)}
        raw_env = server.get("env") if isinstance(server.get("env"), Mapping) else {}
        for key in wanted:
            env_value = os.environ.get(key)
            if env_value:
                terms.append(env_value)
        for key, value in raw_env.items():
            if any(marker in str(key).lower() for marker in ("api_key", "password", "secret", "token")):
                terms.append(str(value or ""))
        for env_path in self._candidate_env_files(str(server.get("cwd") or "").strip() or None):
            try:
                if not env_path.is_file() or env_path.stat().st_size > 128 * 1024:
                    continue
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() in wanted:
                        terms.append(value.strip().strip("\"'"))
            except (OSError, UnicodeError):
                continue
        return list(dict.fromkeys(term for term in terms if len(term) >= 4))

    def _candidate_env_files(self, cwd: str | None) -> list[Path]:
        paths: list[Path] = []
        if cwd:
            paths.append(Path(cwd) / ".env")
        paths.append(Path.cwd() / ".env")
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _sanitize_output(self, value: str, *, redaction_terms: list[str]) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        for term in redaction_terms:
            if term:
                text = text.replace(term, "[redacted]")
        text = re.sub(r"(?i)authorization:\s*bearer\s+[^\s]+", "Authorization: Bearer [redacted]", text)
        text = re.sub(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
        return text.strip()
