# Akane cover-song business runtime

Current slice: reusable RVC protocol and process-safety boundary. This package
does **not yet** expose a marketplace plugin or claim the full cover pipeline.
No SDK, Akane host, UI, Job, resource store, or channel imports are required.

`RvcWebUiProvider` owns discovery, parameter mapping, UVR separation and RVC
conversion. The temporary compatibility import in `companion_v01/cover_song.py`
points here; there is no second Gradio implementation in the host.

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
