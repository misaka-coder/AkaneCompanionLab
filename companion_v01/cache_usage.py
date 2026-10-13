"""Daily conversation cache statistics from host-scoped usage, without prompts."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=8))


def conversation_cache_scope(profile: str, session: str, character: str) -> str:
    return hashlib.sha256("\0".join((profile, session, character)).encode("utf-8")).hexdigest()


def cache_day(value: str, *, today: date | None = None) -> date:
    today = today or datetime.now(LOCAL_TZ).date()
    if value in ("", "今天"):
        return today
    if value == "昨天":
        return today - timedelta(days=1)
    result = date.fromisoformat(value)
    if result > today:
        raise ValueError("future_cache_date")
    return result


def _legacy_requested_models(log_dir: Path, utc_days, keys: set[tuple[str, str]]) -> dict:
    """Use old request evidence only when the recorded route is unambiguous."""
    candidates = {key: set() for key in keys if key[1]}
    if not candidates:
        return {}
    for utc_day in utc_days:
        path = Path(log_dir) / "llm_prompt_audit" / f"{utc_day.isoformat()}.jsonl"
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        raw = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(raw, dict) or raw.get("record_type") != "prompt":
                        continue
                    key = (str(raw.get("instance_id") or ""), str(raw.get("prompt_cache_key") or ""))
                    model = str(raw.get("model") or "").strip()
                    if key in candidates and model:
                        candidates[key].add(model)
        except OSError:
            # This optional audit may be unavailable; retain the upstream name.
            continue
    return {key: next(iter(models)) for key, models in candidates.items() if len(models) == 1}


def read_daily_cache_usage(log_dir: Path, *, day: date, scope_hash: str, instance_id: str = "") -> dict:
    start = datetime.combine(day, time(), LOCAL_TZ)
    end = start + timedelta(days=1)
    records = []
    utc_days = sorted({start.astimezone(timezone.utc).date(), (end - timedelta(microseconds=1)).astimezone(timezone.utc).date()})
    for utc_day in utc_days:
        path = Path(log_dir) / "cache_usage" / f"{utc_day.isoformat()}.jsonl"
        if not path.is_file():
            path = Path(log_dir) / "llm_prompt_audit" / f"{utc_day.isoformat()}.jsonl"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        continue
                    if (raw.get("record_type") != "usage" or raw.get("conversation_scope_hash") != scope_hash
                            or raw.get("client_mode") not in {"qq", "qq_text"}
                            or (instance_id and raw.get("instance_id") != instance_id)):
                        continue
                    stamp = datetime.fromisoformat(raw["ts"].replace("Z", "+00:00"))
                    if stamp.tzinfo is None or not start <= stamp < end:
                        continue
                    tokens = int(raw.get("reported_input_tokens") or 0)
                    cached = int(raw.get("cache_read_tokens") or 0)
                    reported = raw.get("cache_read_reported") is True and tokens > 0 and 0 <= cached <= tokens
                    requested = str(raw.get("requested_model") or "").strip()
                    records.append({"hour": stamp.astimezone(LOCAL_TZ).hour, "ts": stamp.timestamp(),
                                    "model": requested or str(raw.get("model") or "未标注模型"), "reported": reported,
                                    "requested_model_known": bool(requested),
                                    "request_key": (str(raw.get("instance_id") or ""), str(raw.get("prompt_cache_key") or "")),
                                    "input": max(0, tokens), "cached": cached if reported else 0})
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
    legacy_keys = {r["request_key"] for r in records if not r["requested_model_known"]}
    old_requests = _legacy_requested_models(log_dir, utc_days, legacy_keys)
    for row in records:
        if not row["requested_model_known"] and row["request_key"] in old_requests:
            row["model"] = old_requests[row["request_key"]]
    records.sort(key=lambda r: r["ts"])
    def aggregate(rows):
        known = [r for r in rows if r["reported"]]
        tokens = sum(r["input"] for r in known)
        cached = sum(r["cached"] for r in known)
        return {"requests": len(rows), "reported": len(known), "unknown": len(rows) - len(known),
                "input_tokens": tokens, "cached_tokens": cached, "ratio": cached / tokens if tokens else None}
    return {"day": day.isoformat(), "scope": scope_hash, **aggregate(records),
            "hours": [{"hour": hour, **aggregate([r for r in records if r["hour"] == hour])} for hour in range(24)],
            "models": [{"model": model, **aggregate([r for r in records if r["model"] == model])}
                       for model in sorted({r["model"] for r in records})]}
