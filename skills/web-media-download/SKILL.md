---
name: web-media-download
description: Use when exec_run is available and the user wants to find, download, save, or deliver a public web video or audio item from a URL, shortened share link, page title, BV/AV id, or other public media identifier.
metadata:
  required_tools:
    - exec_run
---

# Web Media Download

Use the host's `yt-dlp` through `exec_run` to resolve public media, download it
into the managed run workspace, register the output as `gen_*`, and deliver it
only when the user asked to receive the file. When `exec_run` is available, it is
the primary media path because it exposes the host's real network route and
toolchain; do not silently switch to a different downloader after a Shell
result has already established the actual failure.

## Boundaries

- Use this Skill for fetching public web media. Use `video-understanding` to
  inspect what an existing video attachment shows, and `media-inspect-convert`
  to transform an existing file.
- Do not bypass login, membership, paywalls, DRM, region restrictions, or site
  access controls. If public access is insufficient, report the real boundary.
- A title, URL, id, or search result identifies media; it is not proof that the
  bytes were downloaded or delivered.

## Resolve the target

1. Preserve the exact URL or identifier visible in the current message.
2. Check whether a shortened/share URL contains an actual path token. A bare
   domain such as `b23.tv` is incomplete, not a network failure.
3. If the URL is incomplete but the share text contains a distinctive title,
   search by that title instead of stopping immediately. Prefer the site's
   `yt-dlp` search extractor when one exists; for Bilibili the valid prefix is
   `bilisearch:`, not `bilisearchvideo:`. A general public-video fallback is
   `ytsearch1:`.
4. Inspect several candidates before downloading when the source provides a
   search list. Compare title, uploader/channel, duration, publish date,
   view/engagement count when available, and the canonical URL/id. Prefer an
   official or high-confidence uploader and an exact artist/title match;
   popularity is supporting evidence, not the sole selector. Do not choose a
   candidate from its title alone. If the match is still ambiguous, ask one
   short question.

Check availability before relying on it. This is a host-level probe, not a
project dependency installation:

```text
python3 -m yt_dlp --version
```

Use the available Python launcher on the host (`python3` or `python`). Reuse
the host installation and its shared package cache. Do not install `yt-dlp`
inside every project or every temporary run; only install once when the host
probe proves it is genuinely absent and the current policy permits it.

## Download and register

Run the downloader directly so its real exit code reaches `exec_run`. Do not
pipe the main command into `head`, `tail`, or another command: a failed
downloader followed by a successful reader can make the whole Shell command
look successful. If a log excerpt is necessary, capture the downloader's exit
code first and return that same code after reading the log.

Use a deterministic output template in the managed run workspace, for example:

```text
mkdir -p outputs && python3 -m yt_dlp --no-progress -f "bv*+ba/b" -o "outputs/media.%(ext)s" "CANONICAL_URL"
```

Call `exec_run` with `output_globs=["outputs/media.*"]` and no `cwd`. Treat the
operation as successful only when all of these are true:

- command status is `completed` and `exit_code` is 0;
- `artifact_status` is `registered`;
- `generated_resources` contains one or more real `gen_*` handles.

Site warnings about unavailable premium formats are acceptable only if a lower
public format was actually downloaded and registered. An error printed to
stdout with exit code 0 is not success; inspect the output and verify the
registered artifact.

## Deliver

- For video, image, archive, document, or any other ordinary file, call `send_file`
  once with the exact `gen_*` handle returned by `generated_resources`.
- For downloaded audio in QQ, call `send_audio` with that same handle to send a
  playable voice bubble. Use `send_file` separately for ordinary file transfer;
  when both are requested the two delivery calls may be made together.
- A page URL or a `yt-dlp -g` video/CDN URL is not automatically a playable
  audio URL. For a custom QQ card, the `audio` field must be a public,
  client-reachable direct audio resource; keep the page URL in the card's
  click-through field. Signed video URLs may be video streams, require headers,
  or expire, so do not use them as card audio merely because they were resolved.
- Treat the delivery tool result as authoritative. A failed card, voice, or file
  delivery is feedback for the next model step; it must not be described as success.
  A successful delivery result proves queueing, not final client receipt.
- If the user only asked to identify or inspect a link, do not send a file
  automatically.
- On failure, keep the useful canonical id/URL visible, explain the actual
  failure, and offer the smallest next step. Do not repeatedly retry the same
  blocked URL or mechanically page output.
- If a host-level Shell download succeeds, reuse its registered artifact or
  exact output in later steps. Do not re-download the same media in a fresh
  `mktemp` directory unless the prior artifact is missing or invalid.
