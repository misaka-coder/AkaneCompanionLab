---
name: custom-music-card
description: Use when the user wants QQ to show a custom music card wrapping a public playable audio URL, or when a native music card is unavailable.
metadata:
  required_tools:
    - exec_run
---

# Custom Music Card

Use the OneBot `music` segment with `type=custom` only after the requested
audio and cover have been obtained. The card shell can render even when the
audio is not playable, so the transport receipt is not playback proof.

## Contract

```json
{"type":"music","data":{"type":"custom","url":"<click-through page>","audio":"<public direct audio URL>","title":"...","singer":"...","image":"<public image URL>"}}
```

- `url` is the page opened when the card is clicked.
- `audio` must be a public, client-reachable, direct audio resource. A page,
  HTML response, signed video stream, or login/VIP URL is not sufficient.
- `image` must be a non-empty public HTTP(S) image URL.
- The card may be accepted by OneBot while playback still fails because of
  client reachability, expiry, copyright, or access control.

## Workflow

1. Prefer an exact native NetEase/QQ track and `send_music_card` when it exists.
2. For a downloaded source, use the host `yt-dlp` through `exec_run`, keep the
   registered audio artifact, and obtain a genuinely public audio URL before
   constructing the card. Do not re-download an artifact in a new temporary
   directory without checking whether it is still available.
3. Obtain a cover URL and send one custom segment through the bundled OneBot
   action script. Treat `retcode=0` as queue acceptance only; do not claim
   playback until the client has actually played it.

Do not bypass login, DRM, membership, copyright, or regional restrictions. If
the only available source is blocked, report that boundary and offer a native
card, a public alternative, or an ordinary file instead.
