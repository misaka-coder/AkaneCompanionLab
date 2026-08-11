#!/usr/bin/env python3
"""music-card-share: search NetEase for a track and return a stable
track_id that Akane's ``send_music_card`` can deliver as a QQ native card.

Python standard library only. Outputs a single JSON document on stdout:
  success -> {"status":"success","platform":...,"query":...,"results":[...]}
  empty   -> {"status":"empty",...}
  error   -> {"status":"error","reason":...,...}
No audio is downloaded, no cookies are read, and no host paths are printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_RESULTS = 5
HTTP_TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 1024 * 1024

USER_AGENT = "Mozilla/5.0 (compatible; AkaneMusicCardShare/1.0)"

def _http_get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://music.163.com/"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large")
    return json.loads(raw.decode("utf-8"))


def _search_netease(query: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({"s": query, "type": 1, "limit": limit, "offset": 0})
    url = f"https://music.163.com/api/search/get/web?{params}"
    payload = _http_get_json(url)
    songs = (payload.get("result") or {}).get("songs") or []
    results = []
    for song in songs[:limit]:
        if not isinstance(song, dict):
            continue
        track_id = str(song.get("id") or "").strip()
        if not track_id:
            continue
        artists = [
            str(item.get("name") or "").strip()
            for item in (song.get("artists") or song.get("ar") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        album = song.get("album") or {}
        results.append(
            {
                "track_id": track_id,
                "title": str(song.get("name") or "").strip(),
                "artists": artists,
                "album": str((album.get("name") if isinstance(album, dict) else "") or "").strip(),
            }
        )
    return results


def _search(query: str, limit: int) -> dict:
    base = {
        "status": "error",
        "platform": "netease_music",
        "query": query,
    }
    clean_query = str(query or "").strip()
    if not clean_query:
        return {**base, "reason": "empty_query"}
    try:
        results = _search_netease(clean_query, limit)
    except urllib.error.HTTPError as exc:
        return {**base, "reason": f"http_error:{exc.code}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {**base, "reason": "network_error"}
    except (ValueError, json.JSONDecodeError) as exc:
        return {**base, "reason": f"parse_error:{type(exc).__name__}"}
    if not results:
        return {**base, "status": "empty", "results": []}
    return {**base, "status": "success", "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search NetEase for a track.")
    parser.add_argument("--query", required=True, help="search text, e.g. '借口 陈海星'")
    parser.add_argument("--limit", type=int, default=5, help="max results (1-5)")
    args = parser.parse_args(argv)
    limit = max(1, min(MAX_RESULTS, int(args.limit)))
    result = _search(args.query, limit)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
