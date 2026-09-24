# Petdesk Akane Manual Acceptance M37

Status: accepted for starter usability.
Date: 2026-07-06

## Goal

Close out the M36 Akane-owned petdesk runtime starter with a small manual
acceptance record.

M36 added:

```text
start_akane_petdesk.bat
  -> scripts/start_petdesk_runtime.ps1
  -> Akane backend /pet/health
  -> whitelisted runtimeEnv
  -> explicitly configured petdesk-runtime pnpm tauri:dev
```

This document records what was actually observed after that starter landed.

## Manual Result

User acceptance feedback:

```text
bat file can start normally.
No obvious issue observed.
Window dragging is normal enough for now.
```

This means the M35/M36 chain is now usable as a real local starter path:

```text
Akane starter
  -> /pet/health.runtimeEnv
  -> VITE_PETDESK_INTERACTION_PROFILE(_JSON)
  -> petdesk-runtime window
```

The acceptance result is about startup and basic window handfeel. It should not
be overread as a full product replacement for `desktop_pet_next`.

## Boundary

Accepted:

- `start_akane_petdesk.bat` launches the petdesk runtime path;
- the runtime window appears through the Akane starter;
- drag behavior is acceptable in this manual check;
- no immediate startup blocker was reported.

Not explicitly revalidated in this M37 closeout:

- pressing `S` and confirming `/pet/turn` stream behavior;
- close/reopen process cleanup;
- audio/TTS playback;
- Live2D placement;
- release packaging.

## Decision

Keep `start_akane_petdesk.*` as a source-development/manual acceptance starter.

Do not replace the public one-click `desktop_pet_next` launch path yet. The new
runtime has crossed the "can boot with Akane-owned profile" line, but it still
needs audio/TTS, turn interaction polish, and close/reopen checks before being
promoted as the main desktop pet.

## Next Recommended Slice

M38 should inspect Akane's current TTS/audio output path and petdesk-runtime's
audio queue/display contract, then decide the smallest safe bridge for voice
playback.

The target is:

```text
Akane /pet/turn
  -> speech + optional audio handle/URL
  -> petdesk-runtime audio queue
  -> visible speech remains synced with playback as much as possible
```

Keep this as a follow-up design pass before changing protocol payloads.
