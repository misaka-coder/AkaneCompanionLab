# Petdesk Akane Audio Bridge M38

Status: implemented in this slice.
Date: 2026-07-06

## Goal

Connect Akane's existing TTS path to `petdesk-runtime` audio playback without
making `/pet/*` a permanent second desktop-pet protocol.

The target chain is:

```text
Akane /pet/turn
  -> engine.process_turn(...)
  -> pet.display.v1 speech + visual
  -> Akane TTS synthesis
  -> runtime resource manifest audio handle
  -> petdesk-runtime audio queue
```

## Sources Checked

- `companion_v01/routes/voice.py`
- `companion_v01/routes/petdesk.py`
- `companion_v01/petdesk_bridge.py`
- `companion_v01/routes/desktop_pet.py`
- `companion_v01/desktop_pet_engine.py`
- `companion_v01/engine.py`
- `tests/test_backend_route_modules.py`
- `tests/test_petdesk_bridge.py`
- `petdesk-runtime/src/audio/audio-queue.ts`
- `petdesk-runtime/src/renderer/resource-resolver.ts`
- `petdesk-runtime/docs/audio_queue_m8.md`
- `petcore-protocol/src/index.ts`

## Current Shape

Akane already has `POST /tts`:

- resolves character or request voice preference;
- uses GPT-SoVITS when configured;
- falls back to Edge TTS when possible;
- returns raw audio bytes with safe provider/status headers;
- avoids leaking local paths or secrets in fallback fields.

`/pet/turn` currently emits:

```text
resource_manifest
display
done
```

The display envelope includes speech, speech segments, visual state, and static
asset handle. It does not include an `audio` section yet.

`petdesk-runtime` already supports audio:

- manifest bucket: `audio`;
- top-level display field: `audio.tts.enabled` and `audio.tts.audioHandle`;
- segment field: `segment.voice.audioHandle`;
- resolver kind: `audio`;
- default URL policy allows `/audio/...`, but not `/pet/audio/...`.

## Decision

M38 keeps synthesis and storage host-owned in Akane.

The bridge will:

1. keep the existing first `resource_manifest` event for static resources;
2. emit the display text/visual as soon as the Akane turn frame is ready;
3. synthesize TTS from the final envelope speech;
4. register the audio bytes in a short-lived in-memory registry;
5. emit a second `resource_manifest` with an `audio` entry;
6. emit the same display envelope again with:

```json
{
  "audio": {
    "tts": {
      "enabled": true,
      "audioHandle": "akane/tts/<token>"
    }
  }
}
```

The corresponding manifest entry is:

```json
{
  "audio": {
    "akane/tts/<token>": {
      "kind": "audio",
      "handle": "akane/tts/<token>",
      "url": "/audio/petdesk/<token>",
      "source": "akane_tts"
    }
  }
}
```

`/audio/petdesk/<token>` is used instead of `/pet/audio/<token>` because the
runtime resolver already accepts `/audio/` root-relative URLs.

## Boundary

In scope:

- Akane host bridge for top-level turn TTS audio;
- in-memory short-lived audio registry;
- safe logical audio handles;
- safe root-relative audio URLs;
- graceful no-audio degradation when TTS fails or is unavailable.

Out of scope:

- segment-by-segment TTS;
- streaming TTS;
- BGM or environmental audio;
- persistent generated-file records for every TTS turn;
- runtime or protocol schema changes;
- changing old `/desktop-pet/*`, `/tts`, or `desktop_pet_next` behavior.

## Why Not Reuse Workspace Generated Audio

Old desktop-pet generated audio routes resolve records from the generated file
service. They are workspace artifacts and are appropriate for user-visible files
or tool outputs.

Per-turn TTS is playback cache, not a durable workspace artifact. Persisting
every reply voice there would pollute the workspace and make retention unclear.
The M38 registry is intentionally short-lived and process-local.

## Failure Rules

- TTS failure must not remove the visible speech or visual update.
- TTS failure emits no audio handle and no unsafe error detail.
- Audio tokens are unguessable hex strings and are not local file paths.
- Registry responses are `Cache-Control: no-store`.
- Registry cleanup runs opportunistically on register/read.
- No local absolute paths, API keys, database paths, or cached model paths enter
  manifest, display, docs, or logs.

## Validation Plan

Focused tests:

- `/pet/turn` still emits static manifest, display, and done without TTS;
- with a fake TTS client, `/pet/turn` emits an audio manifest and a display
  envelope containing `audio.tts.audioHandle`;
- `/audio/petdesk/<token>` returns the registered audio bytes and media type;
- if TTS raises, `/pet/turn` still emits display and done without audio;
- manifest URLs are under `/audio/` and contain no filesystem paths.

Commands:

```powershell
$env:PYTHONPATH='F:\Akane\capcore;F:\Akane\capcore-adapter-mcp;F:\Akane\capcore-adapter-python;F:\Akane\capcore-adapter-speech;F:\Akane\capcore-adapter-comfyui;F:\Akane\charpack-core;F:\Akane\promptpack-core;F:\Akane\capcore-provider-native-tools;F:\Akane\capcore-provider-openai;F:\Akane\memcore'; python -m unittest tests.test_petdesk_bridge -v
python -m ruff check companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
python -m ruff format --check companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
python -m py_compile companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
git diff --check -- docs\petdesk_akane_audio_bridge_m38.md companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
```

## Implemented Files

- `companion_v01/petdesk_bridge.py`
- `companion_v01/routes/petdesk.py`
- `companion_v01/app.py`
- `tests/test_petdesk_bridge.py`

## Validation Run

Commands run after implementation:

```powershell
$env:PYTHONPATH='F:\Akane\capcore;F:\Akane\capcore-adapter-mcp;F:\Akane\capcore-adapter-python;F:\Akane\capcore-adapter-speech;F:\Akane\capcore-adapter-comfyui;F:\Akane\charpack-core;F:\Akane\promptpack-core;F:\Akane\capcore-provider-native-tools;F:\Akane\capcore-provider-openai;F:\Akane\memcore'; python -m unittest tests.test_petdesk_bridge -v
$env:PYTHONPATH='F:\Akane\capcore;F:\Akane\capcore-adapter-mcp;F:\Akane\capcore-adapter-python;F:\Akane\capcore-adapter-speech;F:\Akane\capcore-adapter-comfyui;F:\Akane\charpack-core;F:\Akane\promptpack-core;F:\Akane\capcore-provider-native-tools;F:\Akane\capcore-provider-openai;F:\Akane\memcore'; python -m unittest tests.test_backend_route_modules -v
python -m ruff check companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
python -m ruff format --check companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
python -m py_compile companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
git diff --check -- docs\petdesk_akane_audio_bridge_m38.md companion_v01\petdesk_bridge.py companion_v01\routes\petdesk.py companion_v01\app.py tests\test_petdesk_bridge.py
```

Results:

```text
tests.test_petdesk_bridge: 7 tests OK
tests.test_backend_route_modules: 75 tests OK
ruff check: OK
ruff format --check: OK
py_compile: OK
git diff --check: OK
```
