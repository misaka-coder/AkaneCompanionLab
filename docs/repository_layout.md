# Repository Layout

AkaneCompanionLab keeps public source, tests, docs, and local private research
separate so the repository can be prepared for open-source release without
accidentally shipping runtime data or copyrighted corpora.

## Public Project Files

- `companion_v01/`: FastAPI backend and Akane runtime modules.
- `services/`: shared service clients such as LLM and TTS clients.
- `web/`: legacy/static web frontend served by the backend.
- `desktop_pet/`: frozen Electron desktop pet V0.
- `desktop_pet_next/`: Tauri/WebView2 desktop pet mainline.
- `desktop_pet_creator_kit/`: character pack tooling and templates.
- `tests/`: Python test suite.
- `docs/`: product, architecture, and feature documentation.
- `documents/`: framework/project planning documents and reviewed project assets.
- `deploy/`: deployment examples.
- `maintenance/`: cache cleanup and operational helper scripts.
- `scripts/`: developer utilities that are safe to share.

## Root Entrypoints

The root keeps only common entrypoints and configuration:

- `launch_akane_memory_v01.py`
- `start_akane_next.ps1` / `start_akane_next.bat`
- `start_akane_preview.ps1`
- `start_akane_desktop_pet.ps1`
- `run_quick_regression.ps1`
- `run_qq_workshop_self_check.ps1`
- `config.py`
- `requirements*.txt`
- `README.md`

## Local-Only Areas

- `users_data/`, `runtime_logs/`, temp directories, build outputs, and virtual
  environments are ignored.
- `local_research/` is ignored and should contain raw corpora, private notes,
  generated extraction outputs, local shortcut helpers, and anything not safe
  for an open-source repository.

Before publishing, run `git status --short` and confirm only intentional source,
test, docs, and configuration files are staged.
