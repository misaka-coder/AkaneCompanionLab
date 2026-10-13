"""Read the composed pet source for legacy structural contracts.

Ports are normalized only for name-based assertions inherited from main.js.
Behavior and dependency wiring are covered by scripts/pet-runtime-smoke.mjs.
Use read_pet_module for assertions about boundaries or initialization order.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PET = ROOT / "desktop_pet_next/src/pet"


def read_workspace_source() -> str:
    """Follow the workspace entry and only its reachable feature modules."""
    namespace = (ROOT / "desktop_pet_next/src/workspace").resolve()
    pending = [ROOT / "desktop_pet_next/src/workspace.js"]
    seen, sources = set(), []
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        source = path.read_text(encoding="utf-8")
        sources.append(source)
        for relative in re.findall(r'\bfrom\s+["\'](\.[^"\']+)["\']', source):
            dependency = (path.parent / relative).resolve()
            if dependency.is_relative_to(namespace):
                pending.append(dependency)
    return "\n".join(sources)


def read_control_center_bridge_source() -> str:
    """Follow only the production bridge facade's reachable implementation."""
    namespace = (ROOT / "desktop_pet_next/src/control-center-v2/bridge").resolve()
    pending = [ROOT / "desktop_pet_next/src/control-center-v2/bridge.js"]
    seen, sources = set(), []
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        source = path.read_text(encoding="utf-8")
        sources.append(source)
        for relative in re.findall(r'\bfrom\s+["\'](\.[^"\']+)["\']', source):
            dependency = (path.parent / relative).resolve()
            if dependency.is_relative_to(namespace):
                pending.append(dependency)
    return "\n".join(sources)


def read_workshop_source() -> str:
    """Read only workshop modules reachable from the production entry."""
    namespace = (ROOT / "desktop_pet_next/src/workshop").resolve()
    pending = [ROOT / "desktop_pet_next/src/workshop.js"]
    seen, sources = set(), []
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        source = path.read_text(encoding="utf-8")
        sources.append(source)
        for relative in re.findall(r'\bfrom\s+["\'](\.[^"\']+)["\']', source):
            dependency = (path.parent / relative).resolve()
            if dependency.is_relative_to(namespace):
                pending.append(dependency)
    return "\n".join(sources)


def read_pet_module(name: str) -> str:
    return (PET / name).read_text(encoding="utf-8")


def read_realtime_voice_source() -> str:
    """Read the client facade and only its reachable feature implementation."""
    namespace = (ROOT / "desktop_pet_next/src/realtime-voice").resolve()
    pending = [ROOT / "desktop_pet_next/src/realtime-voice-client.js"]
    seen, sources = set(), []
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        sources.append(text)
        for relative in re.findall(r'\bfrom\s+["\'](\.[^"\']+)["\']', text):
            dependency = (path.parent / relative).resolve()
            if dependency.is_relative_to(namespace):
                pending.append(dependency)
    return "\n".join(sources)


def read_pet_runtime_source() -> str:
    sources = []
    for path in sorted(PET.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"\b[A-Za-z]+Port\.", "", text)
        text = re.sub(r"^  ", "", text, flags=re.MULTILINE)
        sources.append(text)
    return "\n\n".join(sources)
