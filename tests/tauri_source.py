"""Read Rust modules reachable from the native production entry."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet_next/src-tauri/src"
DECLARATIONS = re.compile(
    r"(?P<attrs>(?:^#\[[^\n]+\]\n)*)"
    r"^(?:pub(?:\([^)]*\))? )?mod (?P<name>\w+);", re.MULTILINE,
)


def production_sources() -> list[tuple[Path, str]]:
    entry = ROOT / "main.rs"
    pending, seen, sources = [entry], set(), []
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        source = path.read_text(encoding="utf-8")
        sources.append((path, source))
        directory = path.parent if path == entry or path.name == "mod.rs" else path.with_suffix("")
        children = []
        for declaration in DECLARATIONS.finditer(source):
            name = declaration["name"]
            if name in {"test_support", "tests"} or name.endswith("_tests") or "cfg(test)" in declaration["attrs"]:
                continue
            child = directory / f"{name}.rs"
            children.append(child if child.exists() else directory / name / "mod.rs")
        pending.extend(reversed(children))
    return sources


def read_tauri_source() -> str:
    return "\n\n".join(source for _, source in production_sources())


def read_tauri_function(name: str) -> str:
    pattern = rf'^(?:pub(?:\([^)]*\))? )?(?:async )?(?:unsafe )?(?:extern "system" )?fn {re.escape(name)}\b'
    for _, source in production_sources():
        match = re.search(pattern, source, re.MULTILINE)
        if match:
            end = re.search(r"^}", source[match.end():], re.MULTILINE)
            if end:
                return source[match.start():match.end() + end.end()]
    raise AssertionError(f"Production Rust function missing: {name}")
