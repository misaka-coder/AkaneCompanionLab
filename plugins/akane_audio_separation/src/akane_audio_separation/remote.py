"""Streaming client for the existing synchronous loopback media protocol.

Cancellation cannot stop a legacy server's inference. Keep draining the actual
response before acknowledging cancellation; transport loss is an unconfirmed
completion error, never a successful cancellation. No retry or new server queue.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
import uuid
import zipfile

from .process import drain


MAX_BYTES = 1024 * 1024 * 1024
CHUNK = 256 * 1024


class RemoteSeparationError(RuntimeError):
    def __init__(self, reason, *, terminal=False):
        super().__init__(reason)
        self.terminal = terminal


def valid_model_name(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and not any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in value)
    )


class RemoteSeparation:
    def __init__(self, url, *, timeout=1800, uvr_model="HP5_only_main_vocal"):
        parsed = urlsplit(str(url))
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RemoteSeparationError("separation_endpoint_invalid")
        try:
            self.port = parsed.port or 80
        except ValueError:
            raise RemoteSeparationError("separation_endpoint_invalid") from None
        self.host, self.prefix = parsed.hostname, parsed.path.rstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9_./-]*", self.prefix) or ".." in self.prefix.split("/"):
            raise RemoteSeparationError("separation_endpoint_invalid")
        if not valid_model_name(uvr_model):
            raise RemoteSeparationError("separation_uvr_model_invalid")
        self.uvr_model, self.timeout = uvr_model, timeout
        self.active = set()
        self.closed = False

    async def _run(self, function, *args):
        if self.closed:
            raise RemoteSeparationError("separation_remote_closed")
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.active.add(task)
        try:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await drain(task)
                except RemoteSeparationError as exc:
                    if not exc.terminal:
                        raise RemoteSeparationError("remote_completion_unconfirmed") from None
                # A successful completed response, including a local archive
                # validation failure, proves inference returned. Discard it.
                raise asyncio.CancelledError
        finally:
            self.active.discard(task)

    def _health(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request("GET", self.prefix + "/health")
            response = connection.getresponse()
            body = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(body) > 1024 * 1024:
                raise ValueError
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
            separation, rvc = result.get("separation", {}), result.get("rvc", {})
            if isinstance(separation, dict) and separation.get("ready") is True:
                model = separation.get("model") or "htdemucs"
                if model not in ("htdemucs", "htdemucs_ft"):
                    raise ValueError
                return {"backend": "remote_demucs", "model": model}
            if isinstance(rvc, dict) and rvc.get("ready") is True:
                return {"backend": "remote_uvr", "model": self.uvr_model}
            raise RemoteSeparationError("remote_separation_unavailable", terminal=True)
        except (OSError, http.client.HTTPException, ValueError):
            raise RemoteSeparationError("remote_separation_unavailable") from None
        finally:
            connection.close()

    async def probe(self):
        return await self._run(self._health)

    async def separate(self, *, source, output_root, backend, model, output_format):
        return await self._run(self._separate, source, output_root, backend, model, output_format)

    def _separate(self, source, output_root, backend, model, output_format):
        if backend not in ("remote_demucs", "remote_uvr") or output_format not in ("wav", "flac", "mp3"):
            raise RemoteSeparationError("remote_separation_options_invalid", terminal=True)
        if backend == "remote_demucs":
            if model not in ("htdemucs", "htdemucs_ft"):
                raise RemoteSeparationError("remote_separation_options_invalid", terminal=True)
            endpoint, fields, transfer_format = (
                "/v1/audio/separate",
                {"model": model, "output_format": output_format},
                output_format,
            )
        else:
            if not valid_model_name(model):
                raise RemoteSeparationError("remote_separation_options_invalid", terminal=True)
            endpoint, fields, transfer_format = "/v1/rvc/separate", {"separation_model": model}, "wav"
        boundary = "akane-" + uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            for name, value in fields.items()
        ]
        extension = source.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ".bin"
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="source{extension}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        head, tail = "".join(parts).encode(), f"\r\n--{boundary}--\r\n".encode()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        archive_path = output_root / "response.zip"
        terminal = False
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            connection.putrequest("POST", self.prefix + endpoint)
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(len(head) + source.stat().st_size + len(tail)))
            connection.endheaders()
            connection.send(head)
            with source.open("rb") as stream:
                while chunk := stream.read(CHUNK):
                    connection.send(chunk)
            connection.send(tail)
            response = connection.getresponse()
            if response.status != 200:
                # An error may itself be a timeout talking to nested RVC. It
                # does not prove that nested inference stopped or completed.
                raise RemoteSeparationError("remote_separation_failed")
            # A successful archive is returned after the synchronous service
            # has obtained the completed inference outputs.
            terminal = True
            size = 0
            with archive_path.open("wb") as output:
                while chunk := response.read(CHUNK):
                    size += len(chunk)
                    if size > MAX_BYTES + 1024 * 1024:
                        raise RemoteSeparationError("separation_output_too_large", terminal=True)
                    output.write(chunk)
            return unpack_stems(archive_path, output_root=output_root, output_format=transfer_format)
        except (OSError, http.client.HTTPException):
            reason = "remote_separation_response_failed" if terminal else "remote_completion_unconfirmed"
            raise RemoteSeparationError(reason, terminal=terminal) from None
        finally:
            connection.close()
            try:
                archive_path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("separation_archive_cleanup_failed")

    async def aclose(self):
        self.closed = True
        for task in tuple(self.active):
            try:
                await drain(task)
            except RemoteSeparationError:
                # The owning invocation receives the failure. Closing only
                # waits for the already-started transport; never retries work.
                pass


def unpack_stems(archive_path, *, output_root, output_format):
    paths = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            expected = {f"vocals.{output_format}", f"instrumental.{output_format}"}
            if len(entries) != 2 or {item.filename for item in entries} != expected:
                raise ValueError
            if sum(item.file_size for item in entries) > MAX_BYTES:
                raise RemoteSeparationError("separation_output_too_large", terminal=True)
            for entry in entries:
                mode = entry.external_attr >> 16
                if entry.file_size <= 0 or entry.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise ValueError
                path = output_root / entry.filename
                count = 0
                with archive.open(entry) as source, path.open("wb") as target:
                    while chunk := source.read(CHUNK):
                        count += len(chunk)
                        if count > entry.file_size:
                            raise ValueError
                        target.write(chunk)
                if count != entry.file_size:
                    raise ValueError
                paths[Path(entry.filename).stem] = path
        return paths
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, RemoteSeparationError):
            raise
        raise RemoteSeparationError("separation_archive_invalid", terminal=True) from None
