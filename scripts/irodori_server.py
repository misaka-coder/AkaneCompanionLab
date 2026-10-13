"""Serve the official Irodori API with one observable, disconnect-aware lane.

Run with the Irodori Python environment and a private JSON settings file. Model
paths and speaker files belong in that file, not in the companion repository.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path


class SerialSpeechApp:
    def __init__(self, app, *, runtime_status, clear_cache, idle_seconds=10.0, queue_limit=16):
        self.app, self.runtime_status, self.clear_cache = app, runtime_status, clear_cache
        self.idle_seconds, self.queue_limit = idle_seconds, queue_limit
        self.lock = asyncio.Lock()
        self.queued = 0
        self.active = 0
        self.completed = 0
        self.discarded = 0
        self.last_activity = time.monotonic()
        self.cache_dirty = False
        self.last_seconds = None
        self.last_error = ""
        self.last_http_status = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/akane/status" and scope["method"] == "GET":
            value = {**self.runtime_status(), "active": self.active, "queued": self.queued,
                     "max_concurrent_synthesis": 1, "completed": self.completed,
                     "discarded": self.discarded, "last_seconds": self.last_seconds, "last_error": self.last_error,
                     "last_http_status": self.last_http_status}
            return await self._json(send, 200, value)
        if scope["path"] != "/v1/audio/speech" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        if self.queued >= self.queue_limit:
            return await self._json(send, 429, {"error": {"message": "tts_irodori_queue_full"}})
        body, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 256 * 1024:
                return await self._json(send, 413, {"error": {"message": "tts_request_too_large"}})
            body.append(message)
            if not message.get("more_body", False):
                break
        disconnected = asyncio.Event()
        response_finished = False

        async def watch_disconnect():
            while True:
                if (await receive())["type"] == "http.disconnect":
                    if not response_finished:
                        disconnected.set()
                    return

        monitor = asyncio.create_task(watch_disconnect())
        acquire = asyncio.create_task(self.lock.acquire())
        lost = asyncio.create_task(disconnected.wait())
        self.queued += 1
        acquired = False
        try:
            await asyncio.wait((acquire, lost), return_when=asyncio.FIRST_COMPLETED)
            if disconnected.is_set():
                self.discarded += 1
                return
            await acquire
            acquired = True
            self.queued -= 1
            self.active = 1
            self.last_activity = time.monotonic()
            replay = iter(body)

            async def replay_receive():
                message = next(replay, None)
                if message is not None:
                    return message
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def guarded_send(message):
                nonlocal response_finished
                if message["type"] == "http.response.start":
                    self.last_http_status = message["status"]
                if not disconnected.is_set():
                    if message["type"] == "http.response.body" and not message.get("more_body", False):
                        response_finished = True
                    await send(message)

            # A disconnect during inference only discards delivery. The lane
            # remains owned until the real GPU operation and WAV encoding end.
            work = asyncio.create_task(self.app(scope, replay_receive, guarded_send))
            while True:
                try:
                    await asyncio.shield(work)
                    break
                except asyncio.CancelledError:
                    disconnected.set()
                    if work.cancelled():
                        raise
            self.completed += 1
            if disconnected.is_set():
                self.discarded += 1
            self.last_error = (f"http_{self.last_http_status}" if self.last_http_status and self.last_http_status >= 400 else "")
        except Exception as exc:
            self.last_error = type(exc).__name__
            raise
        finally:
            monitor.cancel()
            lost.cancel()
            if not acquired:
                # Acquisition and disconnect may settle in the same loop tick.
                if acquire.done() and not acquire.cancelled() and acquire.exception() is None:
                    self.lock.release()
                else:
                    acquire.cancel()
                self.queued -= 1
            else:
                self.last_seconds = round(time.monotonic() - self.last_activity, 3)
                self.last_activity = time.monotonic()
                self.cache_dirty = True
                self.active = 0
                self.lock.release()
            await asyncio.gather(monitor, lost, acquire, return_exceptions=True)

    async def idle_cleanup(self):
        while True:
            await asyncio.sleep(1)
            if self.cache_dirty and not self.active and not self.queued and time.monotonic() - self.last_activity >= self.idle_seconds:
                async with self.lock:
                    if not self.queued:
                        await asyncio.to_thread(self.clear_cache)
                        self.cache_dirty = False

    @staticmethod
    async def _json(send, status, value):
        data = json.dumps(value).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
        await send({"type": "http.response.body", "body": data})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    for key, value in config["environment"].items():
        if key.startswith(("IRODORI_", "HF_", "TORCH_")):
            os.environ[key] = str(value)
    # One process, one model, one decode candidate, official watermark retained.
    os.environ["IRODORI_MAX_CONCURRENT_SYNTHESIS"] = "1"
    os.environ["IRODORI_EMPTY_CACHE_INTERVAL"] = "0"
    os.environ["IRODORI_PRELOAD"] = "true"
    from contextlib import asynccontextmanager
    import uvicorn
    import torch
    torch.set_num_threads(2)
    from irodori_openai_tts import app as official
    warm = {"ready": False, "last_error": ""}

    def status():
        memory = ({"cuda_allocated_mib": round(torch.cuda.memory_allocated() / 1048576, 1),
                   "cuda_reserved_mib": round(torch.cuda.memory_reserved() / 1048576, 1),
                   "cuda_peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1048576, 1)}
                  if torch.cuda.is_initialized() else {})
        return {"loaded": official.runtime_manager.is_loaded, "loading": official.runtime_manager.is_loading,
                "ready": warm["ready"], "warmup_error": warm["last_error"],
                "model_precision": "bf16", "codec_precision": "bf16", **memory}

    managed = SerialSpeechApp(official.app, runtime_status=status, clear_cache=torch.cuda.empty_cache)
    original_lifespan = official.app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            from irodori_tts.inference_runtime import SamplingRequest
            voice = official.voice_registry.resolve(official.settings.default_voice)
            if not voice.ref_embed:
                raise ValueError("registered_speaker_embedding_required")
            request = SamplingRequest(text="おかえりなさい。今日もお疲れさまでした。", ref_embed=voice.ref_embed,
                num_steps=32, seed=20261022, num_candidates=1, decode_mode="sequential",
                cfg_guidance_mode="independent", cfg_scale_text=3.0, cfg_scale_speaker=5.0,
                t_schedule_mode="linear", context_kv_cache=True, trim_tail=True)
            try:
                result = await asyncio.to_thread(official.runtime_manager.get().synthesize, request)
                if result.sample_rate != 48000 or result.audio.numel() == 0:
                    raise ValueError("warmup_audio_invalid")
                del result
                warm["ready"] = True
                managed.cache_dirty = True
                managed.last_activity = time.monotonic()
            except Exception as exc:
                warm["last_error"] = type(exc).__name__
                raise
            cleanup = asyncio.create_task(managed.idle_cleanup())
            try:
                yield
            finally:
                cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
                official.runtime_manager.get().unload()
    official.app.router.lifespan_context = lifespan
    uvicorn.run(managed, host="127.0.0.1", port=int(config.get("port", 9890)), log_level="info", workers=1)


if __name__ == "__main__":
    main()
