# AkaneCompanionLab

> **v0.1.0-alpha.1 / Learning and research preview**

AkaneCompanionLab is an experimental companion-character system containing a
FastAPI backend, static Web client, Windows-first Tauri desktop pet, character
workshop, and optional local integrations.

This release is not recommended for production. Windows now has a repeatable
bootstrap and single launch entry, while packaged desktop installers and
Linux/macOS desktop support are still pending.

## Status

- Backend: usable for local learning and experiments
- Web client: usable; public exports contain placeholder media
- Tauri desktop pet: Alpha, primarily tested on Windows/WebView2
- QQ/NapCat: optional and disabled by default
- TTS, ASR, vision, retrieval, and local tools: optional
- Installer: not provided as a supported release artifact yet

## Windows Quick Start

Install Python 3.11 or newer, then double-click:

```text
启动_Akane.bat
```

The first run creates `.venv`, installs Python dependencies, and creates
`.env`. Run the same launcher again — if no model is configured yet, the
desktop app automatically opens the Control Center model page. Enter your LLM
API key or external Ollama endpoint there and save; no manual `.env` editing
is required to get started.

`Auto` mode reuses a desktop build already produced inside the current source
checkout, builds the Tauri app when Node.js and Rust are installed, and
otherwise falls back to the Web client.

```powershell
.\start_akane.bat -Mode Web
.\start_akane.bat -Mode Desktop
```

The project does not bundle a local LLM. Local inference is provided through
external services such as Ollama and depends on the selected model and hardware.

The shared backend and character-pack protocol serve Web, Windows desktop, and
optional QQ adapters. This does not mean native desktop support has feature
parity across Windows, Linux, and macOS.

## Minimal Backend Setup

Python 3.11 is recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -r requirements.txt
# Linux/macOS:
./.venv/bin/python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, configure at least one LLM API key, and keep the
minimal optional features disabled:

```dotenv
EMBEDDING_PROVIDER=hashed
ENABLE_VECTOR_MEMORY=false
QQ_BRIDGE_ENABLED=false
```

Run:

```bash
python launch_akane_memory_v01.py
```

The default backend is `http://127.0.0.1:9999`.

## Verification

```bash
python -m unittest discover tests
npm --prefix desktop_pet_next ci
npm --prefix desktop_pet_next run verify:control-center
cargo check --manifest-path desktop_pet_next/src-tauri/Cargo.toml
```

## Public Export

Do not publish the private development repository history directly. It may
contain removed media and local-only assets. On Windows, create a sanitized,
history-free public snapshot with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\export_public_alpha.ps1
```

The export excludes private data, copyrighted audio, Live2D sample data, and
media without documented redistribution rights. Neutral placeholder images
keep the learning and build paths intact.

Sanitization is not the same as product completeness. Before public promotion,
review `docs/productization_release_gate_v1.md`; GPT-SoVITS, MCP, music,
QQ/NapCat, and local workflows should be described according to their gate
status rather than marketed as finished product features.

## License

Source code, scripts, tests, and original technical documentation that the
project has the right to license are available under Apache License 2.0.

Artwork, characters, scenes, audio, Live2D models, trademarks, third-party
media, and user-provided character packs are not automatically covered. See
`ASSETS_LICENSE.md` and `THIRD_PARTY_NOTICES.md`.

The Chinese `README.md` is the primary documentation for this Alpha.
