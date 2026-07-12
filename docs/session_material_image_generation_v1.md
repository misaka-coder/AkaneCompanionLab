# Session Material Transfer + PinAI Image Generation V1

## Goal

Akane does not need a permanent asset library for this milestone. She needs a
safe, session-scoped material transfer layer:

```text
Attachment Inbox / GeneratedFileStore handles
  -> resolve inside current profile + session
  -> read managed image bytes
  -> feed the current chat model or PinAI
  -> register outputs in GeneratedFileStore
  -> deliver through the existing client/QQ artifact path
```

The model sees handles such as `img_001` and `gen_001`. It never supplies local
paths, base64, private URLs, API keys, or provider file ids.

## Phase 1 Scope

1. Native `load_material` tool:
   - accepts one to five image handles;
   - resolves only current profile/session material;
   - injects original image blocks into the next native model round;
   - returns only safe handles/status in tool history and memcore.
2. Native `generate_image` tool:
   - no references -> PinAI `/v1/images/generations`;
   - references -> PinAI `/v1/images/edits`;
   - multiple references use repeated `image[]` multipart fields first, matching
     the OpenAI GPT Image contract supported by PinAI's compatible endpoint;
   - if PinAI rejects a multi-image multipart request, upload those managed
     images to `/v1/files` with `purpose=user_data` and retry once with
     `images[].file_id`; provider file ids stay call-local;
   - optional mask is a separately resolved current-session image;
   - output bytes are written to GeneratedFileStore and emitted through the
     existing `generated_file_ready` event.
3. Generated image reuse:
   - a generated PNG/WebP/JPEG handle can be used by `load_material` or as a
     later `generate_image.reference_images` input.

Document/PDF/PPT image layout is a separate phase after this provider path is
verified with real requests.

## Native Tool Contract

`load_material`:

```json
{
  "targets": ["img_001", "gen_002"],
  "purpose": "compare the composition before editing"
}
```

`generate_image`:

```json
{
  "prompt": "Keep the subject and change the background to a future city at night.",
  "reference_images": ["img_001", "img_002"],
  "mask_image": "img_003",
  "size": "1536x1024",
  "quality": "high",
  "background": "auto",
  "output_format": "png",
  "n": 1,
  "output_title": "Future city version",
  "send_to_user": true
}
```

The host validates fixed fields and limits. The model retains freedom over the
creative prompt, reference selection, aspect ratio, quality, and output intent.

## Configuration

```env
IMAGE_GENERATION_ENABLED=false
IMAGE_GENERATION_BASE_URL=https://us.pinai-cn.com/v1
IMAGE_GENERATION_API_KEY=
IMAGE_GENERATION_MODEL=gpt-image-2
IMAGE_GENERATION_TIMEOUT_SECONDS=300
IMAGE_GENERATION_MAX_INPUT_IMAGES=5
IMAGE_GENERATION_MAX_OUTPUT_IMAGES=4
IMAGE_GENERATION_MAX_IMAGE_BYTES=8388608
IMAGE_GENERATION_MAX_TOTAL_INPUT_BYTES=20971520
IMAGE_GENERATION_MAX_OUTPUT_BYTES=26214400
```

The image key is separate from chat/vision configuration. A deployment may use
the same PinAI key value, but code must not silently send an unrelated chat
provider key to the PinAI host.

## Provider Rules

- Default model: `gpt-image-2`.
- Request `response_format=b64_json` and `stream=true`.
- Read SSE until final output; also accept a normal JSON response.
- Decode only bounded base64 image fields.
- Never log response bodies, uploaded bytes, provider file ids, or credentials.
- Do not persist provider file ids as Akane assets. Local managed files are the
  source of truth.
- Provider failure is a structured tool failure and never creates an empty or
  fake generated file.

## Memory And History

- Native tool call/result boundaries remain in provider history.
- memcore receives safe tool arguments (handles and creative parameters) plus a
  safe result summary.
- `ToolExecutionResult.model_image_inputs` is internal-only and must not be
  copied to stream events, state updates, prompt text, logs, or memcore.
- Attachment/material traces continue to store only handles/status/uploader.

## Acceptance

1. A later turn can call `load_material(["img_001"])` and the next Sonnet call
   contains the original image block.
2. Text-to-image creates a non-empty managed image and emits one delivery event.
3. One or more current-session image handles can be sent to image edits.
4. A generated image handle can be reused as a later reference.
5. Cross-session, cleared, missing, oversized, unsupported, and non-image
   targets fail closed.
6. No base64, absolute path, private URL, API key, or provider file id appears
   in user-visible responses, logs, native tool text results, or memcore.
