# Akane cover-song business runtime

Current slice: reusable RVC protocol, process safety, media policy, two-layer
cache and cover pipeline. This package does **not yet** expose a marketplace plugin.
No SDK, Akane host, UI, Job, resource store, or channel imports are required.

`RvcWebUiProvider` owns discovery, parameter mapping, UVR separation and RVC
conversion. The temporary compatibility import in `companion_v01/cover_song.py`
points here; there is no second Gradio implementation in the host.

`CoverMedia` owns audio-stream duration validation, 44.1 kHz stereo decoding,
and WAV/FLAC/320 kbps MP3 mixing. Its async `run` port is injected: the library
does not import an Akane process runner. Both remaining legacy callers bind
the public SDK's cancellation-safe process runner to this same implementation.
The caller owns fresh input/work paths and removes partial outputs on failure.
FFprobe is required: an unverified duration is no longer treated as zero.

Mixing supports FFmpeg 4.3 as well as current versions. Both tracks are padded
to the longer audio duration before undoing amix's default averaging; this
avoids doubling the tail when one track ends first. Input demuxers/protocols
are restricted, metadata is removed, and existing output files are rejected.
No gain or mixing code remains in the legacy host or local-service wrapper.

`CoverPipeline` owns input validation, source hashing, model/parameter cache
keys, stem reuse, conversion, mixing and completed-result restoration. The old
host service is now resource/publication glue over this pipeline. The existing
local-service full-render backend is retained through the provider interface;
its server-side Demucs orchestration still awaits the next slice.

`RemoteRvcClient` and `RemoteRvcProvider` own the byte-only loopback media-host
protocol, including model discovery, UVR ZIP transfer, conversion and complete
Demucs covers. The old `LocalMediaExecutorClient` inherits this implementation;
its old RVC methods/provider have been removed. ASR compatibility is unchanged.
Requests disable ambient proxy/credentials and redirects, bound response/ZIP
sizes, filter timing keys and reject server model/index paths. Cancellation
drains the active HTTP request. Disconnect, incomplete response, timeout or
non-200 status retains the same durable endpoint fence used by the WebUI client.
This conservative policy also fences completed HTTP errors: a proxy error does
not prove its upstream stopped. Restart the dedicated media service **and** its
WebUI before operator acknowledgement for the media-service URL. The two endpoint
ports have separate fences; clear each affected one only after restart. Health
and model discovery do not clear either fence. No cancellation API is invented.

`CoverCache(root, scope=...)` hashes the complete caller identity. Akane's SDK
storage root is instance/plugin-scoped, so the future adapter must also pass the
trusted invocation's profile identity, never a model argument. Cache v3 uses
copied content-addressed audio objects and atomic completed manifests. A reader
verifies size and SHA-256 before restoration. Availability hints only check a
bounded set of manifests and sizes; they are not a cache-hit guarantee. Failed
writes do not invalidate prior completed records and return explicit notices.
Objects are not automatically garbage-collected, avoiding deletion while a
different process is restoring them; this disposable cache may grow over time.

The old v2 cache files remain untouched but are not automatically imported:
their lossy profile filenames cannot prove ownership, and their mixed-file
manifests have no content-integrity evidence. Previously registered generated
artifacts remain readable. Provide the original source to rebuild v3 cache.
Title-only restoration requires matching voice, format and parameters; a
different artist is ambiguous. Force-rebuild requires source media and bypasses
both cache layers.

`ProviderCalls` drains blocking provider threads on repeated cancellation.
Pass its `cancelled` callback into each per-invocation WebUI provider. The
pipeline leaves work-directory cleanup to its caller, which must await the
pipeline before deleting any work/input files. Remote uncertainty remains a
failure rather than being relabeled as confirmed cancellation.

## Dedicated RVC requirement

Use a dedicated, loopback-only WebUI. Its global selected voice is mutable.
Every participating caller must use this provider and the **same lease state
directory**. Interactive WebUI operations and third-party clients do not honor
the lock and must not run concurrently. Separate OS users/containers must not
share a WebUI unless they also share and support the same filesystem lock.

The default lease directory is `akane-rvc-leases` under the calling process's
system temporary directory. If callers have different `TEMP` environments, pass
the same explicit `state_dir`. Loopback host aliases and URL prefixes on the same
port deliberately share one lock/fence. No URLs, paths or audio are written into
the inflight marker, only the random protocol session id.

Voice selection and inference hold one OS lock, including generator drain.
Cancellation stops work before dispatch or after the current remote call has
**confirmed** its terminal response. It cannot kill computation inside WebUI.
After disconnect, malformed response, timeout, or worker termination, a durable
fence rejects subsequent mutation with `rvc_remote_completion_unconfirmed`.
No automatic retry or guessed remote completion is used.

An operator must restart the dedicated WebUI and only then acknowledge recovery:

```powershell
python -m akane_cover_song.recover --base-url http://127.0.0.1:7899 --confirm-restarted
```

Pass the original `--state-dir` too when one was explicitly configured. This
command relies on the operator's confirmation; Gradio 3.14 supplies no stable
server incarnation token. It is not a model-visible capability or automatic
health repair. Do not clear the marker merely because `/config` is responding.

## Output boundary and compatibility

Configure `root_dir` (defaults the trusted output directory to `root_dir/TEMP`),
or an explicit dedicated `output_dir`. Returned audio paths are resolved and
must remain inside that directory. Arbitrary absolute paths and symlink escapes
are rejected before copying. UVR uses unique attempt directories, a copied
input, and exactly one non-empty audio output in each requested stem directory.

Set `TEMP` and `TMP` to the WebUI's dedicated output directory **before starting
its Python interpreter**. Some Gradio versions cache Python's temp directory
during import, so setting `TEMP` later in `infer-web.py` is insufficient. If an
existing server returns files elsewhere, the provider now fails explicitly
rather than reading an arbitrary server-selected file. No existing RVC install
or environment is rewritten by the library.

The supported protocol is the named Gradio dependency layout used by RVC WebUI:
`infer_change_voice`, `infer_convert`, and `uvr_convert`. Missing parameter
mappings fail before dispatch; fixed fn indexes are not assumed. JSON responses
are bounded, redirects/proxy inheritance are disabled, generator sessions are
unique, and terminal markers are mandatory.

## Verification

```powershell
python -m unittest discover -s plugins/akane_cover_song/tests -v
python -m unittest tests.test_rvc_protocol tests.test_cover_song tests.test_local_media_executor
```

Six package tests use actual subprocesses and loopback HTTP for locking, worker
death, cancellation/drain, disconnect, output containment and schema rejection.
They are not model inference tests. On 2026-09-06, a separate isolated loopback
RVC 3.14/CUDA acceptance used existing local weights and six seconds of newly
generated speech: UVR produced two 1,057,964-byte WAVs and RVC produced a
478,444-byte, 40 kHz mono, 5.98-second WAV. The final safety implementation was
retested against the model and left no inflight marker. No new weights, paid
service, personal audio, QQ sending, or real desktop playback was involved.

Six additional tests execute actual FFmpeg for all three formats, measured
gain, unequal-track tails, audio duration inside longer video, invalid media,
existing-file protection, child-process cancellation, and both legacy caller
paths. All six pass with both the system FFmpeg and the existing RVC FFmpeg 4.3.
These use generated tones, not singing-quality evaluation or plugin lifecycle
acceptance.

Seven cache/pipeline tests additionally cover actual cross-process writes,
partial-write rollback, identity collision avoidance, corruption, title lookup,
two-layer reuse, force rebuild, failure notices and repeated cancellation. The
pipeline tests use explicit provider doubles with real FFmpeg; they do not
claim model quality. A separate 2026-09-06 isolated real UVR/RVC/FFmpeg pipeline
produced a 719,776-byte, 5.997275-second WAV in 11.194 seconds. Both source-key
reuse and title-only restoration returned byte-identical audio without another
inference. The endpoint fence was clear and the owned server was stopped.
The rebuilt wheel imported `CoverPipeline`, `CoverCache` and `CoverMedia` under
`python -I` without importing any Akane host module. Plugin market installation,
Jobs, lifecycle and channel acceptance are still pending. Six additional real
loopback HTTP tests cover remote parameter/byte transfer, model path rejection,
ZIP bounds, repeated cancellation/drain, disconnect/502 fencing and endpoint
validation. Their audio payloads are explicit transport fixtures, not inference.
