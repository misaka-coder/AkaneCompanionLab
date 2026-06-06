# Akane Local Capability & Workflow Registry V1

Updated: 2026-06-06

This document captures the design discussion around Akane's future local
capability system: user-configurable tools, model backends, external executors,
workflow templates, MCP integration, and how these should connect to the
desktop pet, character workshop, voice stack, workspace, and future plugin
ecosystem.

It is intentionally detailed so future implementation can continue after
context compaction.

## 0. Implementation Status

Phase 1 first backend slice is implemented. Phase 1B read-only frontend
dashboard wiring is in progress/implemented as a narrow slice: the control
center reads `/capabilities` as optional enhancement data, translates raw
tool/provider entries into user-facing ability modules, and keeps configuration
and local-environment probing out of the UI until a separate management slice.

Files:

- `companion_v01/local_capability_catalog.py`
  - Builds a read-only capability catalog from existing `engine.tool_handlers`,
    prompt-time capability modules, and lightweight provider status checks.
  - Keeps concrete products in `adapter`, not in base `type` or `source`.
  - Exposes profile-scoped config path and machine-local discovery path as safe
    metadata.
- `companion_v01/routes/capabilities.py`
  - `GET /capabilities`
  - `POST /capabilities/local-environment-check`
  - The local environment check probes only known localhost services and never
    auto-enables providers.
- `companion_v01/app.py`
  - Registers the capabilities router.
- `tests/test_backend_route_modules.py`
  - Verifies read-only catalog shape, existing backend tool exposure, provider
    classification, safe config paths, no obvious sensitive fields, and local
    discovery not being enablement.
- `desktop_pet_next/src/control-center/data-sources.js`
  - Reads `/capabilities` alongside the control-center runtime data.
  - Treats it as optional: missing or invalid catalog data does not block
    snapshot hydration or legacy fallback.
  - Groups raw `tool.*`, `provider.*`, and `prompt_module.*` entries into
    readable dashboard modules such as "音频与语音" and "本地模型与执行器".
- `desktop_pet_next/src/control-center-lab.js`
  - Renders module status labels from the catalog-derived dashboard model.
  - Does not expose raw ids such as `transcribe_media` in the normal ability
    dashboard.
- `desktop_pet_next/scripts/control-center-runtime-probe.mjs`
  - Verifies optional catalog enrichment, catalog failure degradation, and
    no raw capability id leakage in module text.

Verification:

```bash
python -m unittest tests.test_backend_route_modules
python -m py_compile companion_v01/local_capability_catalog.py companion_v01/routes/capabilities.py companion_v01/app.py tests/test_backend_route_modules.py
git diff --check
```

## 1. Short Version

Akane should not become a giant bundled model distribution.

Akane should become the host that can discover, configure, explain, confirm,
and orchestrate local and external capabilities.

The target shape:

```text
Akane
  -> Capability Registry
      -> Tool entries
      -> Provider entries
      -> Workflow entries
      -> MCP tool entries
      -> Local plugin entries
  -> Agent / Workshop / Voice / Workspace consume those entries
  -> Heavy execution happens in external executors or small internal runtimes
```

Important distinction:

- `Tool`: an action Akane can choose or expose, such as opening a browser,
  reading a file, organizing the workspace, or starting an image cutout task.
- `Provider`: an engine or backend that powers a feature, such as GPT-SoVITS,
  RVC, faster-whisper, ComfyUI, an audio separation runtime, or a browser
  automation service.
- `Workflow`: a concrete processing recipe that uses a provider, such as
  "ComfyUI portrait cutout", "ComfyUI character image generation",
  "GPT-SoVITS character TTS", or "RVC voice conversion".
- `Task`: a product-facing operation, such as "auto cut out this portrait in
  the workshop" or "transcribe this dropped audio file".

Do not flatten these concepts into one list of tools. If everything is a tool,
configuration, status, permission, and UI will become muddy.

## 2. Why This Matters

Akane already has many backend capabilities:

- attachment workspace and generated file store
- document and media tools
- audio separation, voice cleanup, media conversion, ASR, voice dataset prep
- long-term memory and memory retrieval
- task workspace
- desktop pet context, screen vision, proactive wake fields
- character packs, persona fields, portrait assets, layout and bubble config
- control-center action contracts and runtime snapshots

The problem is that these abilities are mostly hidden behind backend prompts,
diagnostics, or scattered UI surfaces. A user cannot easily answer:

- What can Akane do right now?
- Which capabilities are unavailable because a dependency or model is missing?
- Which capabilities are local, cloud, external, MCP, or plugin-provided?
- Which operations require confirmation?
- Which features can be upgraded by configuring better local models?
- Which workflows are meant for the character workshop versus ordinary chat?

For open-source users and advanced local users, this is a major opportunity.
They may already have ComfyUI, RVC, GPT-SoVITS, faster-whisper, Demucs, UVR-like
tools, local browser automation, or private MCP servers installed. Akane should
give those users stable "slots" to plug these systems into the companion.

## 3. Relation To Existing Docs

This document does not replace `docs/capability_registry_v1.md`.

Existing `capability_registry_v1.md` is mainly about prompt-time capability
selection:

```text
client mode + runtime state -> light hints + selected tool manuals
```

That registry decides which tool instructions enter the model prompt and which
tool calls can execute during a turn.

This document is broader. It defines a product/runtime catalog of local
capabilities:

```text
installed providers + configured workflows + internal tools + MCP tools
  -> capability catalog
  -> status/config/permission UI
  -> agent/workshop/voice/workspace consumers
```

The two systems should eventually meet:

- Local Capability Registry knows what exists and whether it is ready.
- Prompt Capability Registry chooses the minimal available tools for the
  current turn.
- Tool execution guard still enforces the selected tool set.

### 3.1 Boundary With Existing Runtime

This system should wrap and expose the current tool/runtime stack. It should not
replace it.

Existing components that should keep their current responsibilities:

- `companion_v01/tool_runtime.py`
  - owns `BaseToolHandler` implementations
  - normalizes model-emitted tool calls
  - executes deterministic backend tools
  - returns `ToolExecutionResult`, `followup_context`, `stream_events`, and
    generated artifacts
- `companion_v01/capability_registry.py`
  - owns prompt-time module selection
  - decides which tool manuals are injected for the current client mode and
    runtime state
  - prevents hidden tools from executing even if the model hallucinates them
- `docs/capability_registry_v1.md`
  - documents the prompt-time selector and light/heavy tool hint model

The Local Capability & Workflow Registry adds a product/runtime layer above
those systems:

```text
Local Capability Catalog
  -> describes configured providers, workflows, tools, MCP tools, status, risk
  -> exposes safe UI/diagnostic metadata
  -> resolves preferred provider/workflow chains
  -> feeds readiness and availability into prompt-time selection

Prompt Capability Registry
  -> picks minimal tools for this turn
  -> uses catalog readiness but does not own local model configuration

Tool Runtime
  -> executes selected built-in tools
  -> may call provider/workflow runners after permission checks
```

In other words, this is not a rewrite. It is a discoverability, configuration,
health, permission, and workflow layer that sits on top of existing handlers.

## 4. Reference Project Takeaways

We reviewed `Rvosy/Sakura` as a reference point:

- Repository: https://github.com/Rvosy/Sakura
- Technical README:
  https://github.com/Rvosy/Sakura/blob/main/docs/TECHNICAL_README.md
- Plugin SDK:
  https://github.com/Rvosy/Sakura/blob/main/docs/SAKURA_PLUGIN_SDK.md

Useful ideas to learn from:

- Make the product promise clear: an active desktop pet Agent that observes,
  asks for permission, uses tools, and responds with expression/voice.
- Use external executors for heavy systems such as GPT-SoVITS, MCP servers, and
  browser automation.
- Treat plugins/MCP as extension sources.
- Expose tool confirmation and debug/history surfaces as user-visible features.

Boundary:

- Do not copy third-party source unless its license allows it and the code path
  is reviewed. Learn the architecture and product framing, not the code.
- MCP itself is an open protocol direction; implementing MCP support is not
  copying a project.

## 5. Design Principles

### 5.1 Extension First, Not Bundle First

Do not bundle every model.

Prefer:

- user-configurable endpoints
- model paths
- workflow JSON paths
- external CLI/service adapters
- optional lightweight internal runtimes for stable narrow tasks

This keeps Akane smaller, easier to open source, and friendlier to advanced
users who already maintain their own model stack.

### 5.2 Local Does Not Mean Automatically Safe

Local tools can still be risky.

Examples:

- opening a browser
- controlling a browser
- reading files
- writing character pack assets
- running user-provided scripts
- invoking ComfyUI workflows that save files
- calling RVC/GPT-SoVITS with arbitrary paths

Every capability must carry risk and confirmation metadata.

### 5.3 No Fake Bridges

If a provider or workflow has no real execution boundary, mark it unavailable or
deferred. Do not return fake success.

This follows the control-center principle:

```text
Do not bridge an action just to make UI feel complete.
```

### 5.4 The User Should Not Need To Understand Nodes

ComfyUI workflows are node graphs. RVC and GPT-SoVITS are processing chains.
The user should not have to edit these inside Akane.

Akane should support:

- importing an existing workflow
- mapping named slots such as `input_image`, `prompt`, `output_image`
- testing the workflow
- showing missing model/node/endpoint errors

Akane should not attempt to become a full ComfyUI node editor.

### 5.5 Provider And Workflow Are Separate

Example:

```text
Provider: ComfyUI at http://127.0.0.1:8188
Workflow A: character portrait cutout
Workflow B: character illustration generation
Workflow C: background cleanup
```

One provider can support many workflows. One workflow may depend on one provider
and several model files.

### 5.6 Capability Status Must Be Structured

No silent failure.

Use status values:

- `ready`
- `disabled`
- `missing_config`
- `missing_model`
- `missing_executor`
- `unreachable`
- `misconfigured`
- `unsupported_platform`
- `error`

Each unavailable state should have a reason that can be shown in the UI.

### 5.7 Artifacts Stay In Akane Stores

External executors may create files, but Akane should import or register final
outputs into existing stores:

- character pack asset directory for workshop portrait outputs
- GeneratedFileStore for user-facing generated outputs
- Attachment/Workspace stores for temporary working files
- voice dataset zip output for training prep

Do not leak arbitrary full local paths into prompts, snapshots, or public logs.

## 6. Core Concepts

### 6.1 Capability Entry

A capability entry is the catalog item visible to the system and, when safe, to
the user.

Suggested shape:

```json
{
  "id": "workshop.portrait.cutout",
  "kind": "workflow",
  "type": "asset_processor",
  "source": "external_executor",
  "adapter": "comfyui",
  "name": "角色立绘抠图",
  "description": "将上传的角色图片处理为透明背景立绘。",
  "enabled": true,
  "status": "ready",
  "risk": "medium",
  "requiresConfirmation": false,
  "usedBy": ["workshop"],
  "providerId": "provider.comfyui.local",
  "workflowId": "workflow.comfyui.portrait_cutout",
  "configSchemaId": "schema.workflow.comfyui.slot_mapping.v1",
  "lastCheckedAt": "2026-06-06T00:00:00Z"
}
```

### 6.2 Tool

A tool is an action that the Agent may choose or that UI may expose as a command.

Examples:

- `browser.open`
- `browser.search`
- `workspace.inspect`
- `workspace.transcribe_media`
- `workshop.portrait.cutout`
- `workshop.portrait.generate_candidates`
- `voice.test_tts`
- `mcp.bilibili.search`

Tool fields:

```json
{
  "id": "browser.open",
  "kind": "tool",
  "source": "builtin",
  "name": "打开浏览器",
  "description": "在用户确认后打开浏览器或指定网址。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "url": { "type": "string" },
      "reason": { "type": "string" }
    },
    "required": ["url"]
  },
  "risk": "high",
  "requiresConfirmation": true,
  "usedBy": ["agent", "desktop_pet"],
  "status": "ready"
}
```

### 6.3 Provider

A provider is an executor or service.

Examples:

- ComfyUI HTTP endpoint
- GPT-SoVITS API endpoint
- RVC CLI/API
- faster-whisper local runtime
- Demucs/UVR-like audio separation runtime
- browser automation provider
- MCP server

Provider fields:

```json
{
  "id": "provider.comfyui.local",
  "kind": "provider",
  "type": "asset_processor",
  "source": "external_executor",
  "adapter": "comfyui",
  "executionMode": "external",
  "name": "本地 ComfyUI",
  "enabled": true,
  "status": "ready",
  "endpoint": "http://127.0.0.1:8188",
  "healthCheck": {
    "method": "GET",
    "path": "/system_stats"
  },
  "configSchemaId": "schema.provider.comfyui.v1"
}
```

Provider classification rule:

- `type` says what the provider does, such as `tts_provider`,
  `asr_provider`, `asset_processor`, or `browser_provider`.
- `source` says where the implementation comes from, such as `builtin`,
  `external_executor`, `mcp`, or `local_plugin`.
- `adapter` says which concrete product/protocol/runtime is used, such as
  `comfyui`, `gpt_sovits`, `rvc`, `edge_tts`, `faster_whisper`, or
  `custom_http`.
- `executionMode` says how it runs: `external`, `internal`, or `auto`.

Concrete product names should not be added to the base `type` or `source`
enums. They belong in `adapter` and provider IDs.

### 6.4 Workflow

A workflow is a concrete recipe that maps Akane inputs to provider-specific
execution details.

Examples:

- ComfyUI portrait cutout
- ComfyUI character image generation
- GPT-SoVITS character TTS
- RVC voice conversion
- voice dataset preparation chain
- ASR transcription pipeline

Workflow fields:

```json
{
  "id": "workflow.comfyui.portrait_cutout",
  "kind": "workflow",
  "providerId": "provider.comfyui.local",
  "type": "asset_processor",
  "name": "ComfyUI 角色立绘抠图",
  "workflowPath": "workflows/comfyui/portrait_cutout.json",
  "slots": {
    "input_image": {
      "node": "12",
      "path": "inputs.image"
    },
    "output_image": {
      "node": "31",
      "path": "outputs.images[0]"
    }
  },
  "inputSchema": {
    "type": "object",
    "properties": {
      "imageHandle": { "type": "string" },
      "outputPackId": { "type": "string" },
      "outfit": { "type": "string" },
      "emotion": { "type": "string" }
    },
    "required": ["imageHandle", "outputPackId", "outfit", "emotion"]
  },
  "output": {
    "type": "transparent_png",
    "targetStore": "character_pack_assets"
  }
}
```

### 6.5 Task

A task is the product-level operation. Tasks should hide provider complexity.

Example:

```text
Workshop button:
  "自动抠图"

Task:
  workshop.portrait.cutout

Behind the task:
  resolve enabled cutout workflow
  validate input image
  call ComfyUI
  retrieve output image
  write through safe character-pack path
  refresh portrait tab
```

The user sees a simple operation. The registry keeps the underlying provider
and workflow replaceable.

## 7. External Executor Mode

External executor mode means Akane calls a service or CLI installed by the user.

This should be the first supported mode for heavy capabilities.

### 7.1 Why External First

Pros:

- keeps Akane lightweight
- avoids bundling large models
- lets advanced users reuse their existing setup
- avoids chasing every model ecosystem internally
- fits open-source distribution better

Cons:

- users must install dependencies
- status checks are required
- workflow/node versions may differ
- error messages must be good

### 7.2 Examples

ComfyUI:

```text
Akane -> http://127.0.0.1:8188 -> workflow JSON -> output image
```

GPT-SoVITS:

```text
Akane -> http://127.0.0.1:9880/tts -> audio bytes -> playback/cache
```

RVC:

```text
Akane -> local CLI/API -> converted voice audio -> generated output/cache
```

MCP:

```text
Akane -> MCP server -> tool list + tool calls
```

Browser automation:

```text
Akane -> Playwright/browser provider -> browser action
```

## 8. Internal Lightweight Executor Mode

Internal lightweight executor mode means Akane ships a small, stable runtime for
a narrow capability.

Good candidates:

- ONNX/rembg-style portrait cutout
- faster-whisper ASR if dependency packaging is acceptable
- simple ffmpeg-based audio conversion
- lightweight image validation/cropping

Less suitable for early internal mode:

- full ComfyUI image generation stack
- GPT-SoVITS full runtime and large voice models
- RVC full runtime
- large UVR/MDX/RoFormer variants

Internal executors should be chosen based on:

- high project value
- stable dependencies
- manageable install size
- cross-platform viability
- predictable error modes

### 8.1 Execution Mode

Providers should declare how they execute:

```text
external
internal
auto
```

- `external`: Akane calls a user-installed service or CLI, such as ComfyUI,
  GPT-SoVITS API, RVC CLI/API, or a custom HTTP service.
- `internal`: Akane owns the runtime dependency and can execute the capability
  directly, such as a future ONNX cutout runner or bundled faster-whisper path.
- `auto`: Akane can choose the best available provider from a priority chain.

Example:

```yaml
providers:
  - id: provider.asr.faster_whisper.internal
    type: asr_provider
    source: builtin
    adapter: faster_whisper
    execution_mode: internal
    enabled: true
    model: large-v3

  - id: provider.asr.custom_http
    type: asr_provider
    source: external_executor
    adapter: custom_http
    execution_mode: external
    enabled: false
    endpoint: http://127.0.0.1:9000/asr
```

If a capability can run through both external and internal providers, the
registry should not expose two unrelated UX concepts. It should expose one
product capability with a provider resolution policy.

Example:

```yaml
capabilities:
  - id: voice.input.asr
    type: asr_provider
    execution_mode: auto
    provider_priority:
      - provider.asr.custom_http
      - provider.asr.faster_whisper.internal
      - provider.asr.browser_fallback
```

The UI can still show which concrete provider is active.

### 8.2 Graceful Degradation

The registry should resolve the best available implementation instead of
crashing when a preferred provider is unavailable.

This is especially important for downloaded character packs. A pack may request
a premium voice chain, but the user's computer may not have the required local
executors or models.

Example voice fallback chain:

```text
character GPT-SoVITS + RVC
  -> character GPT-SoVITS
  -> remote/custom TTS API
  -> Edge TTS
  -> text bubble only
```

Structured policy example:

```yaml
resolution_policies:
  voice.tts.character:
    strategy: first_ready
    candidates:
      - provider.voice.gpt_sovits.character_rvc
      - provider.voice.gpt_sovits.character
      - provider.voice.custom_http
      - provider.voice.edge_tts
      - provider.voice.text_only
```

Degradation should be visible and gentle:

```text
这个角色请求使用 GPT-SoVITS + RVC 声线，但本机暂时没有可用的 RVC。
我先用 GPT-SoVITS/默认语音陪你说话，等你配好模型后再切回角色声线。
```

Do not treat graceful degradation as fake success. The selected fallback should
be explicit in status and logs:

```json
{
  "capabilityId": "voice.tts.character",
  "requestedProviderId": "provider.voice.gpt_sovits.character_rvc",
  "activeProviderId": "provider.voice.edge_tts",
  "status": "degraded",
  "reason": "requested_provider_missing_executor"
}
```

## 9. Capability Types

Proposed type enum:

```text
tool
provider
workflow
mcp_tool
asset_processor
tts_provider
asr_provider
voice_conversion_provider
audio_separation_provider
browser_provider
desktop_automation_provider
local_plugin
```

`type` describes what the capability is or what role it plays. It should not
name concrete products or runtimes.

Good:

```json
{
  "type": "tts_provider",
  "source": "external_executor",
  "adapter": "gpt_sovits"
}
```

Avoid:

```json
{
  "type": "gpt_sovits",
  "source": "gpt_sovits"
}
```

Type should drive:

- display grouping
- configuration UI
- runtime health checks
- permission defaults
- agent eligibility

## 10. Sources

Proposed source enum:

```text
builtin
backend_tool
tauri_command
external_executor
mcp
local_plugin
character_pack
```

`source` describes where the implementation comes from. `type` describes what
the capability does.

`source` must stay coarse and implementation-location oriented:

- `builtin`: implemented directly by Akane backend/frontend code
- `backend_tool`: backed by an existing `BaseToolHandler`
- `tauri_command`: backed by desktop-only Tauri commands or window APIs
- `external_executor`: calls a local external service or CLI
- `mcp`: discovered through an MCP server
- `local_plugin`: loaded from a future local plugin system
- `character_pack`: declared by a character pack as a preference or permission
  request; not an executable implementation source by itself

Concrete systems such as ComfyUI, GPT-SoVITS, RVC, faster-whisper, Demucs, and
custom HTTP APIs should be represented by `adapter`, not by `source`.

## 11. Risk Model

Risk levels:

```text
low
medium
high
dangerous
```

Suggested defaults:

| Capability | Risk | Confirmation |
| --- | --- | --- |
| Inspect local capability status | low | no |
| Use TTS provider for current reply | low/medium | no |
| Run ASR on user-provided audio | medium | no if user initiated |
| Run image cutout on selected workshop image | medium | no if user clicked |
| Generate image with ComfyUI | medium/high | maybe |
| Open browser URL | high | yes |
| Browser automation/clicking | high | yes |
| Read arbitrary local file | high | yes |
| Write into character pack asset dir | medium | no if workshop-initiated |
| Run user-provided script/plugin | dangerous | explicit install/enable |

Confirmation should include:

- what Akane wants to do
- why
- which provider/tool will run
- what inputs are involved
- whether files will be created/modified

Example:

```text
我准备用本地 ComfyUI 的“角色立绘抠图”流程处理这张图片，
输出会写入当前角色包的 school/normal.png。可以吗？
```

## 12. Status And Health Checks

Every provider should support a health check when possible.

Examples:

ComfyUI:

- endpoint configured?
- HTTP reachable?
- optional `/system_stats` available?
- workflow JSON exists?
- required slots mapped?

GPT-SoVITS:

- API URL configured?
- HTTP reachable?
- selected character weights exist?
- reference audio exists?
- endpoint returns a structured error on test request?

RVC:

- CLI path or API endpoint configured?
- model path exists?
- index path optional?
- test conversion can start?

ASR:

- provider enabled?
- model path/name configured?
- runtime import available?
- sample short audio test passes?

MCP:

- server config exists?
- process/connect starts?
- tool list fetched?
- tool names valid and unique?

Status output shape:

```json
{
  "ok": false,
  "status": "missing_model",
  "reason": "Configured GPT-SoVITS SoVITS weight does not exist.",
  "capabilityId": "voice.tts.gpt_sovits.character",
  "safeDetails": {
    "missingField": "weights.sovits"
  }
}
```

Do not expose full sensitive paths in public snapshots or model prompts. UI can
show local paths only in local Tauri windows when appropriate.

### 12.1 Local Environment Check

Phase 1 can turn the read-only catalog into a useful "local environment check"
without executing heavy workflows.

This should be a safe, bounded localhost probe, not a broad port scanner.

Allowed V1 probes:

- known localhost endpoints
  - `http://127.0.0.1:8188` for ComfyUI
  - `http://127.0.0.1:9880` for GPT-SoVITS-compatible API
- endpoints the user has already configured
- local CLI commands only when explicitly configured

Avoid:

- scanning LAN/private network ranges
- scanning arbitrary port ranges
- sending user files during detection
- auto-enabling high-risk capabilities just because a port is reachable

Discovery flow:

```text
User opens capability page or clicks "检查本地环境"
  -> Akane probes known safe localhost endpoints
  -> writes results to machine-local discovery cache
  -> UI shows "detected but not bound" providers
  -> user chooses whether to bind/enable them
```

Good user experience:

```text
检测到本机 ComfyUI 正在运行（127.0.0.1:8188）。
你可以把它绑定为“本地图像工作流 Provider”，用于角色立绘抠图或候选图生成。
```

Avoid auto-claiming:

```text
ComfyUI detected, all image tools enabled.
```

Detection is discovery, not permission.

The probe should be rate-limited, non-blocking, and repeatable on demand. A
failed probe should update capability status but must not block navigation or
ordinary desktop pet startup.

## 13. Configuration Shape

### 13.1 Profile Capability Config And Local Discovery

Use a hybrid config model:

```text
profile-scoped explicit config
  -> user choices, enabled providers, permission grants, workflow bindings

machine-local discovery cache
  -> auto-detected localhost services and environment hints
```

Recommended explicit config path:

```text
users_data/<profile_user_id>/capabilities/capabilities.yaml
```

Why profile-scoped:

- different users on the same machine may prefer different providers
- permissions should belong to the user, not only to the installation
- character packs and voice preferences may differ by profile
- it matches the desktop-pet direction where profile and character state matter

Recommended machine-local discovery cache:

```text
users_data/_local/capabilities/discovery.json
```

Why separate discovery cache:

- auto-detected endpoints are not the same as user-approved configuration
- local environment probes should not silently grant permissions
- machine-specific probe results should not be treated as portable profile data

Avoid making `pet_state.json` the main capability config store. It is useful for
desktop runtime flags, but provider/workflow configuration will outgrow it.

Avoid using only:

```text
users_data/capabilities/capabilities.yaml
```

because it is not profile-scoped enough.

Avoid using only:

```text
data/config/capabilities.yaml
```

because it behaves like global app config and does not express per-user
permissions well.

Example:

```yaml
providers:
  - id: provider.comfyui.local
    type: asset_processor
    source: external_executor
    adapter: comfyui
    execution_mode: external
    enabled: true
    endpoint: http://127.0.0.1:8188

  - id: provider.asr.faster_whisper
    type: asr_provider
    source: builtin
    adapter: faster_whisper
    execution_mode: internal
    enabled: true
    model: large-v3
    device: auto

  - id: provider.audio.demucs
    type: audio_separation_provider
    source: external_executor
    adapter: demucs
    execution_mode: external
    enabled: false
    command: demucs

workflows:
  - id: workflow.comfyui.portrait_cutout
    provider_id: provider.comfyui.local
    type: asset_processor
    enabled: true
    workflow_path: workflows/comfyui/portrait_cutout.json
    slots:
      input_image: "12.inputs.image"
      output_image: "31.outputs.images[0]"

tools:
  - id: browser.open
    source: builtin
    enabled: true
    requires_confirmation: true
```

Discovery cache example:

```json
{
  "checkedAt": "2026-06-06T00:00:00Z",
  "services": [
    {
      "adapter": "comfyui",
      "endpoint": "http://127.0.0.1:8188",
      "status": "ready",
      "confidence": "high"
    },
    {
      "adapter": "gpt_sovits",
      "endpoint": "http://127.0.0.1:9880",
      "status": "unreachable",
      "confidence": "medium"
    }
  ]
}
```

### 13.2 Character-Pack Capability Config

Character packs can declare preferred providers/workflows without forcing global
installation.

Example:

```json
{
  "voice": {
    "tts_provider": "provider.gpt_sovits.local",
    "gpt_sovits": {
      "gpt_weight": "voice/models/character.ckpt",
      "sovits_weight": "voice/models/character.pth",
      "reference_audio": "voice/reference/neutral.wav",
      "ref_lang": "ja",
      "text_lang": "ja"
    },
    "rvc": {
      "model": "voice/rvc/character.pth",
      "index": "voice/rvc/character.index"
    }
  },
  "workflows": {
    "portrait_cutout": "workflow.comfyui.portrait_cutout"
  }
}
```

Important:

- Relative paths from character packs must go through safe path resolution.
- Character pack config should not make a provider globally ready by itself.
- If required local models are missing, status should be `missing_model`.

### 13.3 Character-Pack Capability Contract

Third-party character packs may request optional abilities. Treat these as a
declarative contract request, not as automatic permission.

Example:

```json
{
  "capability_contract": {
    "schema_version": "akane.capability_contract.v1",
    "requests": [
      {
        "capability_id": "voice.tts.character",
        "reason": "Use this character's packaged GPT-SoVITS voice model.",
        "required": false,
        "risk": "medium"
      },
      {
        "capability_id": "workshop.portrait.generate_candidates",
        "workflow_id": "workflow.comfyui.character_image",
        "reason": "Generate candidate expressions for this character.",
        "required": false,
        "risk": "medium"
      },
      {
        "capability_id": "desktop.open_file",
        "reason": "Open local reference folders when the user asks.",
        "required": false,
        "risk": "high"
      }
    ]
  }
}
```

Load-time behavior:

- low/medium requests can be shown as optional enhancements
- high-risk requests default to denied until the user approves
- dangerous requests require explicit install/enable flow
- a character pack may declare preferences, but it cannot self-grant access
- denied requests must degrade gracefully

Product framing can be playful, but the underlying permission model must stay
strict. For example, UI copy may call this an "ability contract", but the
implementation should still show concrete capabilities, risk, and requested
scope:

```text
新角色请求以下能力：
- 使用本地 GPT-SoVITS 角色声线
- 使用 ComfyUI 立绘生成工作流
- 打开本地文件或文件夹（高风险，默认关闭）

你可以逐项批准。未批准的能力会自动降级。
```

Do not allow character pack text such as "I promise not to upload privacy" to
change risk classification. Risk is computed by Akane's registry, not by the
pack's self-description.

## 14. ComfyUI Integration

### 14.1 Role

ComfyUI should be treated as an external workflow provider.

Akane should not become a node editor.

Primary use cases:

- character portrait cutout
- character image generation
- image cleanup/upscale
- background removal or transparent PNG production
- future expression candidate generation

### 14.2 Workflow Slot Mapping

ComfyUI workflows vary by node IDs and custom nodes. A hardcoded node layout will
break for many users.

Use slot mapping:

```yaml
slots:
  input_image: "12.inputs.image"
  positive_prompt: "6.inputs.text"
  negative_prompt: "7.inputs.text"
  output_image: "31.outputs.images[0]"
```

UI should ask the user to map required slots, not edit all nodes.

Required slots for cutout:

- `input_image`
- `output_image`

Optional slots:

- `mask_output`
- `background_color`
- `padding`
- `alpha_threshold`

Required slots for image generation:

- `positive_prompt`
- `output_image`

Optional slots:

- `negative_prompt`
- `seed`
- `width`
- `height`
- `checkpoint`
- `lora`
- `control_image`
- `reference_image`

### 14.3 Product Flow: Workshop Cutout

```text
User uploads image in workshop
  -> Akane stores it as temporary local input
  -> User clicks "自动抠图"
  -> Registry resolves enabled cutout workflow
  -> Check provider/workflow status
  -> Run ComfyUI workflow
  -> Retrieve output image
  -> Validate PNG/webp/jpeg, prefer transparent PNG
  -> Write through Tauri safe character-pack command
  -> Refresh portrait tab
```

### 14.4 Error Examples

- ComfyUI is not running.
- Workflow file is missing.
- Slot `input_image` does not exist in workflow JSON.
- Custom node missing in user's ComfyUI.
- Output node produced no image.
- Output image is too large or invalid.

Errors should be shown as actionable setup guidance.

## 15. GPT-SoVITS Integration

### 15.1 Role

GPT-SoVITS should be treated as a TTS provider, usually external.

Akane already has `services/tts_client.py` with Edge TTS. A future provider
layer should allow switching between:

- Edge TTS
- GPT-SoVITS API
- custom HTTP TTS API
- null/no TTS

### 15.2 Character Voice

Character packs can include or reference:

- GPT weight
- SoVITS weight
- reference audio
- reference text
- language settings
- speaking speed/volume defaults

Akane should not require every character pack to include voice. Missing voice
should degrade to configured default TTS or no TTS.

### 15.3 Status

Potential statuses:

- `ready`: API reachable and selected weights/reference are valid
- `missing_executor`: GPT-SoVITS API is not running
- `missing_model`: character voice weights missing
- `misconfigured`: API URL invalid or language settings invalid
- `disabled`: TTS disabled

## 16. RVC Integration

### 16.1 Role

RVC is a voice conversion provider, not a normal TTS provider by itself.

Possible flows:

```text
Text -> TTS base voice -> RVC conversion -> character voice output
```

or:

```text
User audio -> RVC conversion -> generated voice output
```

### 16.2 Config

```yaml
providers:
  - id: provider.rvc.local
    type: voice_conversion_provider
    enabled: true
    mode: cli
    command: runtime/rvc/infer.exe

voice_conversion:
  default_provider: provider.rvc.local
  models:
    - id: character.reimu.rvc
      model_path: characters/reimu/voice/rvc/model.pth
      index_path: characters/reimu/voice/rvc/model.index
      transpose: 0
```

### 16.3 Early Recommendation

Do not make RVC part of the first registry implementation. Reserve the schema
and UI section, but implement after provider health checks and TTS provider
switching are stable.

## 17. ASR Integration

### 17.1 Role

ASR provider controls how Akane hears the user.

Potential providers:

- current local/default ASR path if any
- faster-whisper
- external ASR API
- browser/WebView speech recognition if used in a future client

### 17.2 Why This Is Attractive

Users can improve voice input quality by configuring a better model.

This maps directly to product value:

```text
better ASR -> Akane hears more accurately -> voice interaction feels smarter
```

### 17.3 Config

```yaml
providers:
  - id: provider.asr.faster_whisper
    type: asr_provider
    enabled: true
    model: large-v3
    device: auto
    compute_type: auto
```

### 17.4 UI

Voice settings should show:

- active ASR provider
- model
- status
- latency estimate if available
- test recording action
- fallback provider

## 18. Audio Separation / Voice Cleanup

Akane already has docs and backend tools around:

- `separate_audio_stems`
- `clean_voice_track`
- `transcribe_media`
- `prepare_voice_dataset`
- `convert_media_file`

The capability registry should make these visible and configurable.

Provider examples:

- `provider.audio.demucs`
- `provider.audio.deepfilternet`
- `provider.asr.faster_whisper`

Workflow examples:

- `workflow.audio.vocals_instrumental`
- `workflow.audio.voice_cleanup`
- `workflow.audio.voice_dataset_gpt_sovits`
- `workflow.audio.voice_dataset_rvc`

Product value:

- users can prepare voice datasets for character voices
- users can extract clean vocals before RVC/GPT-SoVITS work
- users can use better local models without Akane bundling them

## 19. Browser / Desktop Tools

Opening a browser and browser automation are high-value desktop Agent features.

They should be tools, not providers only.

Potential capabilities:

- `browser.open`
- `browser.search`
- `browser.summarize_page`
- `browser.extract_current_page`
- `desktop.open_file`
- `desktop.reveal_file`
- `desktop.focus_window`

Rules:

- opening arbitrary browser pages requires confirmation unless user explicitly
  clicked a UI action
- browser automation/clicking requires confirmation
- no silent desktop action from backend HTTP
- Tauri desktop actions must stay in the Tauri boundary
- backend can propose an action, but desktop client executes only after
  confirmation

## 20. MCP Integration

MCP should be one of the main extension routes.

### 20.1 Why MCP Before Local Plugins

MCP already defines:

- external server process/service boundary
- tool discovery
- tool schema
- tool invocation protocol

This means Akane can support user-added tools without inventing a plugin system
first.

### 20.2 MCP Provider Shape

```yaml
providers:
  - id: provider.mcp.bilibili
    type: mcp_server
    enabled: true
    command: python
    args:
      - tools/mcp/bilibili_server.py
```

Discovered tools become capability entries:

```json
{
  "id": "mcp.bilibili.search",
  "kind": "mcp_tool",
  "source": "mcp",
  "providerId": "provider.mcp.bilibili",
  "name": "搜索 B 站视频",
  "risk": "low",
  "requiresConfirmation": false,
  "status": "ready"
}
```

### 20.3 Safety

MCP tools still need:

- risk classification
- enable/disable toggles
- per-tool confirmation override
- execution logs
- timeout and concurrency limits
- output size limits

Do not expose every MCP tool to every prompt turn. MCP tools should feed into
the prompt-time capability selector.

## 21. Local Plugin Direction

Local plugins are attractive but should come after MCP/provider foundations.

Reason:

- local plugins are powerful and risky
- Python/JS plugin loading can access files/network/process state
- version compatibility and dependency management can become a project of its
  own

Possible future plugin structure:

```text
plugins/
  my_tool/
    plugin.yaml
    handler.py
```

Example manifest:

```yaml
api_version: 1
id: my_local_tool
name: My Local Tool
enabled: true
capabilities:
  - id: my_local_tool.echo
    kind: tool
    risk: low
    input_schema:
      type: object
      properties:
        text:
          type: string
      required:
        - text
```

Do not implement local plugin execution before:

- confirmation UI exists
- execution logs exist
- config/status catalog exists
- capability filtering is enforced

## 22. UI Surfaces

### 22.1 Control Center: Abilities Page

The existing abilities page can eventually show:

- enabled capability count
- missing configuration count
- high-risk tools count
- provider health rows
- workflow examples
- recent executions

But V1 should avoid fake buttons.

### 22.2 Settings / Control Center: Capability Management

Potential sections:

- Tools
- Providers
- Workflows
- MCP
- Local Models
- Permission Rules
- Execution History

For each capability:

- status
- source
- used by
- risk
- configuration summary
- test action if real
- enable/disable if implemented

### 22.3 Character Workshop

Workshop should consume capability entries instead of hardcoding all future
image/voice options.

Examples:

- If cutout workflow ready: show "自动抠图".
- If cutout workflow missing config: show setup hint.
- If ComfyUI unreachable: show "ComfyUI 未启动".
- If GPT-SoVITS provider ready for character: voice tab can test character
  voice.
- If voice provider missing model: show missing voice model status.

### 22.4 Voice Page

Voice page should eventually expose:

- active TTS provider
- active ASR provider
- character voice provider
- provider status
- model/path summary
- test input/output

Do not overload the page with raw paths by default.

### 22.5 Workspace

Workspace can show capabilities used for generated files:

- transcription provider
- audio separation provider
- cleanup provider
- conversion backend

This makes "why this output exists" and "how to improve quality" visible.

## 23. Runtime Architecture

Suggested backend modules:

```text
companion_v01/
  local_capabilities/
    __init__.py
    models.py
    registry.py
    config_store.py
    health.py
    providers/
      comfyui.py
      gpt_sovits.py
      rvc.py
      asr.py
      audio.py
      mcp.py
    workflows/
      runner.py
      slot_mapping.py
```

Do not create all files in V1 unless needed. This is a target shape.

Suggested API routes:

```text
GET  /capabilities
GET  /capabilities/{id}
POST /capabilities/{id}/health-check
POST /capabilities/{id}/test
GET  /capabilities/providers
GET  /capabilities/workflows
```

For desktop-only operations, route proposals may come from backend, but actual
Tauri execution stays client-side.

## 24. Data Boundary

Public/backend snapshots must not leak:

- API keys
- tokens
- full local paths unless specifically local-only UI
- prompt text
- chat messages
- clipboard contents
- screenshot contents
- model file full paths in prompt/logs

Local settings UI may display paths because it runs on the user's machine, but
snapshot/action contracts should sanitize.

Suggested safe path display:

```text
characters/reimu/voice/model.pth
ComfyUI endpoint: http://127.0.0.1:8188
Model: large-v3
```

Avoid:

```text
C:\Users\...\private\...
api_key=...
```

## 25. Invocation Flow

### 25.1 UI-Initiated Workflow

Example: workshop portrait cutout.

```text
User clicks "自动抠图"
  -> frontend calls action/workflow route or Tauri command
  -> registry checks capability status
  -> backend/Tauri validates input and output target
  -> provider runner executes external workflow
  -> output is imported into correct store
  -> UI refreshes
```

No model/Agent decision needed.

### 25.2 Agent-Initiated Tool

Example: Akane wants to open browser.

```text
LLM proposes browser.open
  -> prompt-time registry verifies tool is selected
  -> execution guard normalizes call
  -> risk policy says confirmation required
  -> desktop client shows confirmation
  -> user approves
  -> Tauri/browser provider executes
  -> result returns to model as followup context
  -> Akane responds naturally
```

### 25.3 External Workflow From Agent

Example: user asks Akane to make a transparent character image.

```text
LLM proposes workshop.portrait.cutout or image.workflow.run
  -> registry checks enabled workflow
  -> if input/target ambiguous, ask user
  -> if high-risk or file write target, confirm
  -> run provider
  -> register output artifact
  -> model receives concise result
```

## 26. Execution Logs

Every capability execution should produce a structured event:

```json
{
  "event": "capability_execution",
  "capabilityId": "workshop.portrait.cutout",
  "providerId": "provider.comfyui.local",
  "workflowId": "workflow.comfyui.portrait_cutout",
  "status": "ok",
  "startedAt": "2026-06-06T00:00:00Z",
  "durationMs": 5312,
  "userInitiated": true,
  "confirmation": "not_required",
  "artifact": {
    "kind": "character_portrait",
    "handle": "normal.png"
  }
}
```

Logs should be visible enough for users to understand what happened, but safe
enough not to expose secrets or arbitrary path details.

## 27. Implementation Roadmap

### Phase 0: Documentation And Inventory

Goal:

- document this design
- inventory current backend tools and local model-related services
- identify which existing tools are configurable providers already

Output:

- this document
- table of existing tools/providers/workflows

### Phase 1: Read-Only Capability Catalog

Goal:

- expose a read-only catalog of existing built-in/backend capabilities
- no new external execution yet
- no fake enable buttons
- include safe local environment check metadata when explicitly requested or
  when entering the capability page

Possible route:

```text
GET /capabilities
POST /capabilities/local-environment-check
```

Catalog includes:

- current backend tools from `tool_runtime.py`
- media/ASR/audio capabilities inferred from installed dependencies/config
- desktop pet Tauri-only capabilities as local-only metadata if appropriate
- detected-but-unbound localhost providers such as ComfyUI/GPT-SoVITS, clearly
  marked as discovery results, not enabled configuration

Acceptance:

- frontend can render "what Akane can do"
- missing/unavailable dependencies have structured status
- no capability execution through the catalog yet
- detected providers are not auto-enabled

### Phase 2: Provider Config Skeleton

Goal:

- define config schema for external providers
- support health check for one or two providers

Recommended first providers:

- ComfyUI endpoint
- GPT-SoVITS endpoint

Acceptance:

- user can configure endpoint
- status shows ready/unreachable/missing_config
- health checks do not block startup
- provider entries include `type`, `source`, `adapter`, and `execution_mode`

### Phase 3: Workflow Skeleton

Goal:

- support workflow entries with slot mapping
- do not build a full workflow editor

Recommended first workflow:

- `workshop.portrait.cutout` via ComfyUI

Acceptance:

- workflow JSON path can be configured
- required slots are validated
- test run can fail with structured reason

### Phase 4: Workshop Integration

Goal:

- character workshop consumes cutout capability

Acceptance:

- "自动抠图" appears only when a real workflow is ready
- missing setup shows clear guidance
- output writes through safe character-pack boundaries
- UI stays responsive during long run

### Phase 5: Voice Provider Layer

Goal:

- TTS/ASR provider abstraction visible in capability catalog
- Edge TTS remains available
- GPT-SoVITS can be configured externally
- provider resolution supports graceful degradation

Acceptance:

- voice page shows active provider/status
- character pack can request preferred voice provider
- missing voice models degrade cleanly
- status records requested provider and active fallback provider when degraded

### Phase 6: MCP Provider

Goal:

- configure one MCP server
- discover MCP tools
- merge them into capability catalog
- do not expose all MCP tools to every prompt turn

Acceptance:

- MCP tool IDs are unique
- schemas are normalized
- risk/confirmation defaults applied
- execution is logged

### Phase 7: Permission And Confirmation UX

Goal:

- high-risk tools ask before executing
- user sees what Akane wants to do and why

Acceptance:

- browser open/control requires confirmation
- arbitrary file read/write requires confirmation or explicit UI initiation
- denied actions return structured followup context

### Phase 8: Local Plugin System

Goal:

- optional local plugin mechanism after provider/MCP foundation is stable

Acceptance:

- plugin manifest validation
- enable/disable
- no silent import failures
- risk and confirmation integrated

## 28. First Slice Recommendation

Do not start with ComfyUI execution.

Recommended next engineering slice:

```text
Capability Catalog V1: read-only catalog + status schema + backend route
```

Why:

- low risk
- useful immediately for control center abilities page
- establishes data shape for providers/workflows/MCP
- does not require model installation
- avoids fake buttons

Candidate implementation:

```text
companion_v01/local_capability_catalog.py
companion_v01/routes/capabilities.py
tests/test_capability_catalog.py
desktop_pet_next/src/control-center/data-adapter.js patch later
```

Initial catalog can include:

- built-in backend tools
- media tools
- memory tools
- workspace tools
- existing TTS client status
- desktop-pet feature flags as consumer metadata

Do not include:

- fake ComfyUI
- fake RVC
- fake GPT-SoVITS
- fake MCP

Those should appear as `missing_config` only when a real config section exists,
or be absent until Phase 2.

## 29. Open Questions

1. Which process owns provider execution?
   - FastAPI backend
   - Tauri desktop process
   - separate worker process

2. How should Tauri-only desktop actions be represented in backend catalog
   without letting backend execute them?

3. Should provider health checks run on startup, on demand, or both?

4. How much local path information can local desktop settings UI show?

5. Should character packs declare preferred workflows, or only provider/model
   preferences?

6. How should long-running workflow progress be streamed?
   - backend SSE
   - polling task id
   - Tauri event
   - workspace task system

7. Which capability should be the first real external executor integration?
   - ComfyUI cutout is visually valuable
   - GPT-SoVITS is character-value heavy
   - ASR is core interaction quality

Decision already made for config path V1:

```text
users_data/<profile_user_id>/capabilities/capabilities.yaml
users_data/_local/capabilities/discovery.json
```

## 30. Non-Goals For V1

V1 should not:

- become a full plugin marketplace
- include a full ComfyUI node editor
- bundle large image/TTS/RVC models
- let backend execute desktop window/browser actions directly
- expose API keys or full local paths in snapshots
- fake provider readiness
- make every discovered tool available to the model every turn
- auto-run high-risk external actions without confirmation

## 31. Carryover Summary

If future context is compacted, keep these points:

- User wants high extensibility for advanced local users.
- The attractive vision is user-added local capabilities: ComfyUI workflows,
  RVC, GPT-SoVITS, ASR models, audio separation, browser tools, and MCP.
- Base classification is strict:
  - `type` says what it is.
  - `source` says where implementation comes from.
  - `adapter` names concrete products such as ComfyUI/GPT-SoVITS/RVC.
  - `executionMode` says external/internal/auto.
- Akane should not bundle many models by default. Let users configure external
  executors and model paths.
- Only model files are usually not enough; most systems need an executor:
  ComfyUI for node workflows, GPT-SoVITS API for voice synthesis, RVC runtime
  for conversion, ASR runtime for transcription.
- A few stable features may later use internal lightweight executors, such as
  ONNX cutout or faster-whisper, but external executor mode should come first.
- Providers can degrade through priority chains, such as GPT-SoVITS + RVC ->
  GPT-SoVITS -> remote/custom TTS -> Edge TTS -> text bubble only.
- Character packs may request abilities through a capability contract, but they
  cannot self-grant permissions. High-risk requests default to denied.
- Local environment check should be safe bounded localhost probing. Detection is
  discovery, not permission or auto-enable.
- Recommended config paths:
  `users_data/<profile_user_id>/capabilities/capabilities.yaml` for explicit
  user config, and `users_data/_local/capabilities/discovery.json` for
  machine-local detection cache.
- The right abstraction is not just `Tool Registry`; it is:

```text
Capability Registry
  -> Tool
  -> Provider
  -> Workflow
  -> Task
```

- Existing `docs/capability_registry_v1.md` handles prompt-time tool selection.
  This document handles local capability/product/runtime registration.
- First implementation slice should be a read-only catalog with structured
  status, not real ComfyUI/MCP execution.
- Permission, confirmation, and logs are part of the feature, not polish.
- Do not copy third-party project code without license review. Referencing MCP
  or external-executor architecture is fine.
