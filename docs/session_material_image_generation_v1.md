# Session Material Transfer + PinAI Image Generation V1

Updated 2026-09-06: the former built-in `generate_image` provider/service/handler
and fixed schema/prompt have been removed. The optional market plugin exposes
`akane.image-generation.run.v1`; install/enable it explicitly. `load_material`
and common session material registration/delivery remain host-owned. Current
acceptance and limitations: [plugin migration V2](optional_business_plugins_v2.md).
The Phase 1 shapes below document the original workflow; the installed plugin's
descriptor is the current schema authority and defaults `send_to_user` to false.

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
IMAGE_GENERATION_BASE_URL=https://api.pinaic.com/v1
IMAGE_GENERATION_API_KEY=
IMAGE_GENERATION_MODEL=gpt-image-2
IMAGE_GENERATION_TIMEOUT_SECONDS=300
IMAGE_GENERATION_MAX_INPUT_IMAGES=5
IMAGE_GENERATION_MAX_OUTPUT_IMAGES=4
IMAGE_GENERATION_MAX_IMAGE_BYTES=8388608
IMAGE_GENERATION_MAX_TOTAL_INPUT_BYTES=20971520
IMAGE_GENERATION_MAX_OUTPUT_BYTES=26214400
```

当前插件每次通过有权限的命名连接读取当前 Bot 模型服务设置；专用图片 key 为空时保留
现有聊天 key 回退规则，管理员需确保该凭证确实适用于所配置的图片服务。插件不另存配置。
安装健康检查只验证本地编解码，不访问 `/models`、不发起付费请求，也不保证服务商可用。
网络生成/编辑遵循首次确认策略；鉴权、禁用或服务商拒绝在实际调用时结构化报告。

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
