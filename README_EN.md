# AkaneCompanionLab

> **v0.1.0-alpha.1 / Learning and research preview**

AkaneCompanionLab is a desktop companion-character system: a FastAPI backend, a
static Web client, a Windows-first Tauri desktop pet, and a character workshop
for building your own character.

It isn't trying to be a "smarter Q&A AI" — it's trying to let you and a character
turn time together into something everyday. The points below are where it differs
from a wrapper chatbot; each one is backed by actual code, and anything not yet
built is left out.

## What makes it different

**Layered memory that remembers "when."** Conversations settle into three layers:
recent raw messages, compressed episodic summaries, and long-term semantic memory
(who you are, the people and topics you keep bringing up, unresolved commitments).
Long-term memory isn't a flat fact table — it keeps the time range, and recurring
things get "reinforced." All three layers feed into the prompt before each reply,
so the older things she remembers still carry a "when."

**What the character can express is bounded by the resources you give it.**
Expressions, outfits, scenes, and BGM all come from a runtime resource manifest.
Each turn the model only picks from what is currently available, and its output is
normalized back to resources that actually exist — it can't invent an expression
that isn't there. Delete an expression and she genuinely can't show that mood; add
a song and you have one more thing to listen to together.

**A reply is a "performance," not just text.** A single structured output carries
it all at once: what to say, segmented speech bubbles, which expression, whether to
play a voice clip or music, how the relationship state shifts — together driving
the pet's face, bubble, TTS, and music.

**The character is yours.** The workshop lets you create or import characters:
configure the persona, upload and calibrate portraits, switch anytime. A character
pack feeds into the identity prompt, the expression resources, and memory isolated
per character — switching characters swaps a whole set of memory and the world she
can perceive, not just an avatar. Akane is the bundled default demo character.

**Tool calls are validated, executed, and fed back.** When the model wants a tool,
it is validated first; bad arguments or an unknown tool get a readable reason fed
back so it can retry; on success the result is carried into the next turn. One tool
per round, multi-step over multiple rounds, high-risk actions ask for confirmation
— not "I said I did it, so it's done."

**One backend, multiple front-ends.** Web, desktop pet, and QQ share the same turn
engine, but each trims its presentation and available tools to its own mode (for
example, QQ only sends text, voice, and sticker images — no portrait, scene, or BGM
rendering).

## Status note

This release is not recommended for production. Windows now has a repeatable
bootstrap and single launch entry, while packaged desktop installers and
Linux/macOS desktop support are still pending.

## Status

- Backend: usable for local learning and experiments
- Web client: usable; the public export ships the default Akane character art and uses placeholders for other media
- Tauri desktop pet: Alpha, primarily tested on Windows/WebView2
- Character workshop: Alpha — create or import your own characters
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

### What happens on first launch

The first double-click of `启动_Akane.bat` walks through the steps below.
Each step prints an `[INFO] / [OK] / [WARN] / [FAIL]` status line. Long bursts
of `Compiling …`, `Downloading …`, or `Resolving …` are pip / cargo / npm
fetching and building dependencies — they are progress, not errors.

| Stage | What you see | Rough time |
| --- | --- | --- |
| Python environment | `Creating .venv with Python …` then `Installing Python dependencies…` | 3–8 min on first run |
| Desktop pet dependencies | `desktop_pet_next/node_modules not found. Running npm install...` | 1–3 min on first run |
| Tauri desktop build | A `[首次构建提示]` block, then cargo `Compiling akane_desktop_pet_next…` and similar entries | 5–15 min on first run |
| Backend warmup | `Starting backend with: …` / `Backend log: …`; if it's still warming up, the launcher says the desktop pet will open first and reconnect automatically | seconds to tens of seconds |
| Desktop pet launch | `Starting Akane Next desktop app...` / `Akane Next PID: …` | a few seconds, then the pet window appears |
| Model configuration | The Control Center "Model" page opens automatically | depends on what you fill in; takes effect on save |

A few extra things worth knowing:

- The backend listens on `http://127.0.0.1:9999` by default. If the port already
  hosts a managed Akane backend, the launcher restarts it for fresh code. If an
  unknown process owns the port, the launcher keeps it running and asks you to
  free the port or pass `-SkipBackend`.
- Character packs, memories, chat databases, and logs all land in
  `%LOCALAPPDATA%\Akane\`. Uninstall or migration only needs to touch that
  directory. Advanced deployments can point `AKANE_DATA_ROOT` at another
  absolute path.
- The LLM API key is stored at
  `%LOCALAPPDATA%\Akane\users_data\_local\model_service.json` (Git-ignored).
  The `.env` at the repo root is for advanced local config, listed in
  `.gitignore`, and never enters the public export — the public repo ships
  only `.env.example`.
- When startup fails, read the last `[FAIL]` line in the terminal, then check:
  - `%LOCALAPPDATA%\Akane\logs\akane_backend.log`
  - `%LOCALAPPDATA%\Akane\logs\akane_backend.err.log`

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
media without documented redistribution rights. The default Akane (`akane_v1`)
character art ships as documented, redistributable demo assets; every other
image slot uses a neutral placeholder to keep the learning and build paths
intact.

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
