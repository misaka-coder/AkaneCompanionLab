# Petdesk Akane E2E Acceptance M33

Status: Level 1 and Level 2 passed; Level 3 manual window acceptance not run yet.
Date: 2026-07-06

## Goal

Verify that the new M32 `/pet/*` bridge is not just a unit-level adapter. It must be consumable by
`petdesk-runtime` as a real backend:

```text
Akane FastAPI backend
  -> /pet/health
  -> /pet/snapshot
  -> /pet/turn SSE stream
  -> resource_manifest event
  -> display event
  -> petdesk-runtime backend parser/resource resolver
  -> static character image URL fetch
```

This milestone is an acceptance check for the transitional bridge. It does not make `/pet/*` canonical yet.

## Why This Matters

M32 added intentional redundancy:

- old Akane desktop-pet routes remain available;
- new `/pet/*` routes serve `petdesk-runtime`.

M33 decides whether that redundancy is proving useful in practice. A green M33 means the bridge can support
continued runtime migration work. A red M33 means fix the bridge/runtime before adding more desktop-pet features.

## Sources Checked

- `launch_akane_memory_v01.py`
- `README.md`
- `docs/petdesk_akane_bridge_m32.md`
- `petdesk-runtime/README.md`
- `petdesk-runtime/src/bridge/backend-client.ts`
- `petdesk-runtime/src/app/runtime-config.ts`
- `petdesk-runtime/examples/runtime-bridge-e2e-smoke.ts`
- `petdesk-runtime/examples/runtime-bridge-process.ts`
- `petdesk-runtime/docs/runtime_bridge_e2e_m16.md`

## Environment Assumptions

Akane source checkout layout:

```text
F:\Akane\
  AkaneCompanionLab\
  capcore\
  capcore-adapter-mcp\
  capcore-adapter-python\
  capcore-adapter-speech\
  capcore-adapter-comfyui\
  charpack-core\
  promptpack-core\
  capcore-provider-native-tools\
  capcore-provider-openai\
  memcore\
  petcore-protocol\
  petdesk-runtime\
```

Akane backend default:

```text
http://127.0.0.1:9999
```

`petdesk-runtime` backend env:

```powershell
$env:VITE_PETDESK_BACKEND_URL='http://127.0.0.1:9999'
```

## Acceptance Levels

### Level 1: Backend Protocol Smoke

Non-window automated check:

- `GET /pet/health` returns `ok: true`;
- `GET /pet/snapshot` returns a `pet.display.v1` envelope;
- `POST /pet/turn` returns parseable SSE;
- the stream includes at least `resource_manifest` and `done`;
- if the LLM turn succeeds, the stream includes a valid `display` event;
- if the LLM turn fails because local model/API config is unavailable, the stream must include a structured `error`
  event and still finish with `done`;
- `resource_manifest.staticImages` contains safe handles and `/petdesk-character-packs/...` URLs;
- the first resolved static image URL can be fetched from the Akane backend.

### Level 2: Runtime Parser Smoke

Use `petdesk-runtime` code to parse the Akane backend:

- `PetBackendClient.health()`;
- `PetBackendClient.snapshot()`;
- `PetBackendClient.turnStream()`;
- `createRuntimeResourceBridgeFromManifestUpdate(..., { resourceBaseUrl })`;
- `staticImage.assetResolver(...)`.

This verifies the contract from the runtime side, not just with ad hoc HTTP.

### Level 3: Manual Window Acceptance

Start Akane backend, then run:

```powershell
cd F:\Akane\petdesk-runtime
$env:VITE_PETDESK_BACKEND_URL='http://127.0.0.1:9999'
pnpm tauri:dev
```

Expected signs:

- debug panel reaches `backend:ready`;
- snapshot speech appears instead of only the offline demo;
- static portrait resolves from Akane under `/petdesk-character-packs/...`;
- pressing `S` triggers `/pet/turn`;
- after turn completion, the bubble and visual update or a structured backend error is visible.

## Not Accepted As Success

- Only unit tests passing.
- The runtime showing its offline demo while backend is unreachable.
- `/pet/turn` silently failing without `error` and `done`.
- static image URLs that only work from Akane but are rejected by `petdesk-runtime` resolver.
- introducing a second permanent feature surface without updating the M32 redundancy guard.

## Commands

Start Akane backend:

```powershell
cd F:\Akane\AkaneCompanionLab
$env:PYTHONPATH='F:\Akane\capcore;F:\Akane\capcore-adapter-mcp;F:\Akane\capcore-adapter-python;F:\Akane\capcore-adapter-speech;F:\Akane\capcore-adapter-comfyui;F:\Akane\charpack-core;F:\Akane\promptpack-core;F:\Akane\capcore-provider-native-tools;F:\Akane\capcore-provider-openai;F:\Akane\memcore'
$env:COMPANION_HOST='127.0.0.1'
$env:COMPANION_PORT='9999'
python launch_akane_memory_v01.py
```

Runtime manual window:

```powershell
cd F:\Akane\petdesk-runtime
$env:VITE_PETDESK_BACKEND_URL='http://127.0.0.1:9999'
pnpm tauri:dev
```

## Result Log

### 2026-07-06

M32 was first committed as:

```text
dafb109 Add petdesk runtime bridge
```

Existing `http://127.0.0.1:9999` was an already-running older Akane process without the new `/pet` router. To avoid
interrupting that service, this acceptance run started a second backend from current source on:

```text
http://127.0.0.1:10033
```

Backend startup notes:

- `.venv\Scripts\python.exe launch_akane_memory_v01.py`
- `COMPANION_HOST=127.0.0.1`
- `COMPANION_PORT=10033`
- sibling package `PYTHONPATH` set for current source checkout
- backend loaded local `F:/models/bge-m3` before uvicorn became ready

Level 1 backend protocol smoke passed:

- `GET /pet/health`: HTTP 200, `ok: true`, `status: ready`
- default pack: `akane_sample`
- static image count: 38
- `GET /pet/snapshot`: HTTP 200, `schemaVersion: pet.display.v1`
- snapshot visual: `static_portrait`, `assetHandle: akane_sample/static/default/normal`
- `GET /petdesk-character-packs/akane_v1/character.json`: HTTP 200
- `POST /pet/turn`: HTTP 200, `text/event-stream`
- stream events: `resource_manifest`, `display`, `done`
- display speech from real Akane engine: `嗯？我在哦。`
- display trace id present: `akane_v01_77d03f99bb7c`

Level 2 runtime parser/resource smoke passed with `petdesk-runtime` source code:

```text
PetBackendClient.health()
PetBackendClient.snapshot()
PetBackendClient.turnStream()
createRuntimeResourceBridgeFromManifestUpdate(..., { resourceBaseUrl })
staticImage.assetResolver(...)
fetch(resolved static image)
```

Observed runtime-side result:

```json
{
  "ok": true,
  "health": "ready",
  "snapshot": "pet.display.v1",
  "events": ["resource_manifest", "display", "done"],
  "speech": "收到，smoke通过。",
  "asset": "http://127.0.0.1:10033/petdesk-character-packs/akane_sample/assets/characters/default/normal.png",
  "manifestStatus": "update"
}
```

Level 3 manual Tauri window acceptance was not run in this slice. It should be the next visible check before claiming
the new runtime is better than the old Akane desktop pet in user experience.
