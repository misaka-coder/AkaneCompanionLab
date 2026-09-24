#!/usr/bin/env python3
"""Search music IDs and, on explicit request, resolve a public audio URL.

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

PLATFORMS = {"netease": "netease_music", "qq": "qq_music"}
NETEASE_AUDIO_URL = "https://music.163.com/song/media/outer/url?id={track_id}.mp3"
QQ_MUSIC_VKEY_ENDPOINT = "https://u.y.qq.com/cgi-bin/musicu.fcg"

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


def _search_qq(query: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({"w": query, "format": "json", "p": 1, "n": limit, "new_json": 1})
    url = f"https://c.y.qq.com/soso/fcgi-bin/client_search_cp?{params}"
    payload = _http_get_json(url)
    song_list = (((payload.get("data") or {}).get("song") or {}).get("list")) or []
    results = []
    for song in song_list[:limit]:
        if not isinstance(song, dict):
            continue
        songmid = str(song.get("songmid") or song.get("mid") or "").strip()
        if not songmid:
            continue
        artists = [
            str(item.get("name") or "").strip()
            for item in (song.get("singer") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        album = str(song.get("albumname") or "").strip()
        if not album and isinstance(song.get("album"), dict):
            album = str(song["album"].get("name") or "").strip()
        results.append(
            {
                "track_id": songmid,
                "title": str(song.get("songname") or song.get("title") or "").strip(),
                "artists": artists,
                "album": album,
            }
        )
    return results


_SEARCHERS = {"netease": _search_netease, "qq": _search_qq}


def _resolve_audio_url(platform_key: str, track_id: str) -> dict:
    clean_id = str(track_id or "").strip()
    base = {"status": "error", "platform": PLATFORMS[platform_key], "track_id": clean_id}
    if platform_key == "netease":
        if not clean_id.isdigit() or len(clean_id) > 20:
            return {**base, "reason": "invalid_track_id"}
        return {**base, "status": "success", "audio_url": NETEASE_AUDIO_URL.format(track_id=clean_id)}
    if not clean_id or len(clean_id) > 64 or not any(character.isalpha() for character in clean_id):
        return {**base, "reason": "invalid_songmid"}
    payload = {
        "req_1": {
            "module": "vkey.GetVkeyServer",
            "method": "CgiGetVkey",
            "param": {
                "guid": "10000",
                "songmid": [clean_id],
                "songtype": [0],
                "uin": "0",
                "loginflag": 1,
                "platform": "20",
            },
        },
        "comm": {"uin": 0, "format": "json", "ct": 24, "cv": 0},
    }
    request = urllib.request.Request(
        QQ_MUSIC_VKEY_ENDPOINT,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, "Referer": "https://y.qq.com/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return {**base, "reason": "response_too_large"}
        document = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {**base, "reason": f"http_error:{exc.code}"}
    except (urllib.error.URLError, TimeoutError):
        return {**base, "reason": "network_error"}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {**base, "reason": "invalid_provider_response"}
    data = ((document.get("req_1") or {}).get("data") or {}) if isinstance(document, dict) else {}
    entries = data.get("midurlinfo") if isinstance(data, dict) else None
    first = entries[0] if isinstance(entries, list) and entries and isinstance(entries[0], dict) else {}
    purl = str(first.get("purl") or "").strip()
    sip_values = data.get("sip") if isinstance(data, dict) else None
    sip = str(sip_values[0] or "").strip() if isinstance(sip_values, list) and sip_values else ""
    if not purl or not sip:
        return {**base, "status": "unavailable", "reason": "public_audio_unavailable"}
    return {**base, "status": "success", "audio_url": urllib.parse.urljoin(sip, purl)}


def _search(platform_key: str, query: str, limit: int) -> dict:
    base = {
        "status": "error",
        "platform": PLATFORMS.get(platform_key, platform_key),
        "query": query,
    }
    clean_query = str(query or "").strip()
    if not clean_query:
        return {**base, "reason": "empty_query"}
    try:
        results = _SEARCHERS[platform_key](clean_query, limit)
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
    parser = argparse.ArgumentParser(description="Search NetEase or QQ Music for a track.")
    parser.add_argument("--platform", choices=sorted(PLATFORMS), default="netease", help="netease or qq")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--query", help="search text, e.g. '借口 陈海星'")
    operation.add_argument("--audio-track-id", help="resolve one exact track id to a public audio URL")
    parser.add_argument("--limit", type=int, default=5, help="max results (1-5)")
    args = parser.parse_args(argv)
    limit = max(1, min(MAX_RESULTS, int(args.limit)))
    result = (
        _resolve_audio_url(args.platform, args.audio_track_id)
        if args.audio_track_id is not None
        else _search(args.platform, args.query, limit)
    )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
