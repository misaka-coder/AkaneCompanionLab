---
name: web-media-download
description: Use when exec_run is available and the user wants to find, download, save, or deliver a public web video or audio item from a URL, shortened share link, page title, BV/AV id, or other public media identifier.
---

# Web Media Download

Use `yt-dlp` through `exec_run` to resolve public media, download it into the
managed run workspace, register the output as `gen_*`, and deliver it only when
the user asked to receive the file.

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
4. Inspect the first result's title, uploader and canonical URL/id before
   downloading. If the match is ambiguous, ask one short question.

Check availability before relying on it:

```text
python3 -m yt_dlp --version
```

Use the available Python launcher on the host (`python3` or `python`). If the
module is absent and installing packages is permitted in the current Shell
policy, install `yt-dlp` once with that launcher; otherwise report the missing
dependency honestly.

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
- Treat the delivery tool result as authoritative. A failed card, voice, or file
  delivery is feedback for the next model step; it must not be described as success.
- If the user only asked to identify or inspect a link, do not send a file
  automatically.
- On failure, keep the useful canonical id/URL visible, explain the actual
  failure, and offer the smallest next step. Do not repeatedly retry the same
  blocked URL or mechanically page output.
