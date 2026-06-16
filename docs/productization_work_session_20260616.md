# Productization Work Session - 2026-06-16

This is the working handoff for the current productization pass. It exists so
future context compaction does not lose the product decision behind the code.

## Current Decision

Do not publish AkaneCompanionLab as a broad public release yet.

The issue is not that advanced features should be removed. The issue is that
several already-built features are not yet product-shaped enough for public
users:

- users cannot always tell where to configure a feature;
- success/failure states are not always obvious;
- optional integrations do not always have a guided setup path;
- some features have code paths but not enough visible diagnostics;
- public wording can accidentally sound more complete than the current product.

The goal is to **productize existing features**, not to hide or discard them.

## Reference Project Lesson

AstrBot is the local reference project at `F:\AstrBot\AstrBot`.

Useful patterns observed:

- provider types are separated into clear pages/tabs;
- adding a provider starts from templates, then structured config;
- MCP has a dedicated manager: add/edit/delete, enable, test connection,
  discover tools, list tools, sync external servers;
- plugin/capability UI shows installed vs marketplace vs components rather
  than flattening everything into one advanced settings area;
- operational state is visible through console, trace, status chips, and
  failure messages.

Akane should not copy AstrBot's product. Akane is a companion/desktop-pet
system. The lesson is the configuration and diagnostics discipline.

## Documents Added

- `docs/productization_release_gate_v1.md`
  - Product gate for every major Akane capability.
  - Defines Ready / Alpha / Productization Gap / Experimental.
  - Lists product gates for GPT-SoVITS, MCP, music, QQ, workflows, memory,
    desktop pet, character packs, and model configuration.
- `docs/open_source_readiness_v1.md`
  - Clarifies that sanitized source export is not product completeness.
- `PUBLIC_RELEASE.md`, `README.md`, `README_EN.md`
  - Point to the productization gate.

The public audit now requires `docs/productization_release_gate_v1.md`.

Latest audited source snapshot:

- `F:\Temp\AkaneCompanionLab-public-alpha-20260616-115538`

This snapshot is for internal smoke testing, not a main announcement.

## Current Product Claims

Allowed:

- Windows-first source Alpha.
- Backend/Web can run from source on other platforms.
- Visible model configuration exists.
- Desktop pet is Alpha.
- GPT-SoVITS, MCP, QQ, music, and workflows are optional/productization-in-progress.

Avoid:

- "one-click installer"
- "full cross-platform desktop app"
- "built-in GPT-SoVITS"
- "full MCP platform"
- "complete music companion"
- "QQ works out of the box"

## First Productization Targets

### 1. Capability Inventory / Ability Status

Make the control center show a clear status inventory for major capabilities:

- Core chat/model
- Memory
- Character packs
- Desktop pet
- GPT-SoVITS
- MCP
- Music
- QQ/NapCat
- Local workflows

Each row should answer:

- what it is for;
- status: ready / alpha / needs setup / experimental / unavailable;
- where to configure it;
- how to test it;
- what external dependency it needs.

This should be visible enough that users do not have to infer product state from
source code or scattered README lines.

### 2. MCP Manager

Move MCP from "can save config" toward a real manager:

- add custom server;
- templates for stdio / streamable HTTP / SSE;
- test connection;
- discover tools;
- list tools;
- enable/disable/delete/edit;
- show prompt exposure / approval state;
- show failure reasons without leaking secrets.

### 3. GPT-SoVITS Guided Flow

Make one guided path:

1. set external endpoint;
2. health check;
3. inspect model/profile folder;
4. save voice profile;
5. short TTS test;
6. assign to current character;
7. send a desktop reply and see provider/fallback status.

The UI must say Akane does not bundle GPT-SoVITS.

### 4. Music Stabilization

Before advertising "listen together", stabilize and test:

- local queue empty/loaded/playing/paused/ended/cleared;
- system media present/stale/unavailable;
- lyrics found/not found;
- prompt context must not claim playback succeeded when only a search page was
  opened.

## Verification Baseline

Use these after each productization slice:

```powershell
python -m unittest tests.test_windows_bootstrap tests.test_desktop_pet_frontend_contract tests.test_user_data_paths
cd desktop_pet_next
npm run verify:control-center
cargo check --manifest-path src-tauri/Cargo.toml
cd ..
git diff --check
```

If a slice only changes docs/audit, narrow tests are acceptable, but always run
`git diff --check`.

Known caveat:

- `cargo fmt --check` currently has preexisting formatting issues in
  `desktop_pet_next/src-tauri/src/main.rs`; do not treat that as caused by a
  productization doc/UI slice unless the Rust code was touched.

## Working Rule

Every feature being polished must gain at least one of:

- clearer configuration;
- clearer status;
- clearer test/check button;
- clearer failure reason;
- clearer docs;
- stronger audit/test guard.

Do not add new integrations until the existing visible integrations are easier
to understand and verify.
