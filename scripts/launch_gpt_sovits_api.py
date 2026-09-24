"""Launch GPT-SoVITS with a scoped inference context and idle cleanup.

The upstream pipeline remains the synthesis authority. Automatic Python GC is
unchanged; only its explicit full collection / allocator cleanup after a
successful utterance is deferred. No model files or upstream sources are edited.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import runpy
import sys
from typing import Any


logger = logging.getLogger("akane.voice_runtime")


def install_tts_inference_mode(pipeline: Any, torch_module: Any) -> bool:
    mode = getattr(torch_module, "inference_mode", None)
    if not callable(mode) or not callable(getattr(pipeline, "run", None)):
        return False
    # PyTorch's decorator enters on every generator resume and exits before
    # each yield, including close/throw. Never hold a thread-local context over
    # an async HTTP response or leak it into another request on the same loop.
    pipeline.run = mode()(pipeline.run)
    return True


class IdleTTSCacheCleanup:
    def __init__(self, pipeline: Any, *, idle_seconds: float = 10.0) -> None:
        self.pipeline = pipeline
        self.original_run = pipeline.run
        self.original_cleanup = pipeline.empty_cache
        self.idle_seconds = idle_seconds
        self.active_runs = 0
        self.pending = False
        self.timer: Any = None
        self.closed = False

    def install(self) -> None:
        self.pipeline.run = self.run
        self.pipeline.empty_cache = self.request_cleanup

    def cancel_timer(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def request_cleanup(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Upstream StreamingResponse iterates in worker threads. Preserve
            # their original cleanup and do not touch the API loop's timer.
            self.original_cleanup()
            return
        # Weight changes and non-inference callers retain immediate cleanup.
        if not self.active_runs or self.closed:
            self.cancel_timer()
            self.pending = False
            self.original_cleanup()
            return
        self.pending = True

    def flush(self) -> None:
        self.cancel_timer()
        if self.active_runs or not self.pending:
            return
        self.pending = False
        try:
            self.original_cleanup()
        except Exception:
            # Never expose upstream exceptions or let maintenance crash the API.
            logger.warning("gpt_sovits_idle_cleanup_failed")

    def schedule(self) -> None:
        if self.active_runs or not self.pending:
            return
        self.cancel_timer()
        if self.closed:
            self.flush()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # The standalone/synchronous pipeline has no idle event loop.
            self.flush()
            return
        self.timer = loop.call_later(self.idle_seconds, self.flush)

    def run(self, inputs: Any):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            yield from self.original_run(inputs)
            return
        self.cancel_timer()
        self.active_runs += 1
        failed = False
        generator = None
        try:
            generator = self.original_run(inputs)
            yield from generator
        except Exception:
            failed = True
            raise
        finally:
            try:
                if generator is not None:
                    generator.close()
            except Exception:
                failed = True
                raise
            finally:
                self.active_runs -= 1
                if failed:
                    self.pending = True
                    self.flush()
                else:
                    self.schedule()

    def close(self) -> None:
        self.closed = True
        self.flush()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--upstream-api", required=True)
    own_args, upstream_args = parser.parse_known_args()
    # The host supplies the configured working directory; never discover models.
    entry = Path(own_args.upstream_api).resolve(strict=True)
    if entry != (Path.cwd() / "api_v2.py").resolve(strict=True):
        raise RuntimeError("gpt_sovits_entrypoint_invalid")
    sys.argv = [str(entry), *upstream_args]
    upstream = runpy.run_path(str(entry), run_name="gpt_sovits_hosted_api")
    pipeline = upstream["tts_pipeline"]
    import torch

    if install_tts_inference_mode(pipeline, torch):
        logger.warning("gpt_sovits_inference_mode_enabled")
    else:
        logger.warning("gpt_sovits_inference_mode_unsupported")
    if callable(getattr(pipeline, "run", None)) and callable(getattr(pipeline, "empty_cache", None)):
        cleanup = IdleTTSCacheCleanup(pipeline)
        cleanup.install()
        upstream["APP"].add_event_handler("shutdown", cleanup.close)
        logger.warning("gpt_sovits_idle_cleanup_enabled")
    else:
        logger.warning("gpt_sovits_idle_cleanup_unsupported")
    upstream["uvicorn"].run(app=upstream["APP"], host=upstream["host"], port=upstream["port"], workers=1)


if __name__ == "__main__":
    main()
