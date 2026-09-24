# Petdesk Akane Bridge M32

Status: implemented in this slice.

## Goal

Expose a narrow Akane backend bridge for `petdesk-runtime` without replacing or rewriting the existing desktop pet frontend.

The bridge adds the runtime-facing endpoints:

- `GET /pet/health`
- `GET /pet/snapshot`
- `GET /pet/resource-manifest`
- `POST /pet/turn`

It converts existing Akane character resources and final turn output into `petdesk-runtime` contracts:

- runtime resource manifest with `staticImages`
- `pet.display.v1` display envelope
- SSE turn stream events: `resource_manifest`, `display`, `done`

## Sources Checked

- `companion_v01/app.py`
- `companion_v01/routes/core.py`
- `companion_v01/routes/think.py`
- `companion_v01/desktop_pet_character_resources.py`
- `companion_v01/desktop_pet_contract.py`
- `charpack_core/character_resources.py`
- `charpack_core/resource_manifest.py`
- `petdesk-runtime/src/bridge/backend-client.ts`
- `petdesk-runtime/src/renderer/resource-resolver.ts`
- `petcore-protocol/src/index.ts`
- `petdesk-character-host/examples/runtime-bridge-server.ts`
- existing Akane route tests in `tests/test_backend_route_modules.py`

## Boundary

M32 is a bridge layer, not a migration.

This is intentional transitional redundancy. It exists only so old Akane desktop-pet clients and the new
`petdesk-runtime` can run side by side while the new runtime is validated. Do not treat `/pet/*` as a second permanent
desktop-pet protocol surface.

In scope:

- Add a pure Python adapter module for Akane -> petdesk conversion.
- Add a FastAPI router under `/pet`.
- Add `/petdesk-character-packs` as a static alias for the existing Creator Kit character directory because petdesk-runtime's resolver allows this prefix.
- Use current Akane `engine.process_turn(...)` for `POST /pet/turn`.
- Use current `DesktopPetCharacterResourceService` / charpack resource manifests for static portrait assets.

Out of scope:

- Do not change old `/desktop-pet/*`, `/think`, `/resource-manifest`, `/tts`, or legacy desktop frontend behavior.
- Do not embed `petdesk-character-host` Node code into the Python backend.
- Do not implement Live2D model discovery in Akane yet.
- Do not add Tauri launch management or process supervision.
- Do not expose local filesystem paths, API keys, logs, databases, or generated cache paths.

## Redundancy Guard And Retirement Criteria

This bridge must be revisited when any of these happen:

- `petdesk-runtime` can complete a real Akane chat turn from launch to visible static portrait display.
- `petdesk-runtime` can consume Akane character-pack resources without relying on the old frontend.
- TTS/audio and basic interaction state have a replacement path in `petdesk-runtime`.
- The old Electron/Tauri desktop-pet frontend is no longer part of the supported smoke path.

At that point, do one of these explicitly:

- keep `/pet/*` as the sole runtime contract and freeze old `/desktop-pet/*` as compatibility only;
- or remove this Python bridge and replace it with a dedicated host/runtime sidecar contract;
- or document why both surfaces still need to exist, with a dated owner decision.

Do not add new feature branches to both `/desktop-pet/*` and `/pet/*` without choosing which one is canonical. If a
feature must support both during migration, write the shared conversion once and keep one side as a thin adapter.

## Runtime Contract

### `GET /pet/health`

Returns a small JSON object:

```json
{
  "ok": true,
  "status": "ready",
  "version": "akane-petdesk-bridge.m32",
  "snapshot": "/pet/snapshot",
  "turn": "/pet/turn",
  "runtimeEnv": {
    "VITE_PETDESK_RESOURCE_MANIFEST_URL": "/pet/resource-manifest"
  },
  "resourceManifest": { "...": "runtime manifest summary or payload" }
}
```

The response is `Cache-Control: no-store`.

### `GET /pet/snapshot`

Query parameters:

- `character_pack_id` or `characterPackId`
- `user_id` or `session_id`
- `real_user_id`
- optional `outfit`
- optional `emotion`

Returns a valid `pet.display.v1` envelope with a static portrait visual and safe `assetHandle`.

### `GET /pet/resource-manifest`

Query parameters:

- `character_pack_id` or `characterPackId`

Returns the same `petdesk-runtime` resource manifest shape used by the first
`POST /pet/turn` stream `resource_manifest` event. The starter passes this
endpoint to `petdesk-runtime` through `VITE_PETDESK_RESOURCE_MANIFEST_URL` so
the initial `/pet/snapshot` static `assetHandle` can resolve before the first
user turn.

### `POST /pet/turn`

Request body from petdesk-runtime:

```json
{
  "text": "hello",
  "turnId": "optional",
  "metadata": {
    "character_pack_id": "akane_v1",
    "user_id": "desktop",
    "real_user_id": "master"
  }
}
```

Bridge behavior:

1. Validate body is an object and `text` is a non-empty string.
2. Build an Akane turn payload:
   - `message`: request `text`
   - `user_id`: metadata/query fallback, default `petdesk`
   - `real_user_id`: metadata/query fallback, default same as `user_id`
   - `client_mode`: `desktop_pet`
   - `client_capabilities`: speech segments, TTS, audio playback, resource manifest, static sprite
   - `character_pack_id`: metadata/query/default fallback
3. Call `engine.process_turn(payload)`.
4. Convert the final Akane frame into a `pet.display.v1` envelope.
5. Return an SSE stream:
   - `event: resource_manifest`
   - `event: display`
   - `event: done`

## Conversion Rules

### Character Resource Manifest -> Petdesk Manifest

Input is Akane/charpack runtime manifest:

```json
{
  "characters": {
    "outfits": [
      {
        "id": "猫娘",
        "emotions": [
          {
            "id": "开心",
            "path": "/desktop-pet-character-packs/mika_pack/assets/characters/猫娘/开心.png"
          }
        ]
      }
    ]
  },
  "defaults": {
    "outfit": "猫娘",
    "emotion": "开心"
  }
}
```

Output runtime manifest:

```json
{
  "staticImages": {
    "mika_pack/static/outfit-.../emotion-...": {
      "kind": "static_image",
      "handle": "mika_pack/static/outfit-.../emotion-...",
      "url": "/petdesk-character-packs/mika_pack/assets/characters/%E7%8C%AB%E5%A8%98/%E5%BC%80%E5%BF%83.png",
      "source": "akane_character_pack"
    }
  },
  "live2dModels": {},
  "audio": {},
  "metadata": {
    "source": "akane_character_pack",
    "characterPackId": "mika_pack"
  }
}
```

Rules:

- `handle` is never a URL or path.
- handle segments are ASCII and match petcore safe handle grammar.
- Chinese or unsafe outfit/emotion ids become deterministic hash-based handle segments.
- Resource URL must be root-relative and under `/petdesk-character-packs/<packId>/...`.
- Old `/desktop-pet-character-packs/...` paths are rewritten to `/petdesk-character-packs/...`.
- Entries without safe URLs are skipped.

### Akane Frame -> Pet Display Envelope

Input examples:

```json
{
  "speech": "我在。",
  "speech_segments": [{"text": "我在。"}],
  "emotion": "开心",
  "character": {"outfit": "猫娘"}
}
```

Output:

```json
{
  "schemaVersion": "pet.display.v1",
  "clientMode": "desktop",
  "speech": "我在。",
  "segments": [{"id": "seg_1", "text": "我在。"}],
  "visual": {
    "renderer": "static_portrait",
    "emotion": "开心",
    "outfit": "猫娘",
    "motion": "speaking",
    "assetHandle": "mika_pack/static/outfit-.../emotion-..."
  },
  "actions": [],
  "safety": {"status": "ok", "warnings": []},
  "metadata": {
    "source": "akane",
    "characterPackId": "mika_pack"
  }
}
```

Rules:

- Preserve Akane's display-facing `emotion` and `outfit` strings.
- Normalize unknown emotion/outfit through the charpack manifest fallback.
- Use `motion = "speaking"` when speech exists, otherwise `idle`.
- Only allow petcore motion enum values.
- If the requested visual asset cannot be found, return a safe fallback envelope with warning metadata.
- Do not include `_debug`, prompts, model internals, local paths, or secrets.

## Safety Notes

- `/pet/turn` is still a real LLM turn through Akane; it must not fake success.
- Invalid JSON or empty text returns structured 400.
- Engine failure returns an SSE `error` event and `done`, without exposing local exception details beyond a short generic reason.
- Runtime resource manifest only includes URL paths the browser can request; no absolute filesystem paths.
- The bridge is intentionally static-portrait first. Live2D is a later bridge after layout/model metadata is explicitly mapped.

## Validation Plan

Focused tests:

- safe handle generation rejects path/URL-like output and handles Chinese ids.
- runtime manifest rewrites old character pack URLs to `/petdesk-character-packs`.
- display envelope selects the correct static `assetHandle`.
- `/pet/health` returns ready JSON.
- `/pet/snapshot` returns `pet.display.v1`.
- `/pet/turn` calls a fake engine and returns SSE with `resource_manifest`, `display`, and `done`.

Commands:

```powershell
python -m unittest tests.test_petdesk_bridge -v
python -m py_compile companion_v01/petdesk_bridge.py companion_v01/routes/petdesk.py companion_v01/app.py
git diff --check
```

## Implemented Files

- `companion_v01/petdesk_bridge.py`
- `companion_v01/routes/petdesk.py`
- `companion_v01/app.py`
- `tests/test_petdesk_bridge.py`

## Validation Run

Commands run after implementation:

```powershell
$env:PYTHONPATH='<workspace>\charpack-core'; python -m unittest tests.test_petdesk_bridge -v
$env:PYTHONPATH='<workspace>\capcore;<workspace>\capcore-adapter-mcp;<workspace>\capcore-adapter-python;<workspace>\capcore-adapter-speech;<workspace>\capcore-adapter-comfyui;<workspace>\charpack-core;<workspace>\promptpack-core;<workspace>\capcore-provider-native-tools;<workspace>\capcore-provider-openai;<workspace>\memcore'; python -m unittest tests.test_backend_route_modules -v
python -m ruff check companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py tests\test_petdesk_bridge.py companion_v01\app.py
python -m ruff format --check companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py tests\test_petdesk_bridge.py
python -m py_compile companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
git diff --check
```

Results:

- `tests.test_petdesk_bridge`: 4 tests OK.
- `tests.test_backend_route_modules`: 75 tests OK.
- `ruff check`: OK.
- `ruff format --check` for new files: OK.
- `py_compile`: OK.
- `git diff --check`: OK, with existing CRLF warnings on unrelated dirty files.

## M45 Startup Manifest Note

M45 found that `/pet/snapshot` could return a valid static `assetHandle` before
the runtime had any resource manifest, causing the first paint to use the
runtime placeholder image until the first `/pet/turn` stream delivered a
`resource_manifest` event.

The bridge now exposes `GET /pet/resource-manifest`, advertises it in
`/pet/health.runtimeEnv.VITE_PETDESK_RESOURCE_MANIFEST_URL`, and the starter
whitelists that env key for `petdesk-runtime`. This keeps the runtime from
guessing file paths from handles while making first paint resolve real character
assets.
