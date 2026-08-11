---
name: music-card-share
description: Use when exec_run is available and the user wants to find a song, request a song, or share a QQ native music card for a NetEase or QQ Music track.
---

# Music Card Share

Find the exact platform track id for a song and deliver a QQ native music card.
The card itself is sent with the `send_music_card` tool; this Skill only handles
finding the correct id. It never downloads, decrypts, or uploads audio.

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
python music-card-share/scripts/search_music.py --platform netease --query "借口 陈海星" --limit 5
python music-card-share/scripts/search_music.py --platform qq --query "借口 周杰伦" --limit 5
```

The script prints one JSON document with `status` in `success` / `empty` / `error`
and, on success, `results` with `track_id` (`songmid` for QQ), `title`, `artists`
and `album`. Read it directly; it is small, so no cursor paging is needed.

- `netease_music` track ids are decimal; `qq_music` track ids are `songmid`
  (alphanumeric, not the numeric `songid`).
- Pick the result whose title + artist best matches the user's request. Honor
  "原唱", "Live", "翻唱" and similar qualifiers.
- If several versions are all reasonable, choose by the user's wording; if you
  really cannot decide, ask one short question instead of guessing.
- After picking an id, call `send_music_card(platform=..., track_id=...)`. Do not
  claim the card was transmitted; the tool result only confirms it entered the
  QQ delivery queue.

## Without Shell

If `exec_run` is not available, only call `send_music_card` when a search result
or user input already gave an exact official track id (or `web_search` returned a
page whose official song url embeds a precise id). Otherwise say honestly that the
exact id cannot be located right now; never invent an id from the song title.

## Behavior rules

- Do not download audio, bypass membership, or attempt to extract encrypted cache
  formats. Only the card is delivered; whether the full song plays is decided by
  QQ, the platform's copyright, membership, and region.
- User did not name a platform: try NetEase first; if nothing suitable, try QQ
  and say you switched platforms.
- On `empty` or `error`, report the real outcome and offer to adjust the query;
  do not pretend a card was prepared.
