# Akane next package extraction audit v0

Status: audit / implementation planning
Date: 2026-07-01

## Purpose

This document records the next extraction decision after the `memcore` and
`capcore-*` ecosystem cleanup. It is written as a continuation guide for AI
agents: read this before creating the next independent package from Akane.

The goal is to extract reusable product-independent code, not to split code
for its own sake. The best next package should:

- have a clear host boundary;
- be useful outside Akane;
- avoid dragging FastAPI routes, UI, secrets, user data, or local runtime logs;
- have existing tests that can be moved with low risk;
- reduce Akane iteration load after extraction.

## Current Recommendation

Recommended next package:

```text
charpack-core
```

Suggested import package:

```python
import charpack_core
```

Working scope:

- character pack id safety;
- character pack metadata reading;
- resource manifest scanning for character outfits, emotions, scenes, BGM, and
  prompt-visible notes;
- visual output normalization against real available assets;
- character context library discovery and loading;
- persona/resource prompt context builders that remain host-neutral.

This is the best next extraction target because it is already close to a
standalone runtime library. It mostly uses `pathlib`, `json`, `re`, and plain
dict/list structures. It is used by desktop pet, QQ, vision, routes, and tests,
but it does not need FastAPI, model providers, capcore, memcore, or the Akane
store to perform its core job.

## Evidence Collected

High-signal files:

| File | Lines | Notes |
| --- | ---: | --- |
| `companion_v01/resource_manifest.py` | 1342 | Pure stdlib/Path resource scanning and normalization. Strong package core. |
| `companion_v01/desktop_pet_character_resources.py` | 553 | Character pack metadata, persona/resource prompt context, QQ/care/voice hints. Depends on resource manifest and context library. |
| `companion_v01/character_context_library.py` | about 250+ | Creator-authored Markdown context library discovery/load. Pure stdlib/Path. |

Existing focused tests:

| Test file | Test count | Notes |
| --- | ---: | --- |
| `tests/test_resource_manifest.py` | 12 | Manifest scanning, legacy scene layout, emotion aliases, runtime visual bundle. |
| `tests/test_desktop_pet_character_resources.py` | 10 | Pack id safety, pack manifests, persona context, QQ delivery config, voice hints. |
| `tests/test_character_context_library.py` | 9 | Context library prompt catalog, private folder rejection, batch load, tool integration checks. |

Important current imports:

```text
resource_manifest.py
  -> stdlib only

character_context_library.py
  -> stdlib only

desktop_pet_character_resources.py
  -> CharacterContextLibraryService
  -> ResourceManifest
```

Akane integration points currently referencing these pieces:

- `companion_v01/app.py`
- `companion_v01/engine.py`
- `companion_v01/routes/core.py`
- `companion_v01/routes/control_center.py`
- `companion_v01/routes/qq.py`
- `companion_v01/vision_service.py`
- `companion_v01/engine_services/response_builder.py`
- `desktop_pet_next/src-tauri/src/main.rs`
- `desktop_pet_next/src/workshop.js`
- `desktop_pet_creator_kit/scripts/*`

The frontend/Rust/creator-kit pieces should not move into the package. They are
hosts/editors that produce or consume the pack format.

## Why Not Generated Files First

`generated_files` and attachment/task/artifact layers are valuable, but they are
not the best next extraction.

Evidence:

| Area | Files | Coupling |
| --- | --- | --- |
| generated files | `generated_files.py` 2815 lines, `generated_files_media.py` 2386 lines, delivery/cards/io helpers | Depends on `MemoryStore`, `AttachmentInboxService`, media subprocess tools, tool handler behavior, client delivery semantics. |
| attachment inbox | `attachment_inbox.py` 1670 lines, `attachment_ingest.py` 1887 lines | Depends on `MemoryStore`, workspace/file materialization, remote media download policy, document parsers. |
| task workspace | `task_workspace.py` 697 lines | Conceptually reusable, but persistence is currently pure `MemoryStore` methods. |
| artifact containers | `artifact_system/service.py` about 450 lines | Useful, but currently writes gift-asset/store fields and Akane-specific container semantics. |

Existing test mass also shows extraction weight:

- `tests/test_generated_files.py`: 47 tests, 2218 lines.
- `tests/test_attachment_inbox.py`: 19 tests, 847 lines.
- `tests/test_task_workspace.py`: 11 tests, 555 lines.
- `tests/test_artifact_system.py`: 6 tests, 514 lines.

These are good future packages, but they need a storage-port design first.
Extracting them now would either copy too much Akane store code or produce a
library that is only nominally independent.

## Proposed Package Boundary

`charpack-core` owns:

- read-only character pack runtime interpretation;
- path-safe pack id and context target handling;
- resource manifest schema generation;
- manifest merge/runtime projection;
- model-visible prompt context for available resources and declared character
  context libraries;
- deterministic normalization from model output to real existing assets.

Akane keeps:

- Tauri commands for creating/importing/exporting/editing packs;
- desktop pet renderer and layout editor UI;
- FastAPI routes;
- QQ delivery side effects;
- memory isolation and session ownership;
- current visual state persistence;
- capcore tool handlers, unless a later host-neutral tool adapter is proven;
- care/shop runtime effects;
- voice provider resolution and actual TTS runtime.

The package may expose QQ/care/voice metadata as safe declarative data, but it
must not send QQ messages, mutate care state, or invoke a voice provider.

## Initial Public API Sketch

Stable-ish names for the first implementation:

```python
from charpack_core import (
    CharacterContextLibraryService,
    CharacterPackResourceService,
    ResourceManifest,
    sanitize_character_pack_id,
)
```

Compatibility aliases may be useful during migration:

```python
DesktopPetCharacterResourceService = CharacterPackResourceService
```

This avoids forcing Akane to rename every internal use in the first pass.

Suggested modules:

```text
charpack-core/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── charpack_core/
│   ├── __init__.py
│   ├── py.typed
│   ├── resource_manifest.py
│   ├── character_resources.py
│   └── context_library.py
├── docs/
│   └── usage_flow_v0.md
├── examples/
│   └── minimal_character_pack.py
└── tests/
    ├── test_resource_manifest.py
    ├── test_character_resources.py
    └── test_context_library.py
```

Dependencies should start as stdlib-only. Do not depend on Akane, capcore,
memcore, FastAPI, Tauri, or media packages.

## Migration Plan

Phase 0: package skeleton

1. Create `F:/Akane/charpack-core`.
2. Add `pyproject.toml`, `README.md`, `AGENTS.md`, docs, examples, tests.
3. Move/copy `resource_manifest.py` with minimal rename changes.
4. Move/copy `character_context_library.py`.
5. Move/copy `desktop_pet_character_resources.py` as
   `character_resources.py`, with class alias for compatibility.
6. Move focused tests and update imports.
7. Run package validation.

Phase 1: Akane integration

1. Replace Akane imports with package imports:

   ```python
   from charpack_core import ResourceManifest
   from charpack_core import CharacterPackResourceService as DesktopPetCharacterResourceService
   ```

2. Keep thin compatibility modules in `companion_v01/` only if the migration
   diff becomes too noisy.
3. Keep Tauri/creator-kit validation unchanged in this pass.
4. Run focused Akane tests:

   ```bash
   python -m unittest tests.test_resource_manifest tests.test_desktop_pet_character_resources tests.test_character_context_library -v
   python -m unittest tests.test_desktop_pet_backend_contract tests.test_backend_route_modules tests.test_vision_service -v
   git diff --check
   ```

Phase 2: release-state cleanup

1. Add package AGENTS usage guide.
2. Add example character pack in tests/examples without private assets.
3. Initialize git repo and commit.
4. Commit Akane integration separately.

## Validation Commands

For `charpack-core`:

```bash
uv run --extra dev python -m unittest discover -s tests -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run python examples/minimal_character_pack.py
uv run --extra dev python -m build
git diff --check
```

For Akane integration:

```bash
python -m unittest tests.test_resource_manifest tests.test_desktop_pet_character_resources tests.test_character_context_library -v
python -m unittest tests.test_desktop_pet_backend_contract tests.test_backend_route_modules tests.test_vision_service -v
git diff --check
```

If full Akane regression is affordable:

```bash
python -m unittest tests.quick_regression_suite
```

## Open Questions

1. Package name:
   - Recommended: `charpack-core` / `charpack_core`.
   - Alternative: `akane-character-pack` if the package should explicitly own
     the `akane.character.v0.x` schema name.
2. Whether to rename `DesktopPetCharacterResourceService` immediately.
   - Recommended: expose a new neutral name and keep the old name as an alias.
3. Whether context-library tool handling should move.
   - Recommended: no. Move only the service. Keep capcore/tool handler wiring in
     Akane until another host needs it.
4. Whether care/shop and QQ delivery config should be parsed by the package.
   - Recommended: parse and expose declarative metadata only. Effects and
     delivery remain host-owned.

## Stop Conditions

Stop and reconsider if the extraction starts to include:

- FastAPI route code;
- Tauri commands or Rust file-writing code;
- QQ sending / NapCat gateway behavior;
- actual TTS provider invocation;
- memory/session store ownership;
- generated file or attachment storage;
- UI layout editor code.

Those are host/product concerns. The package should remain a read-only runtime
interpreter for character packs and their prompt/resource projections.

## Recommended Next Action

Start `F:/Akane/charpack-core` as a new package, copy the three focused modules
and tests, and get package validation green before touching Akane imports.
