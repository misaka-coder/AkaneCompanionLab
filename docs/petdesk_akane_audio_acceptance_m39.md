# Petdesk Akane Audio Acceptance M39

Status: automated backend audio chain passed; manual listening result pending.
Date: 2026-07-07

## Goal

Validate the M38 Akane audio bridge against a running Akane backend and the
petdesk runtime starter path.

M38 added:

```text
/pet/turn
  -> display without audio
  -> TTS synthesis
  -> resource_manifest.audio
  -> display.audio.tts.audioHandle
  -> /audio/petdesk/<token>
```

M39 checks whether that chain works outside unit tests.

## Checks Run

Starter dry-run checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_petdesk_runtime.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_akane_petdesk.ps1 -CheckOnly
cmd /c start_akane_petdesk.bat -CheckOnly
```

Result:

```text
all three starter entrypoints resolved the Akane project, sibling runtime, and
backend URL successfully.
```

Running backend health check:

```text
GET /pet/health returned status ready, character packs, default pack, and
whitelisted runtimeEnv.
```

Real `/pet/turn` smoke:

```text
POST /pet/turn returned status 200.
SSE event order:
resource_manifest -> display -> resource_manifest -> display -> done
```

The final display contained:

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

The final manifest contained one audio entry:

```json
{
  "kind": "audio",
  "handle": "akane/tts/<token>",
  "url": "/audio/petdesk/<token>",
  "source": "akane_tts"
}
```

Audio content smoke:

```text
GET /audio/petdesk/<token> returned status 200.
Content-Type: audio/mpeg
Cache-Control: no-store
Body length was non-zero.
```

Runtime starter smoke:

```text
start_akane_petdesk.ps1 -SkipBackend launched petdesk-runtime with whitelisted
runtimeEnv from /pet/health.
The Tauri dev window process started and did not report a runtime panic during
the observation window.
The process was stopped manually after the observation window.
```

## Result

Passed:

- backend `/pet/turn` emits the expected second resource manifest and display;
- audio handle shape is logical, not a URL or filesystem path;
- audio URL is root-relative under `/audio/`;
- audio content can be fetched from the running backend;
- startup glue still passes runtimeEnv into petdesk-runtime.

Pending:

- human listening confirmation that the runtime window actually plays the audio;
- visual/audio timing confirmation, especially whether speech appears before
  playback and whether lip-sync/queue status feels acceptable.

## Decision

Do not promote M39 to full audio acceptance yet.

The backend-to-runtime contract is proven enough to continue, but the product
acceptance line is still:

```text
user sees the reply, hears the voice, and the window stays usable.
```

Record that manual check separately once the user confirms the actual audible
behavior.

## Follow-up Closeout

M40 and M41 closed the pending line on 2026-07-07:

- M40 manual runtime acceptance confirmed that the user can send a message from
  the petdesk window and hear Akane's TTS playback.
- M41 added `scripts/tools/run_petdesk_mvp_smoke.py` and
  `start_akane_petdesk.ps1 -SmokeOnly` so the backend MVP chain can be
  revalidated without opening the runtime window.

The M41 live smoke passed against `http://127.0.0.1:9999` with:

```text
resource_manifest -> display -> resource_manifest -> display -> done
audio_present: true
audio_content_type: audio/mpeg
audio_bytes: non-zero
```
