# System Music Awareness V1

This document defines the first lightweight slices for Akane to perceive the
music currently playing on the user's desktop, reuse that context in prompts,
optionally request current-session playback controls, and keep the existing
local upload / ASR music timeline as a fallback instead of the default path.

## Status

Design target: Phase 1 implemented in desktop pet next; Phase 2/3 implemented
as an optional online synced-lyrics provider plus prompt integration; Phase 5
has a lightweight current-session playback control slice for Windows SMTC.

The perception and lyrics implementation should be Windows-only, read-mostly,
and non-blocking. It must degrade cleanly on unsupported platforms or when no
system media session is available. Playback control is limited to the current
system media session and returns structured failure when the player rejects a
command.

Implementation note: V1 uses Windows SMTC only to learn the current track,
artist, playback state, and timeline position. It does not read platform lyric
panels or control the player. Online lyrics are resolved separately from the
normalized title/artist metadata and may be unavailable or low confidence.

## Why This Exists

Akane already has a desktop music prompt projection path:

- `companion_v01/desktop_music_timeline.py` can project nearby music timeline
  segments into prompt context.
- `desktop_pet_next/src/main.js` can parse local `.lrc` files and pass
  `lyric_current`, `lyric_previous`, and `lyric_next` in desktop activity.
- `companion_v01/desktop_context_engine.py` already treats direct lyric fields
  as authoritative and does not need to start the heavy ASR timeline path when
  those fields exist.

The missing high-ROI piece is a lightweight source for "what the user is
already listening to" without requiring drag/drop, file import, Demucs, or
Whisper. Windows exposes this through
`GlobalSystemMediaTransportControlsSessionManager` for apps that integrate with
System Media Transport Controls. Mainstream music players and browsers usually
publish this session; unsupported or private players may not.

## Product Goal

Akane should feel like she is sitting beside the user and naturally noticing the
music that is already playing.

Examples:

- The user plays music in QQ Music, Spotify, NetEase Cloud Music, or Chrome.
- Akane detects title, artist, playback status, and approximate progress.
- If lyrics can be found, Akane knows the nearby line without the user uploading
  the file.
- If lyrics cannot be found, Akane can still say what song appears to be
  playing, but must not invent lyrics.

## Non-Goals for V1

V1 perception and lyrics do not:

- search and play requested songs;
- operate a browser or external music app;
- require MCP;
- run Demucs / Whisper for system-media songs;
- inject full lyrics into prompt;
- expose local absolute paths, API keys, prompt text, screenshots, clipboard
  content, or full lyric bodies in snapshots.

Full "Akane, play this song" orchestration belongs to later capability phases.
Current playback control is only for the already-active system media session:
play/resume, pause, stop, previous, and next.

## Layered Music Sources

Music context should be selected by cheapest reliable source first:

| Layer | Source | Default Behavior |
|-------|--------|------------------|
| 0 | System media session | Read current title/artist/status/progress from OS |
| 1 | Online synced lyrics | Search by title/artist, cache LRC-derived segments |
| 2 | Local `.lrc` | Parse adjacent or explicitly selected lyric file |
| 3 | Existing backend timeline | Reuse existing generated transcript/timeline when present |
| 4 | ASR fallback | Run Demucs/Whisper only after explicit user intent |

For system media, layer 0 plus layer 1 should be enough for the common case.
For local uploads, the desired order is:

1. Read metadata from the uploaded audio if available and search online lyrics.
2. Parse adjacent or explicitly supplied `.lrc`.
3. Reuse existing backend timeline.
4. Ask the user before running the expensive ASR fallback.

The existing ASR path remains valuable, but it should no longer be the default
way to make ordinary online music understandable.

## High-Level Flow

```text
Desktop music player updates OS media session
  -> Tauri/Rust reads current media snapshot
  -> frontend keeps a fresh systemMedia state
  -> track change triggers lyric lookup / cache hydration
  -> lyric segments are matched against current position
  -> desktop_activity includes safe current/previous/next lyric fields
  -> backend prompt context receives nearby music context
```

If lyric lookup is unavailable:

```text
systemMedia state still reports title / artist / progress
  -> desktop_activity marks source_kind = "system_media"
  -> backend prompt says lyrics are unavailable
  -> Akane must not fabricate lyrics
```

## Tauri Provider

Provider id:

```text
provider.music.system_media
```

Capability type:

```text
music_perception_provider
```

Implementation source:

```text
tauri_bridge
```

Adapter metadata:

```text
winrt_smtc
```

Execution mode:

```text
internal
```

Reasoning:

- `type` describes what the provider is.
- `source` describes where it runs.
- `adapter` names the platform implementation.
- Library names such as `windows`, `smtc-suite`, or `nowhear` should not be
  used as capability type/source values.

## Tauri Command Contract

Command:

```text
get_current_system_media
```

Return shape:

```json
{
  "ok": true,
  "status": "ready",
  "reason": "",
  "capturedAt": 1760000000000,
  "platform": "windows",
  "trackKey": "qqmusic::晴天::周杰伦::叶惠美",
  "title": "晴天",
  "artist": "周杰伦",
  "album": "叶惠美",
  "sourceApp": "QQMusic.exe",
  "playbackStatus": "playing",
  "isPlaying": true,
  "positionSeconds": 135.2,
  "durationSeconds": 269.0
}
```

Required statuses:

| Status | Meaning |
|--------|---------|
| `ready` | A usable media session and at least title or artist are available |
| `empty` | Media session exists but does not expose useful title/artist |
| `unavailable` | Unsupported platform, no session, or read failure |

Required failure reasons:

| Reason | Meaning |
|--------|---------|
| `unsupported_platform` | Non-Windows V1 runtime |
| `no_active_session` | No current SMTC session |
| `read_failed` | Windows API call failed |
| `join_failed` | Tauri blocking task failed |

The command must never throw for normal absence of media. It should return
structured `unavailable` data.

## Polling vs Events

V1 may use frontend polling every 2 seconds:

- call `get_current_system_media`;
- normalize text fields;
- ignore Akane's own player source to avoid self-feedback;
- update settings snapshot only when track/status changes;
- treat data older than about 10 seconds as stale.

This is acceptable because the Windows API reads local session state. It should
not start a process, touch the network, or read media bytes.

Later versions may replace polling with a Tauri event watcher if the
implementation is simpler and stable.

## Frontend Activity Shape

When fresh system media exists and no local Akane music track is playing,
`buildDesktopMusicActivity()` may return:

```json
{
  "type": "audio_playback",
  "title": "晴天 - 周杰伦",
  "source_id": "system_media:<trackKey>",
  "handle": "system_media_current",
  "status": "running",
  "progress_seconds": 135.2,
  "duration_seconds": 269.0,
  "source_kind": "system_media",
  "source_app": "QQMusic.exe",
  "artist": "周杰伦",
  "album": "叶惠美",
  "system_media": true,
  "playback_status": "playing",
  "lyric_file_name": "",
  "lyric_line_count": 0,
  "lyric_index": -1,
  "lyric_current": "",
  "lyric_previous": "",
  "lyric_next": ""
}
```

Once lyric segments are available, the frontend should fill:

```json
{
  "lyric_file_name": "online:lrclib",
  "lyric_line_count": 42,
  "lyric_index": 18,
  "lyric_current": "当前附近的一句",
  "lyric_previous": "上一句",
  "lyric_next": "下一句"
}
```

The frontend should not send full lyric text with every chat turn.

## Backend Lyrics Provider

Suggested provider id:

```text
provider.music.lyrics.online
```

Capability type:

```text
music_lyrics_provider
```

Implementation source:

```text
builtin
```

Initial implementation can use `syncedlyrics` if dependency review is clean.
The provider should be optional. Missing dependency or disabled network search
must produce structured `unavailable` / `disabled` responses, not crashes.

Suggested route:

```text
POST /capabilities/music/lyrics
```

Request:

```json
{
  "trackKey": "qqmusic::晴天::周杰伦::叶惠美",
  "title": "晴天",
  "artist": "周杰伦",
  "album": "叶惠美",
  "source": "system_media",
  "positionSeconds": 135.2
}
```

Response:

```json
{
  "ok": true,
  "status": "ready",
  "trackKey": "qqmusic::晴天::周杰伦::叶惠美",
  "source": "lrclib",
  "confidence": "medium",
  "segments": [
    { "start": 130.2, "end": 134.8, "text": "..." },
    { "start": 134.8, "end": 139.4, "text": "..." }
  ],
  "lineCount": 42,
  "cached": true
}
```

Failure examples:

```json
{ "ok": false, "status": "disabled", "reason": "network_lyrics_disabled" }
```

```json
{ "ok": false, "status": "not-found", "reason": "lyrics_not_found" }
```

```json
{ "ok": false, "status": "low-confidence", "reason": "ambiguous_match" }
```

The route should return only normalized segments needed by the client. It should
not expose provider API keys, raw request logs, or local cache paths.

## LRC Segment Model

Use one shared normalized shape across online lyrics, local `.lrc`, and ASR:

```json
{
  "start": 135.2,
  "end": 139.4,
  "text": "normalized lyric line"
}
```

Rules:

- Strip LRC metadata tags such as `ar`, `ti`, `al`, `by`, and `offset`.
- Support multiple timestamps on one line by duplicating the lyric text.
- Sort by `start`.
- Infer `end` from the next segment start.
- Drop empty lines.
- Cap per-line text length before storage and prompt use.
- Cap total returned segments to avoid huge payloads.

## Cache Model

Cache should be profile-scoped:

```text
users_data/<profile_user_id>/music/lyrics_cache/
```

Cache key:

```text
hash(normalized title + artist + album + provider source)
```

Store:

```json
{
  "trackKey": "...",
  "title": "晴天",
  "artist": "周杰伦",
  "album": "叶惠美",
  "provider": "lrclib",
  "confidence": "medium",
  "segments": [],
  "lineCount": 42,
  "createdAt": 1760000000,
  "updatedAt": 1760000000
}
```

Do not store:

- full local audio paths;
- player window titles beyond normalized metadata;
- API keys;
- prompt text;
- chat text.

## Prompt Projection Rules

Prompt injection should be conservative:

- Include title, artist, playback status, and approximate progress.
- Include at most a few nearby lyric lines.
- Prefer `lyric_current`, `lyric_previous`, and `lyric_next` from the frontend
  when present.
- If no lyrics are available, explicitly instruct Akane not to invent lyrics.
- If confidence is low, do not inject lyric text.
- Do not say Akane "read a file" for system media; frame it as noticing the
  music playing nearby.

Existing `DesktopMusicTimelineService.build_prompt_projection()` remains useful
for local timeline sources. V1 should avoid scheduling ASR for
`source_kind = "system_media"` unless a future explicit user action requests it.

## Control Center Display

The settings/control-center music page should show system media as a compact
status row, not a parameter wall.

Recommended collapsed display:

```text
System music: Ready
晴天 - 周杰伦 · QQ Music · 02:15 / 04:29
Lyrics: Found / Not found / Disabled
```

Avoid:

- raw `trackKey`;
- raw provider ids;
- raw source app package ids unless useful;
- full lyric text;
- many debug fields in the normal dashboard.

Expanded diagnostics may show structured status/reason for debugging.

## Capability Catalog Projection

`GET /capabilities` may expose these read-only entries:

```json
{
  "id": "provider.music.system_media",
  "name": "系统媒体感知",
  "type": "music_perception_provider",
  "source": "tauri_bridge",
  "executionMode": "internal",
  "status": "ready",
  "risk": "low",
  "summary": "读取系统正在播放的歌曲和进度",
  "currentTrack": {
    "title": "晴天",
    "artist": "周杰伦",
    "positionSeconds": 135
  }
}
```

```json
{
  "id": "provider.music.lyrics.online",
  "name": "在线歌词检索",
  "type": "music_lyrics_provider",
  "source": "builtin",
  "executionMode": "internal",
  "status": "disabled",
  "risk": "medium",
  "summary": "根据歌名和歌手搜索同步歌词"
}
```

```json
{
  "id": "provider.music.system_media_control",
  "name": "系统媒体控制",
  "type": "music_playback_provider",
  "source": "tauri_bridge",
  "executionMode": "internal",
  "status": "ready",
  "risk": "medium",
  "summary": "请求当前系统播放器播放、暂停、停止、上一首或下一首"
}
```

Risk note:

- System media read is local and low risk.
- System media control only targets the current OS media session and is medium
  risk because it changes playback state.
- Online lyric lookup sends title/artist/album to external providers, so it is
  medium risk and should be configurable.

## Relationship to MCP and Future Song Requests

System Music Awareness V1 does not depend on MCP.

Future "Akane, play this song" should be modeled as ability orchestration:

```text
music intent: play "晴天"
  -> local music library provider?
  -> configured music API provider?
  -> browser control provider / MCP?
  -> fallback: open search page or ask user to configure a provider
```

Suggested future capability types:

| Type | Purpose |
|------|---------|
| `music_perception_provider` | Read what is currently playing |
| `music_lyrics_provider` | Resolve timed lyrics |
| `music_playback_provider` | Play/pause/next/previous/seek current session |
| `music_search_provider` | Search or resolve songs by title/artist |
| `browser_control_provider` | Operate a configured web player |

V1 should only implement the first two read paths. It should not add fake
buttons for search/playback.

## Privacy and Safety

Required boundaries:

- User can disable system media awareness.
- User can disable online lyric lookup separately.
- Online lyric lookup should disclose that title/artist may be sent to external
  providers.
- No screenshot, clipboard, prompt, chat, or full local file content is involved.
- Backend snapshot must not expose full lyrics or raw cache paths.
- Logs should keep only status/reason and safe metadata.
- Failure to read media or lyrics must not block chat, settings navigation, or
  window controls.

## Implementation Phases

### Phase 1: Read-Only System Media

Files likely touched:

- `desktop_pet_next/src-tauri/Cargo.toml`
- `desktop_pet_next/src-tauri/src/main.rs`
- `desktop_pet_next/src/main.js`
- `desktop_pet_next/src/control-center/data-adapter.js`
- `docs/control-center-lab-contract.md`
- tests/smoke scripts if needed

Deliverables:

- `get_current_system_media` Tauri command.
- Windows implementation using the existing `windows` crate.
- Non-Windows structured degradation.
- Frontend polling every 2 seconds in Tauri runtime.
- `music.systemMedia` included in settings snapshot.
- No lyrics lookup yet.

Acceptance:

- When QQ Music/Spotify/Chrome publishes SMTC, settings snapshot shows title,
  artist, playback state, and progress.
- When no session exists, status is `unavailable` with a reason.
- Non-Windows returns `unsupported_platform`.
- Chat prompt can include song title/progress without inventing lyrics.
- No ASR timeline is scheduled for `source_kind = "system_media"`.

### Phase 2: Online Lyrics Cache

Files likely touched:

- `companion_v01/routes/capabilities.py` or a small dedicated music route module
- `companion_v01/desktop_music_timeline.py`
- `companion_v01/local_capability_catalog.py`
- tests under `tests/`

Deliverables:

- Optional online lyrics provider.
- LRC parser into shared segment model.
- Profile-scoped lyrics cache.
- Structured disabled/not-found/low-confidence failures.

Acceptance:

- Same track does not repeatedly hit network providers.
- Low-confidence lyrics are not injected.
- Missing dependency or disabled network lookup degrades cleanly.
- Tests cover LRC parsing, cache hit, cache miss, and no-lyrics prompt behavior.

### Phase 3: Prompt Integration

Files likely touched:

- `desktop_pet_next/src/main.js`
- `companion_v01/desktop_context_engine.py`
- `tests/test_desktop_activity_runtime_contract.py`

Deliverables:

- Frontend maps cached segments to current/previous/next lyric fields.
- Backend prompt uses only nearby lyrics.
- System media path remains read-only.

Acceptance:

- User asks "这首是什么" and Akane can answer from system media metadata.
- User asks about the current lyric and Akane answers only when lyrics exist.
- If lyrics are unavailable, Akane says she knows the song/progress but not the
  exact words.

### Phase 4: Local Upload Online-First

Deliverables:

- Read local audio metadata when available.
- Try online lyrics before ASR.
- Keep adjacent `.lrc` support.
- Ask user before expensive ASR fallback.

Acceptance:

- Uploading a tagged local song can hydrate lyrics without ASR.
- Adjacent `.lrc` remains supported.
- ASR is never triggered silently.

### Phase 5: Future Playback and Song Request Abilities

Read-only perception remains complete without playback control. Phase 5 adds two
small safe interaction slices:

- `control_system_media`: a Tauri command that asks Windows SMTC to control the
  current media session (`play`, `pause`, `stop`, `previous`, `next`) and returns
  structured success/failure.
- `open_music_search`: a tool that opens a public music-platform search page for
  a requested song in desktop pet mode, but must not claim that the song has
  started playing.

Potential deliverables:

- Current-session playback controls through Windows SMTC.
- Music search-page opener (`open_music_search`) for `qq_music`,
  `netease_music`, `bilibili`, and `youtube`.
- Playback control provider for current session.
- Music search provider abstraction.
- Browser/MCP provider for web-player search and play.
- Explicit user permission and confirmation UX.

Current non-goals:

- auto-clicking the first result by default;
- logging in, downloading, bypassing membership/copyright limits, or controlling
  a third-party player beyond the current OS media session without a real
  provider boundary;
- returning fake `{ ok: true }` playback results.

## Tests and Verification

Minimum checks for implementation slices:

```bash
git diff --check
python -m unittest tests.test_desktop_activity_runtime_contract
python -m unittest tests.test_backend_route_modules
cd desktop_pet_next && npm run smoke:control-center-actions
cd desktop_pet_next && npm run probe:control-center-runtime
cd desktop_pet_next && npm run build
```

Rust-specific checks should be run from `desktop_pet_next/src-tauri` or through
the existing Tauri build pipeline after Windows API features are added.

## Open Questions

1. Should online lyrics lookup be opt-in on first use or enabled by a settings
   toggle with a clear privacy label?
2. Should system media awareness be gated by the existing desktop context toggle
   or have its own music-awareness toggle?
3. Should lyrics cache expire automatically, and if so after how long?
4. Which online lyrics provider order should be used first in China-focused
   environments?
5. Should control-center show system media on the Overview page, the Music page,
   or both?

## Design Decision

Proceed with a Windows-only, read-only System Music Awareness V1 before MCP or
song-request automation.

This gives Akane an immediate companionship upgrade while keeping risk low:

- local OS read first;
- optional network lyrics second;
- no playback control;
- no fake action;
- existing heavy ASR remains as explicit fallback.
