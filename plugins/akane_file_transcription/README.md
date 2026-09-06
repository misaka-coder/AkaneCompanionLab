# File transcription business runtime (migration A)

This migration slice is not a discoverable plugin or market entry yet. The old
product entry remains until the SDK/Job/market/shared-call cutover is verified.
Do not add new product callers to both implementations.

The isolated worker runs faster-whisper using already cached models only.
No implicit model download, global environment change or second job queue.
The public Akane subprocess SDK owns process creation, cancellation and drain.
This package targets file transcription and shared inference; it does not move
the realtime voice product entry.

Configuration: `AKANE_ASR_PYTHON` selects a prepared faster-whisper interpreter;
`AKANE_ASR_CACHE_DIR` optionally selects its existing Hugging Face cache;
`AKANE_ASR_MODEL` defaults to small, with tiny/base/small/medium/large-v2/large-v3
supported when cached. `AKANE_ASR_DEVICE=auto|cpu|cuda`,
`AKANE_ASR_COMPUTE_TYPE=auto|float16|float32|int8|int8_float16`.
FFmpeg/FFprobe use `AKANE_MEDIA_FFMPEG` / `AKANE_MEDIA_FFPROBE` or PATH.
Health performs actual model loading and short inference, not package detection.

Optional `AKANE_ASR_BACKEND=auto|local|remote` and
`AKANE_ASR_REMOTE_URL=http://127.0.0.1:PORT` select an existing media service.
Auto prefers a healthy configured remote and otherwise selects local before
inference starts; it does not silently retry failed remote inference locally.
The synchronous legacy remote protocol cannot cancel inference, so cancellation
waits for its completed response. Transport loss is unconfirmed completion.
Explicit remote device/compute controls are rejected: that protocol cannot
honor them. Configure those controls on the service itself.

Whisper models require their existing `model.bin`, `config.json` and
`tokenizer.json`; a missing tokenizer cannot trigger the library's online
fallback. The worker sets Hugging Face offline mode locally and forbids Python
child process creation. System speech synthesis in tests supplies a known
English recording; recognition is performed by the actual cached tiny model,
not a stub transcript. This tests intelligibility/timestamps, not multilingual
quality or GPU performance.
