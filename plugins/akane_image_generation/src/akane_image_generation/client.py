from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import threading
import time
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError

from .types import ImageError, ImageResult, inspect_image


class ImageClient:
    def __init__(
        self,
        *,
        base_url,
        api_key,
        model="gpt-image-2",
        timeout_seconds=300,
        max_output_bytes=25 * 1024 * 1024,
        max_input_bytes=8 * 1024 * 1024,
        max_total_input_bytes=20 * 1024 * 1024,
        max_input_images=5,
        transient_retry_count=2,
        allow_loopback_http=False,
    ):
        parsed = urlsplit(str(base_url).strip())
        loopback = allow_loopback_http and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        if (
            (parsed.scheme != "https" and not loopback)
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ImageError("invalid_provider_base_url")
        self._base = str(base_url).strip().rstrip("/")
        self._key = str(api_key).strip()
        self._model = str(model).strip()
        if not self._key or not self._model:
            raise ImageError("provider_not_configured")
        self._timeout = max(0.1, min(600.0, float(timeout_seconds)))
        self._output_limit = max(1024, min(25 * 1024 * 1024, int(max_output_bytes)))
        self._input_limit = max(1024, min(8 * 1024 * 1024, int(max_input_bytes)))
        self._input_total = max(1024, min(20 * 1024 * 1024, int(max_total_input_bytes)))
        self._input_count = max(1, min(5, int(max_input_images)))
        self._retries = max(0, min(3, int(transient_retry_count)))
        self._active = {}
        self._closed = False

    async def generate(
        self,
        *,
        prompt,
        n=1,
        size="auto",
        quality="auto",
        background="auto",
        output_format="png",
        compression=90,
        references=(),
        mask=None,
        input_fidelity="auto",
    ):
        if self._closed:
            raise ImageError("image_client_closed")
        fields = self._fields(prompt, n, size, quality, background, output_format, compression, input_fidelity)
        if not isinstance(references, (list, tuple)) or len(references) > self._input_count:
            raise ImageError("reference_image_limit")
        refs = tuple(inspect_image(item.data, reference=True, max_bytes=self._input_limit) for item in references)
        if len(refs) > self._input_count or sum(len(item.data) for item in refs) > self._input_total:
            raise ImageError("reference_image_limit")
        if mask is not None:
            mask = inspect_image(mask.data, reference=True, mask=True, max_bytes=self._input_limit)
            if not refs:
                raise ImageError("reference_image_required_for_mask")
            if (mask.width, mask.height) != (refs[0].width, refs[0].height):
                raise ImageError("mask_dimensions_mismatch")
        cancelled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(self._generate, fields, refs, mask, cancelled))
        self._active[task] = cancelled
        interrupted = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    interrupted = True
                    cancelled.set()
            result = task.result()
            if interrupted or cancelled.is_set():
                raise asyncio.CancelledError()
            return result
        finally:
            self._active.pop(task, None)

    async def aclose(self):
        self._closed = True
        for signal in tuple(self._active.values()):
            signal.set()
        interrupted = False
        for task in tuple(self._active):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    interrupted = True
                except Exception:
                    break  # Invocation owns its structured failure.
        if interrupted:
            raise asyncio.CancelledError()

    @staticmethod
    def _check_cancel(signal):
        if signal.is_set():
            raise asyncio.CancelledError()

    def _generate(self, fields, refs, mask, signal):
        self._check_cancel(signal)
        deadline = time.monotonic() + self._timeout
        notices, uploaded = [], []
        with requests.Session() as session:
            try:
                if not refs:
                    response = self._post(session, "/images/generations", signal, deadline, json=fields)
                else:
                    key = "image" if len(refs) == 1 else "image[]"
                    files = [(key, self._file(item, index)) for index, item in enumerate(refs)]
                    if mask is not None:
                        files.append(("mask", self._file(mask, len(refs))))
                    try:
                        response = self._post(
                            session,
                            "/images/edits",
                            signal,
                            deadline,
                            data={key: "true" if value is True else str(value) for key, value in fields.items()},
                            files=files,
                        )
                    except ImageError as exc:
                        if len(refs) < 2 or exc.code != "provider_rejected_request":
                            raise
                        for index, item in enumerate((*refs, *((mask,) if mask else ()))):
                            upload = self._post(
                                session,
                                "/files",
                                signal,
                                deadline,
                                data={"purpose": "user_data"},
                                files=[("file", self._file(item, index))],
                            )
                            with upload:
                                payload = self._json(self._body(upload, 64 * 1024, deadline))
                            file_id = payload.get("id") if isinstance(payload, dict) else None
                            if not isinstance(file_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", file_id):
                                raise ImageError("provider_file_upload_invalid_id")
                            uploaded.append(file_id)
                        payload = {**fields, "images": [{"file_id": item} for item in uploaded[: len(refs)]]}
                        if mask is not None:
                            payload["mask"] = {"file_id": uploaded[-1]}
                        response = self._post(session, "/images/edits", signal, deadline, json=payload)
                with response:
                    images = self._decode(response, fields["n"], deadline)
                if len(images) < fields["n"]:
                    notices.append("provider_returned_fewer_images")
            except (requests.RequestException, HTTPError, TimeoutError):
                # A disconnected client cannot assert remote inference stopped.
                raise ImageError("remote_completion_unconfirmed") from None
            except ImageError as exc:
                if signal.is_set() and exc.code in {"provider_stream_incomplete", "provider_response_deadline"}:
                    raise ImageError("remote_completion_unconfirmed") from None
                raise
            finally:
                # These IDs were created by this invocation, never user assets.
                for file_id in uploaded:
                    try:
                        with session.delete(
                            f"{self._base}/files/{file_id}",
                            headers=self._headers(),
                            timeout=(5, 10),
                            allow_redirects=False,
                            stream=True,
                        ) as response:
                            if not 200 <= response.status_code < 300:
                                notices.append("provider_uploaded_file_cleanup_failed")
                    except requests.RequestException:
                        notices.append("provider_uploaded_file_cleanup_failed")
        return ImageResult(tuple(images), fields["n"], tuple(dict.fromkeys(notices)))

    def _headers(self):
        return {"Authorization": f"Bearer {self._key}"}

    @staticmethod
    def _file(item, index):
        return f"reference_{index}.{item.output_format}", item.data, item.media_type

    def _post(self, session, endpoint, signal, deadline, **kwargs):
        for attempt in range(self._retries + 1):
            self._check_cancel(signal)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ImageError("provider_response_deadline")
            response = session.post(
                f"{self._base}{endpoint}",
                headers=self._headers(),
                timeout=(min(10, remaining), remaining),
                stream=True,
                allow_redirects=False,
                **kwargs,
            )
            if 200 <= response.status_code < 300:
                return response
            with response:
                status = response.status_code
                error = self._body(response, 64 * 1024, deadline).decode("utf-8", errors="replace").lower()
            code = (
                "provider_images_api_unsupported"
                if "images api is not supported" in error
                else "provider_files_api_unsupported"
                if "files api is not supported" in error
                else "provider_auth_or_network_forbidden"
                if status in {401, 403}
                else "provider_rate_limited"
                if status == 429
                else "provider_redirect_rejected"
                if 300 <= status < 400
                else "provider_no_compatible_accounts"
                if status == 503 and "no available compatible accounts" in error
                else "provider_unavailable"
                if status >= 500
                else "provider_rejected_request"
            )
            if code == "provider_no_compatible_accounts" and attempt < self._retries:
                signal.wait(min(attempt + 1, max(0, deadline - time.monotonic())))
                continue
            raise ImageError(code, retryable=status == 429 or status >= 500)
        raise ImageError("provider_unavailable")

    @staticmethod
    def _chunks(response, limit, deadline):
        consumed = 0
        # read1 returns available bytes without waiting to fill an 8 KiB buffer;
        # final SSE events must not wait for a relay's keep-alive to expire.
        while chunk := response.raw.read1(8192, decode_content=True):
            if time.monotonic() > deadline:
                raise ImageError("provider_response_deadline")
            consumed += len(chunk)
            if consumed > limit:
                raise ImageError("provider_response_size_limit")
            yield chunk

    def _body(self, response, limit, deadline):
        return b"".join(self._chunks(response, limit, deadline))

    @staticmethod
    def _json(raw):
        try:
            return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise ImageError("provider_invalid_json") from None

    def _decode(self, response, n, deadline):
        limit = (self._output_limit * 4 // 3 + 8192) * n
        if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
            payload = self._json(self._body(response, limit, deadline))
            encoded = self._final_images(payload, n, stream=False)
        else:
            encoded = {}
            pending = bytearray()
            lines = 0
            done = False
            # Partial images may be large; bound the whole stream, not just finals.
            for chunk in self._chunks(response, limit * 4, deadline):
                pending.extend(chunk)
                while b"\n" in pending:
                    raw, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    lines += 1
                    if lines > 4096 or len(raw) > limit:
                        raise ImageError("provider_stream_size_limit")
                    line = raw.strip()
                    if not line or line.startswith((b":", b"event:", b"id:")):
                        continue
                    if not line.startswith(b"data:"):
                        raise ImageError("provider_invalid_stream")
                    line = line[5:].strip()
                    if line == b"[DONE]":
                        done = True
                        break
                    payload = self._json(line)
                    for index, value in self._final_images(payload, n, stream=True).items():
                        encoded[index] = value
                    if len(encoded) >= n:
                        done = True
                        break
                if len(pending) > limit:
                    raise ImageError("provider_stream_size_limit")
                if done:
                    break
            if not encoded:
                raise ImageError("provider_stream_incomplete")
        if not encoded:
            raise ImageError("provider_returned_no_image")
        images = []
        for _, value in sorted(encoded.items())[:n]:
            if len(value) > self._output_limit * 4 // 3 + 8:
                raise ImageError("provider_output_size_limit")
            try:
                data = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError):
                raise ImageError("provider_returned_invalid_base64") from None
            images.append(inspect_image(data, max_bytes=self._output_limit))
        return images

    @staticmethod
    def _final_images(payload, n, *, stream):
        if not isinstance(payload, dict):
            raise ImageError("provider_invalid_json")
        if payload.get("error") or str(payload.get("type", "")).endswith(".failed"):
            raise ImageError("provider_generation_failed")
        event = str(payload.get("type", "")).lower()
        if stream and event != "completed" and not event.endswith(".completed"):
            return {}
        items = payload.get("data", [payload])
        if not isinstance(items, list) or len(items) > 32:
            raise ImageError("provider_invalid_image_list")
        output = {}
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            value = item.get("b64_json")
            if not isinstance(value, str) or not value:
                continue
            index = item.get("output_index", item.get("index", position))
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < n:
                raise ImageError("provider_invalid_image_index")
            output[index] = value
        return output

    def _fields(self, prompt, n, size, quality, background, output_format, compression, input_fidelity):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise ImageError("invalid_image_prompt")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 4:
            raise ImageError("invalid_image_count")
        size = str(size).lower().replace("×", "x").strip()
        if size != "auto":
            match = re.fullmatch(r"(\d{3,4})x(\d{3,4})", size)
            if not match or not all(512 <= int(part) <= 2048 for part in match.groups()):
                raise ImageError("invalid_image_size")
        if (
            quality not in {"auto", "low", "medium", "high"}
            or background not in {"auto", "opaque"}
            or output_format not in {"png", "jpeg", "webp"}
            or input_fidelity not in {"auto", "low", "high"}
        ):
            raise ImageError("invalid_image_options")
        if isinstance(compression, bool) or not isinstance(compression, int) or not 0 <= compression <= 100:
            raise ImageError("invalid_image_compression")
        fields = dict(
            model=self._model,
            prompt=prompt.strip(),
            n=n,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            response_format="b64_json",
            stream=True,
        )
        if output_format in {"jpeg", "webp"}:
            fields["output_compression"] = compression
        if input_fidelity != "auto":
            fields["input_fidelity"] = input_fidelity
        return fields
