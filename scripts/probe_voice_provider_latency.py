"""Measure configured ASR with synthetic speech; never record a microphone.

Run from the repository root with ``python -m scripts.probe_voice_provider_latency``.
The explicit --enable-asr-for-probe flag overrides only the in-memory snapshot.
No user history, configuration, audio file, or credentials are written.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import tempfile
import time
import unicodedata
from dataclasses import replace

import av

import config
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.voice_runtime.asr_provider import build_voice_asr_provider
from services.tts_client import EdgeTTSClient


def report(stage: str, **fields: object) -> None:
    print(json.dumps({"stage": stage, **fields}, ensure_ascii=False), flush=True)


def pcm16(audio: bytes) -> bytes:
    with av.open(io.BytesIO(audio)) as source:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        frames = [converted for frame in source.decode(audio=0) for converted in resampler.resample(frame)]
        frames.extend(resampler.resample(None))
        return b"".join(frame.to_ndarray().tobytes() for frame in frames)


def spoken_text(text: str) -> str:
    return "".join(c for c in text if not unicodedata.category(c).startswith(("P", "Z"))).lower()


async def probe_reply(settings: BotSettingsView, transcript: str, asr_final_at: float) -> bool:
    """Use the production stream parser with a tiny, tool-free diagnostic prompt."""
    loop = asyncio.get_running_loop()
    audio_jobs = []
    tts = EdgeTTSClient(voice=config.TTS_VOICE, timeout_seconds=20)
    started = time.perf_counter()

    async def synthesize(text: str, ordinal: int) -> bool:
        tts_started = time.perf_counter()
        try:
            audio = await tts.synthesize(text)
        except Exception as exc:
            report("reply_audio", ordinal=ordinal, status="failed", error_type=type(exc).__name__)
            return False
        report(
            "reply_audio",
            ordinal=ordinal,
            status="ready",
            bytes=len(audio),
            tts_ms=round((time.perf_counter() - tts_started) * 1000),
            from_asr_final_ms=round((time.perf_counter() - asr_final_at) * 1000),
        )
        return bool(audio)

    with tempfile.TemporaryDirectory(prefix="akane-voice-provider-probe-") as log_dir:
        runtime = LLMRuntime(log_dir=log_dir, settings=settings)

        def consume() -> bool:
            stream = runtime.stream_chat_json(
                system_prompt='这是连通性测试。仅输出 JSON：{"speech":"一句不超过20字的简短中文回应。","emotion":"normal","memory_metadata":{}}。不要调用工具。',
                user_prompt=transcript,
                fallback={},
                temperature=0,
            )
            try:
                while True:
                    try:
                        event = next(stream)
                    except StopIteration as completed:
                        result = completed.value
                        ok = result is not None and not result.error and not result.fallback_used
                        report(
                            "reply_model",
                            status="accepted" if ok else "failed",
                            elapsed_ms=round((time.perf_counter() - started) * 1000),
                            segments=len(audio_jobs),
                            fallback_used=bool(getattr(result, "fallback_used", False)),
                        )
                        return ok
                    if event.get("type") == "speech_segment" and event.get("text"):
                        ordinal = len(audio_jobs) + 1
                        report(
                            "reply_segment", ordinal=ordinal, elapsed_ms=round((time.perf_counter() - started) * 1000)
                        )
                        audio_jobs.append(asyncio.run_coroutine_threadsafe(synthesize(event["text"], ordinal), loop))
            finally:
                stream.close()

        try:
            model_ok = await asyncio.to_thread(consume)
            audio_ok = await asyncio.gather(*(asyncio.wrap_future(job) for job in audio_jobs))
            return model_ok and bool(audio_ok) and all(audio_ok)
        finally:
            # Bundles can share one client. Close each distinct HTTP pool once.
            clients = {
                id(bundle.client): bundle.client
                for bundle in (runtime.chat, runtime.aux, runtime.memcore_summary, runtime.vision)
                if bundle is not None and bundle.client is not None
            }
            for client in clients.values():
                close = getattr(client, "close", None)
                if callable(close):
                    close()


async def probe(*, enable_asr: bool, include_reply: bool = False) -> bool:
    settings = BotSettingsView.from_config(config)
    if enable_asr:
        settings = replace(settings, fun_asr_realtime_enabled=True)
    resolution = build_voice_asr_provider(settings)
    report(
        "configuration",
        status=resolution.status,
        reason=resolution.reason,
        persistent_asr_enabled=bool(config.FUN_ASR_REALTIME_ENABLED),
        temporary_override=enable_asr,
    )
    if not resolution.ready:
        return False
    stimuli = ("你好，这是一次语音链路测试。", "请继续说明下一步。")
    audio_inputs = []
    tts = EdgeTTSClient(voice=config.TTS_VOICE, timeout_seconds=20)
    for index, text in enumerate(stimuli, 1):
        started = time.perf_counter()
        try:
            audio = await tts.synthesize(text)
            pcm = pcm16(audio)
        except Exception as exc:
            report("stimulus", turn=index, status="failed", error_type=type(exc).__name__)
            return False
        audio_inputs.append(pcm)
        report(
            "stimulus",
            turn=index,
            status="ready",
            tts_ms=round((time.perf_counter() - started) * 1000),
            pcm_duration_ms=round(len(pcm) / 32),
        )
    started = time.perf_counter()
    opened = await resolution.adapter.open_session(filename="synthetic.pcm", content_type="audio/pcm", language="zh")
    report(
        "asr_open", status=opened.status, reason=opened.reason, open_ms=round((time.perf_counter() - started) * 1000)
    )
    if not opened.ok:
        return False
    session = opened.session
    try:
        for index, (text, pcm) in enumerate(zip(stimuli, audio_inputs), 1):
            started = time.perf_counter()
            first_revision_ms = None
            revisions = []
            # Real-time paced PCM plus provider-configured endpoint silence.
            sent_pcm = pcm + bytes(32 * (settings.fun_asr_max_sentence_silence + 200))
            for offset in range(0, len(sent_pcm), 3200):
                update = await session.feed_audio(sent_pcm[offset : offset + 3200])
                if not update.ok:
                    report("asr_feed", turn=index, status=update.status, reason=update.reason)
                    return False
                revisions.extend(update.revisions)
                if first_revision_ms is None and update.revisions:
                    first_revision_ms = round((time.perf_counter() - started) * 1000)
                await asyncio.sleep(max(0, started + min(offset + 3200, len(sent_pcm)) / 32000 - time.perf_counter()))
            endpoint = time.perf_counter()
            result = await session.commit_turn()
            asr_final_at = time.perf_counter()
            revisions.extend(result.revisions)
            final = next((r.text for r in reversed(revisions) if r.quality.value == "final"), "")
            report(
                "asr_turn",
                turn=index,
                status=result.status,
                reason=result.reason,
                first_revision_ms=first_revision_ms,
                commit_wait_ms=round((time.perf_counter() - endpoint) * 1000),
                total_ms=round((time.perf_counter() - started) * 1000),
                revisions=len(revisions),
                recognized_chars=len(final),
                stimulus_match=spoken_text(final) == spoken_text(text),
            )
            if not result.ok:
                return False
        reply_ok = await probe_reply(settings, final, asr_final_at) if include_reply else True
        result = await session.finish_call()
        report("asr_close", status=result.status, reason=result.reason, provider_sessions=1, turns=2)
        return result.ok and reply_ok
    finally:
        await session.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-asr-for-probe", action="store_true")
    parser.add_argument(
        "--include-reply",
        action="store_true",
        help="Also call the configured main model and synthesize its streamed reply",
    )
    args = parser.parse_args()
    try:
        return (
            0
            if asyncio.run(
                asyncio.wait_for(
                    probe(enable_asr=args.enable_asr_for_probe, include_reply=args.include_reply), timeout=120
                )
            )
            else 1
        )
    except Exception as exc:
        report("probe", status="failed", error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
