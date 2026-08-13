---
name: music-card-share
description: Use when exec_run is available and the user wants to find a NetEase or QQ Music track, send a NetEase native card, or resolve a public audio URL for QQ voice.
---

# Music Share

Find the exact platform track id for a song, then let the model choose the delivery surface.
`send_music_card` attempts only a native card and returns the real QQ transport result.
`send_audio` sends a verified public audio URL or an existing audio handle as QQ voice.
Neither tool silently changes the requested surface. The script never downloads,
decrypts, or uploads audio.

## When to load

- The user asks to find a song, point to a song, or receive a QQ native music card.
- A `track_id` is needed for `send_music_card` and is not already visible in the
  tool trace or user input.

## When NOT to load

- The user wants local playback control (pause, next, volume) of the desktop pet
  player or music platform.
- The task is about song lyrics, albums, or playlists as documents.
- `exec_run` is not available and no exact official song id is already known.

## Search with the script (preferred, when exec_run is available)

Run the bundled search script from the Skill directory using `exec_run`:

```
python music-card-share/scripts/search_music.py --query "借口 陈海星" --limit 5
python music-card-share/scripts/search_music.py --platform qq --query "借口 周杰伦" --limit 5
```

The script prints one JSON document with `status` in `success` / `empty` / `error`
and, on success, `results` with `track_id`, `title`, `artists` and `album`.
NetEase is the default platform; pass `--platform qq` for QQ Music. Read the
small result directly; no cursor paging is needed.

- `netease_music` track ids are decimal. `qq_music` track ids are alphanumeric
  `songmid` values, not numeric `songid` values.
- Pick the result whose title + artist best matches the user's request. Honor
  "原唱", "Live", "翻唱" and similar qualifiers.
- If several versions are all reasonable, choose by the user's wording; if you
  really cannot decide, ask one short question instead of guessing.
- If the user wants a card, select a NetEase result and call
  `send_music_card(platform=netease_music, track_id=...)`. QQ Music native cards
  are not exposed because the real transport does not support them.
  Its result is the real card transport receipt. On failure, do not claim success
  and do not assume voice was sent.
- If the user asks for playable QQ voice, or explicitly agrees after a card failure,
  resolve a public URL for the exact selected ID:

```
python music-card-share/scripts/search_music.py --platform netease --audio-track-id "1971144922"
python music-card-share/scripts/search_music.py --platform qq --audio-track-id "002XWgfo0IKPOH"
```

  Only when the script returns `status=success` with `audio_url`, call
  `send_audio(source=<audio_url>, name=<song title>)`.
  `send_audio` preflights the URL and returns the real QQ voice transport result.

## Without Shell

If `exec_run` is not available, only call `send_music_card` when a search result
or user input already gave an exact NetEase track id (or `web_search` returned a
page whose official song url embeds a precise id). Otherwise say honestly that the
exact id cannot be located right now; never invent an id from the song title.

## Behavior rules

- Do not download audio merely to use these tools, bypass membership, or attempt to extract encrypted cache
  formats. Whether a card or public URL is available is still decided by QQ,
  the platform's copyright, membership, and region.
- QQ Music public audio is best effort: it only works when the anonymous public
  web endpoint returns a playable URL. VIP, copyright-restricted, or regional
  tracks can legitimately remain unavailable; report that honestly and offer a
  different version or NetEase result.
- Every public audio URL is preflighted before QQ receives it. A song page that
  returns HTML, a removed track, or a copyright-restricted response is unavailable;
  do not tell the user it is still transcoding or waiting for the song duration.
- If the user did not name a platform, try NetEase first; if no suitable result
  exists, try QQ Music and say you switched platforms.
- On `empty` or `error`, report the real outcome and offer to adjust the query;
  do not pretend a card was prepared.
- A card failure and a voice failure are ordinary tool results. Read them, choose
  the next step, and still produce a normal final reply; never turn either into a
  system crash or ask the user to repeat the whole conversation.
