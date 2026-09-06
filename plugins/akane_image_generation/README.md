# Akane Image Generation

Independent image generation/reference-editing runtime. Source-library stage:
not yet advertised or installed as an Akane tool. The existing host image path
remains the sole product authority until the adapter cutover is verified.

`ImageClient.generate` owns bounded HTTPS JSON/SSE transport and image decoding;
it accepts in-memory reference images and never resolves host sessions or paths.
Only explicit final SSE events are output images. Local codec readiness is not
provider readiness. Cancellation drains a dispatched request: connection loss
means remote completion is unconfirmed, not that inference stopped.

Multi-reference multipart rejection can use the compatible file-ID fallback.
Uploaded IDs are deleted after response consumption; cleanup failure is returned
as a notice. Ambiguous transport failures are never retried automatically.
Only explicit no-compatible-account rejection may be retried before execution.

Source verification: `python -m unittest discover -s plugins/akane_image_generation/tests -v`.
