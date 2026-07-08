# Petdesk Startup Gate M46

Status: implemented in this slice.
Date: 2026-07-08

## Goal

M46 turns the M45 first-paint fix into an automated gate.

M45 proved that `petdesk-runtime` can avoid the placeholder portrait on startup
when Akane provides:

```text
/pet/health.runtimeEnv.VITE_PETDESK_RESOURCE_MANIFEST_URL
-> /pet/resource-manifest
-> /pet/snapshot.visual.assetHandle
-> staticImages[assetHandle]
-> fetchable /petdesk-character-packs/... image
```

Before M46, `SmokeOnly` mainly checked `/pet/turn`, display stream events, and
audio. A regression could still break startup resource resolution while the
turn stream smoke stayed green.

## Sources Checked

- `docs/petdesk_akane_bridge_m32.md`
- `docs/petdesk_akane_mvp_closeout_m41.md`
- `scripts/start_petdesk_runtime.ps1`
- `scripts/tools/run_petdesk_mvp_smoke.py`
- `tests/test_petdesk_mvp_smoke.py`
- `tests/test_petdesk_runtime_starter.py`
- `tests/test_petdesk_bridge.py`

## Decision

Add a startup visual gate to `scripts/tools/run_petdesk_mvp_smoke.py`.

The smoke now checks, before posting a turn:

1. `/pet/health` is ready and advertises the normal endpoints.
2. `runtimeEnv.VITE_PETDESK_RESOURCE_MANIFEST_URL` is present.
3. The resource manifest endpoint returns a `staticImages` bucket.
4. `/pet/snapshot` returns a display envelope with a static portrait
   `assetHandle`.
5. That `assetHandle` exists in the startup manifest.
6. The mapped image URL is under a browser-fetchable petdesk resource prefix.
7. The mapped image can be fetched and returns a non-empty `image/*` response.

`-SmokeOnly` and `-RunSmokeBeforeLaunch` reuse this because they already call
the Python smoke script. No new backend protocol is added.

## Boundary

In scope:

- stronger Python smoke validation;
- fake HTTP smoke tests for success and failure modes;
- starter test lock that the manifest env key remains whitelisted;
- this implementation note.

Out of scope:

- launching or screenshotting the Tauri runtime;
- changing `/pet/*` contracts beyond the existing M45 startup manifest field;
- changing QQ, memory, prompts, TTS provider selection, or character-pack
  authoring.

## Validation Plan

```powershell
python -m unittest tests.test_petdesk_mvp_smoke tests.test_petdesk_runtime_starter -v
$env:PYTHONPATH='F:\Akane\capcore;F:\Akane\capcore-adapter-mcp;F:\Akane\capcore-adapter-python;F:\Akane\capcore-adapter-speech;F:\Akane\capcore-adapter-comfyui;F:\Akane\charpack-core;F:\Akane\promptpack-core;F:\Akane\capcore-provider-native-tools;F:\Akane\capcore-provider-openai;F:\Akane\memcore'; python -m unittest tests.test_petdesk_bridge -v
python -m ruff check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
python -m ruff format --check scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
python -m py_compile scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
git diff --check -- docs\petdesk_startup_gate_m46.md scripts\tools\run_petdesk_mvp_smoke.py tests\test_petdesk_mvp_smoke.py tests\test_petdesk_runtime_starter.py
```

Manual/live check:

```powershell
.\start_akane_petdesk.ps1 -SkipBackend -BackendUrl http://127.0.0.1:9999 -SmokeOnly
```

The command must fail if the startup manifest and snapshot asset handle no
longer line up, even if `/pet/turn` still streams a display later.
