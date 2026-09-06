# File transcription plugin

The public SDK entry point and `plugins/market.toml` expose this optional plugin
only after explicit installation and enablement. The old built-in file tool,
ASR business helpers and transcript rendering implementation have been removed.
Inputs are current-session attachment/generated-file handles, never raw paths.
Batch partial failures are reported; md/txt/json/srt/vtt outputs may be merged
or separate. Outputs are registered, not automatically sent or played.

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

The unchanged realtime voice API uses this package's public `compatibility`
library through `services/asr_business.py`. Source releases must ship the bundled
library, or install its distribution; this does not activate the optional file
tool. `inference.py` is the sole model/PCM/recognition implementation shared by
the file worker and realtime consumer. The local media service also binds to
the public `LocalTranscriber`, retaining its synchronous HTTP response shape.
Missing library/model/runtime returns unavailable, never a fake transcript.

Desktop lyric timelines call the actually installed transcription capability
through normal permissions; missing/disabled plugins produce an unavailable
timeline. Original audio is unchanged. Successful JSON can be reused only for
its recorded source handle; mixed-source transcripts are not reused wholesale.

Legacy `LOCAL_MEDIA_EXECUTOR_BASE_URL` still serves realtime ASR/RVC. Optional
file transcription requires explicitly setting `AKANE_ASR_REMOTE_URL` to reuse
that endpoint. Plugin workers do not import host settings or mutate them.

Validation: `python -m unittest discover -s plugins/akane_file_transcription/tests -v`
and `python -m unittest tests.test_file_transcription_plugin tests.test_file_asr_consumers -v`.
The installation test builds a real market wheel and exercises isolated
activation, formats/batch, Job cancellation and disable/enable/uninstall, source
scope, real CPU recognition, lyric reuse, and existing delivery boundaries.
QQ transport is a test double, not a real network delivery; GPU and real remote
ASR service inference are not claimed.
