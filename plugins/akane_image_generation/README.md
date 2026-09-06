# Akane Image Generation

Independent image generation/reference-editing runtime and public-SDK plugin.
The source wheel is available in the local static market and replaces the former
host image provider, service, handler and fixed prompt/schema registrations.
Installation is explicit; the host retains shared material reading and delivery.

The plugin exposes `akane.image-generation.run.v1`. It requires explicit
installation grants for prompt invocation, resource reads, artifact writes,
network access and `connection.image_generation.read`; medium-risk operations
use the existing first-time confirmation policy. It never changes user approvals.
The host supplies CapCore/public SDK with invocation work directories and named
connections, plus requests >=2.31, urllib3 >=2.2 and Pillow >=10. The current
market installer does not install dependencies automatically; health exercises
all three output codecs. Missing SDK/codecs reject candidates without replacing
the running version.

Image configuration stays in the host's existing model-service settings; each
invocation resolves the current Bot-specific connection. Secrets never enter
model arguments, reports, artifact metadata, argv or logs. No startup provider
probe, second config store, implicit model installation or paid health call.

`ImageClient.generate` owns bounded HTTPS JSON/SSE transport and image decoding;
it accepts in-memory reference images and never resolves host sessions or paths.
Only explicit final SSE events are output images. Local codec readiness is not
provider readiness. Cancellation drains a dispatched request: connection loss
means remote completion is unconfirmed, not that inference stopped.

Multi-reference multipart rejection can use the compatible file-ID fallback.
Uploaded IDs are deleted after response consumption; cleanup failure is returned
as a notice. Ambiguous transport failures are never retried automatically.
Only explicit no-compatible-account rejection may be retried before execution.

Arguments preserve prompt, 1–5 reference handles, optional PNG-alpha mask,
size/quality/background/output format/compression/input fidelity, n=1–4, title
and send_to_user. Default delivery is false. Images may be inspected with the
existing load_material tool, sent via send_file or reused as references. Private
input copies and output work files are invocation-owned and drained before
cleanup. MIME, extension and dimensions come from actual decode; unexpected
provider size/format and incomplete image counts are notices, not fake compliance.

External acceptance (2026-09-06): one n=2 generation was rejected; one n=1 edit
actually changed a newly created blue test square to red, preserving the central
white circle/background. Request size 1024x1024, returned 1254x1254 PNG. These
are distinct from HTTP fixtures. One additionally authorized n=1 generation
succeeded with a blue ceramic mug on a cream background (1402x1122 PNG versus
requested 1024x1024). All three authorized requests are consumed; no replay or
automatic retry was performed. Live multi-image/mask/file-ID fallback are not
claimed by these samples; deterministic HTTP fixtures cover those protocols.

Source verification: `python -m unittest discover -s plugins/akane_image_generation/tests -v`.
Installed pipeline: `python -m unittest tests.test_image_generation_plugin -v`.
