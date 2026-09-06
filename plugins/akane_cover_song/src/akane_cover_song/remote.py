"""Byte-only client/provider for the dedicated loopback media service.

The HTTP service owns one complete request, not a remotely cancellable Job.
Cancellation therefore drains that request; transport uncertainty fences new
mutations. No server error text or server-selected filesystem path is exposed.
"""

from contextlib import contextmanager
from dataclasses import asdict
import io
import json
import math
import mimetypes
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid
import zipfile

import requests

from .errors import CoverSongError
from .lease import EndpointLease, check_cancelled
from .pipeline import CoverOptions
from .rvc import RvcWebUiProvider


MAX_AUDIO_BYTES = 512 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * MAX_AUDIO_BYTES + 65536


def failed(reason, stage="provider"):
    return CoverSongError(stage=stage, reason=reason, public_message="本地翻唱服务未完成处理，请检查服务状态。")


def timings(headers, key):
    try:
        raw = headers.get(key, "{}")
        if len(raw) > 8192:
            return {}
        value = json.loads(raw)
        if not isinstance(value, dict):
            return {}
        # Only known timing keys: arbitrary header keys may contain host paths.
        allowed = {
            "separation",
            "voice_conversion",
            "mix",
            "total",
            "decode",
            "source_hash",
            "model_fingerprint",
            "model_selection",
            "inference_request",
            "feature_extraction",
            "pitch_extraction",
            "voice_selection",
            "voice_synthesis",
            "model_load",
            "inference",
        }
        return {
            k: round(v, 3)
            for k, v in value.items()
            if (k in allowed or k.removeprefix("rvc_") in allowed)
            and type(v) in (int, float)
            and math.isfinite(v)
            and v >= 0
        }
    except (ValueError, TypeError):
        return {}


def file_card(item):
    if not isinstance(item, dict):
        raise ValueError
    name = item.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 240 or any(c in name for c in "/\\:\r\n\x00"):
        raise ValueError
    values = {"name": name}
    for key in ("size", "mtime_ns"):
        value = item.get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError
        values[key] = value
    return values


class RemoteRvcClient:
    def __init__(self, *, base_url, timeout_seconds=1800, state_dir=None, cancelled=lambda: False, session=None):
        normalized = str(base_url).strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("local media executor endpoint must use credential-free loopback HTTP")
        if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
            raise ValueError("invalid_remote_timeout")
        self.base_url = normalized
        self.timeout_seconds = min(7200, float(timeout_seconds))
        self.cancelled = cancelled
        self.session = session
        self.lease = EndpointLease(normalized, state_dir=state_dir, timeout=self.timeout_seconds, cancelled=cancelled)

    @contextmanager
    def exclusive(self):
        with self.lease.hold():
            yield

    def _request(self, method, path, *, data=None, source_path=None, timeout=None, limit=MAX_RESPONSE_BYTES):
        check_cancelled(self.cancelled)
        deadline = time.monotonic() + (timeout or self.timeout_seconds)
        source = Path(source_path).open("rb") if source_path is not None else None
        session = self.session or requests.Session()
        # No ambient credentials or proxy resolution, including on injected transports.
        session.trust_env = False
        response = None
        try:
            kwargs = {"timeout": max(0.01, deadline - time.monotonic()), "stream": True, "allow_redirects": False}
            if source is not None:
                suffix = Path(source_path).suffix
                kwargs["files"] = {
                    "file": (
                        Path(source_path).name,
                        source,
                        mimetypes.guess_type("audio" + suffix)[0] or "application/octet-stream",
                    )
                }
                kwargs["data"] = data
            else:
                kwargs["params"] = data
            if method == "POST":
                self.lease.begin(uuid.uuid4().hex)
            response = getattr(session, method.lower())(self.base_url + path, **kwargs)
            # Non-success may come from a proxy while its upstream is still running.
            # Conservatively retain the fence; never trust arbitrary error bodies.
            if response.status_code != 200:
                raise ValueError
            payload = bytearray()
            while chunk := response.raw.read1(65536, decode_content=True):
                payload.extend(chunk)
                if len(payload) > limit or time.monotonic() >= deadline:
                    raise ValueError
            if method == "POST":
                self.lease.finish()
            check_cancelled(self.cancelled)
            return bytes(payload), response.headers
        except CoverSongError:
            raise
        except Exception:
            raise failed("rvc_remote_completion_unconfirmed" if method == "POST" else "local_rvc_unreachable") from None
        finally:
            if response is not None:
                response.close()
            if source is not None:
                source.close()
            if self.session is None:
                session.close()

    def list_rvc_models(self, *, force=False, request_timeout_seconds=None):
        raw, _ = self._request(
            "GET",
            "/v1/rvc/models",
            data={"force": "true" if force else "false"},
            timeout=min(request_timeout_seconds or 20, self.timeout_seconds),
            limit=4 * 1024 * 1024,
        )
        try:
            payload = json.loads(raw)
            models = payload["models"]
            if not isinstance(models, list) or len(models) > 2048:
                raise ValueError
            result = []
            for item in models:
                card = file_card(item)
                indices = item.get("indices", [])
                if not isinstance(indices, list):
                    raise ValueError
                card["indices"] = [file_card(index) for index in indices[:12]]
                result.append(card)
            return result
        except (ValueError, KeyError, TypeError):
            raise failed("local_rvc_models_invalid") from None

    def capability_status(self):
        try:
            raw, _ = self._request("GET", "/health", timeout=min(2, self.timeout_seconds), limit=65536)
            payload = json.loads(raw)
            rvc, separation = payload["rvc"], payload["separation"]
            return {
                "rvc": {"ready": rvc.get("ready") is True, "modelCount": int(rvc.get("model_count", 0))},
                "separation": {"ready": separation.get("ready") is True},
            }
        except (CoverSongError, ValueError, KeyError, TypeError, AttributeError):
            return {"rvc": {"ready": False, "modelCount": 0}, "separation": {"ready": False}}

    def separate_rvc_vocals(self, *, source_path, separation_model):
        with self.exclusive():
            raw, _ = self._request(
                "POST", "/v1/rvc/separate", source_path=source_path, data={"separation_model": str(separation_model)}
            )
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                expected = {"vocals.wav", "instrumental.wav"}
                infos = archive.infolist()
                if (
                    len(infos) != 2
                    or {i.filename for i in infos} != expected
                    or any(i.file_size <= 0 or i.file_size > MAX_AUDIO_BYTES for i in infos)
                ):
                    raise ValueError
                return archive.read("vocals.wav"), archive.read("instrumental.wav")
        except (ValueError, OSError, RuntimeError, zipfile.BadZipFile):
            raise failed("local_rvc_separation_response_invalid", "separation") from None

    def convert_rvc_voice(self, *, source_path, model_name, **options):
        params = asdict(CoverOptions(**options))
        data = {k: str(v) for k, v in params.items() if not k.endswith("gain_db")}
        data["model_name"] = model_name
        with self.exclusive():
            audio, headers = self._request(
                "POST", "/v1/rvc/convert", source_path=source_path, data=data, limit=MAX_AUDIO_BYTES
            )
        if not audio:
            raise failed("local_rvc_conversion_output_missing", "voice_conversion")
        return audio, timings(headers, "X-Akane-RVC-Timings")

    def render_cover_song(self, *, source_path, model_name, demucs_model, output_format, **options):
        if output_format not in {"wav", "flac", "mp3"} or demucs_model not in {"htdemucs", "htdemucs_ft"}:
            raise failed("local_cover_options_invalid", "options")
        data = {k: str(v) for k, v in asdict(CoverOptions(**options)).items()}
        data.update(model_name=model_name, demucs_model=demucs_model, output_format=output_format)
        with self.exclusive():
            audio, headers = self._request(
                "POST", "/v1/rvc/cover", source_path=source_path, data=data, limit=MAX_AUDIO_BYTES
            )
        if not audio:
            raise failed("local_cover_output_missing", "local_pipeline")
        return audio, timings(headers, "X-Akane-Cover-Timings")


class RemoteRvcProvider:
    provider_id = "local_rvc_executor"

    def __init__(self, *, client, default_model="", separation_model="HP5_only_main_vocal", demucs_model="htdemucs"):
        self.client, self.default_model = client, default_model
        self.timeout_seconds = client.timeout_seconds
        self.rvc_separation_model = separation_model
        self.separation_model = demucs_model

    def cache_namespace(self):
        from .models import endpoint_namespace

        return endpoint_namespace(self.client.base_url)

    def exclusive(self):
        return self.client.exclusive()

    def capability_status(self):
        status = self.client.capability_status()
        if not status.get("rvc", {}).get("ready") or not status.get("separation", {}).get("ready"):
            return {"enabled": False, "status": "unavailable", "reason": "local_rvc_unavailable"}
        if status["rvc"].get("modelCount", 0) <= 0:
            return {"enabled": False, "status": "missing_model", "reason": "rvc_voice_models_missing"}
        return {"enabled": True, "status": "ready", "reason": ""}

    def list_voice_models(self, *, force=False, request_timeout_seconds=None):
        return [
            item["name"]
            for item in self.client.list_rvc_models(force=force, request_timeout_seconds=request_timeout_seconds)
        ]

    def resolve_voice_model(self, requested, *, default_model=""):
        # One selection policy, shared with the WebUI provider.
        return RvcWebUiProvider.resolve_voice_model(self, requested, default_model=default_model or self.default_model)

    _normalize_model_key = RvcWebUiProvider._normalize_model_key

    def model_fingerprint(self, model_name):
        for item in self.client.list_rvc_models():
            if item["name"].lower() == model_name.lower():
                return {
                    "model": item["name"],
                    "weight": {k: item[k] for k in ("size", "mtime_ns")},
                    "indices": item["indices"],
                }
        return {"model": model_name, "missing": True}

    def separate_vocals(self, *, source_path, work_dir):
        vocals, instrumental = self.client.separate_rvc_vocals(
            source_path=source_path, separation_model=self.rvc_separation_model
        )
        directory = Path(work_dir) / "local_executor_stems"
        directory.mkdir(parents=True, exist_ok=True)
        paths = directory / "vocals.wav", directory / "instrumental.wav"
        for path, data in zip(paths, (vocals, instrumental)):
            with path.open("xb") as stream:
                stream.write(data)
        return paths

    def convert_voice(self, *, source_path, output_path, model_name, **options):
        audio, seconds = self.client.convert_rvc_voice(source_path=source_path, model_name=model_name, **options)
        return self._output(output_path, audio, seconds)

    def render_full_cover(self, *, source_path, output_path, model_name, output_format, **options):
        audio, seconds = self.client.render_cover_song(
            source_path=source_path,
            model_name=model_name,
            demucs_model=self.separation_model,
            output_format=output_format,
            **options,
        )
        return self._output(output_path, audio, seconds)

    @staticmethod
    def _output(path, audio, seconds):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(audio)
        return {"timings": seconds}
