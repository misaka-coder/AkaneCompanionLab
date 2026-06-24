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

Phase 1 first backend slice is implemented. Phase 1B frontend dashboard wiring
is implemented as a narrow slice: the control center reads `/capabilities` as
optional enhancement data and translates raw tool/provider entries into
user-facing ability modules.
Phase 2 backend provider config skeleton is now implemented for local external
executors: ComfyUI and GPT-SoVITS can be represented as configurable provider
entries with profile-scoped endpoint config and bounded localhost health checks.
Phase 2B adds a lightweight provider configuration entrance in the control
center ability page. It shows local provider status and allows endpoint
save/health-check through real backend routes only. This still does not execute
ComfyUI/GPT-SoVITS workflows.
Phase 3A read-only workflow catalog skeleton is implemented. The first workflow
entry is `workshop.portrait.cutout`, backed by the local ComfyUI provider
contract. It is visible as status guidance only; there is still no workflow JSON
editor, slot binding UI, or execution route.
Phase 3B workflow binding config skeleton is implemented on the backend. It can
save and validate a safe binding reference for `workshop.portrait.cutout`
(`workflowPath` plus required slot mapping), but it still does not read ComfyUI
workflow JSON, validate node IDs, or execute the workflow.
Phase 3C adds a minimal control-center workflow binding entrance. The abilities
page can expand the "透明背景处理" workflow card, edit the safe workflow
reference plus two user-facing slot labels, save through the workflow config
route, and run config-level validation. It still does not add an "auto cutout"
execution button.
Phase 4A adds a read-only character workshop consumer. The portrait management
tab reads `/capabilities/workflows`, summarizes the `workflow.workshop.portrait.cutout`
status as "透明背景处理", and offers a navigation hint back to the control center
ability configuration. It does not execute ComfyUI, parse workflow JSON, process
images, or write generated assets.
Phase 4B adds a backend workflow preflight boundary:
`POST /capabilities/workflows/{workflowId}/preflight`. It checks whether the
known workflow/provider config and safe opaque image handles are present, then
returns structured missing/invalid/not-implemented states. It still does not
start ComfyUI, parse workflow JSON, upload image bytes, or write generated
assets.
Phase 4C adds a low-level ComfyUI HTTP client foundation under
`companion_v01/local_workflow_runners/comfyui.py`. It is loopback-only and
tested with fake sessions. It can speak the public `/upload/image`, `/prompt`,
`/history/{promptId}`, and `/view` routes, but no backend route or workshop UI
calls it yet.
Phase 4D adds a pure in-memory ComfyUI input slot mapping helper. It applies
safe Akane slot values to a copied workflow JSON object using paths like
`12.inputs.image`, and rejects non-input or missing-node paths. It still does
not load workflow files, submit prompts, or write assets.
Phase 4E adds safe ComfyUI history output parsing. The helper extracts
`ComfyUiImageRef` entries from `/history/{promptId}` payloads and drops unsafe
filenames, subfolders, or image types before any future `/view` call.
Phase 4F adds a Tauri-only generated portrait import command:
`import_generated_portrait_image`. It writes generated image bytes into a
character pack through the existing safe character-pack path boundary and
blocking worker. Phase 4I now calls it from the workshop only after a completed
backend workflow output is fetched.
Phase 4G adds backend workflow job skeleton routes:
`POST /capabilities/workflows/{workflowId}/jobs` and
`GET /capabilities/workflow-jobs/{jobId}`. They call preflight and can create a
profile-scoped `queued-but-inert` job record when all config/request checks pass,
when no runner is bound. Phase 4I supersedes the inert-only path in production
by binding a real ComfyUI runner.
Phase 4H adds the runner binding slot and background job state machine. The
capabilities router can accept an injected `workflow_runner` plus the existing
`BackgroundTaskRunner`; only then does preflight become `ready` and jobs move
through `queued -> running -> completed/failed`.
Phase 4I wires the first real execution path for character portrait cutout:
the app binds a `ComfyUiWorkflowRunner`, job routes accept explicit image bytes
from the workshop, the runner loads a profile-scoped safe workflow JSON file,
uploads to loopback ComfyUI, polls history, fetches generated image bytes, and
exposes completed outputs through a profile-scoped byte route. The workshop now
shows "自动抠图" only when the catalog reports the workflow as `ready` and
`executionReady:true`; generated portraits are written back only through the
Tauri `import_generated_portrait_image` safe character-pack boundary.
Phase 6A adds MCP catalog visibility in the control-center abilities page. MCP
servers are rendered as a separate read-only "外部 MCP 工具" status panel with
tool counts, user-facing capability labels, prompt exposure state, and
confirmation/risk hints. MCP providers are intentionally not rendered as
localhost endpoint config rows, and the UI does not expose command paths, cwd,
args, env values, raw tool IDs, or any execution button.
Phase 6B binds a minimal production stdio MCP discoverer. It starts the
configured server without shell interpolation, performs only `initialize` and
`tools/list`, applies a timeout, suppresses stderr from public responses, and
passes discovered tool summaries through the existing sanitizer. It still never
calls MCP tools.
Phase 6C adds the minimal control-center MCP configuration entrance. The
abilities page can expand an MCP server row, save stdio server config through
`abilities.mcp.config.save`, and manually run tool discovery through
`abilities.mcp.discover`. The collapsed dashboard remains compact and safe; the
private command/cwd/args/env values are saved locally but not echoed back.
Phase 6D adds an AnySearch MCP search preset in the control-center abilities
page. The preset fills the stdio proxy command (`npx mcp-remote`) and stores
only the `${ANYSEARCH_API_KEY}` environment-variable placeholder, never the real
key. Actual API keys/tokens are still rejected if pasted into args or env fields,
and public catalog responses continue to hide command args and env values.
Phase 6E binds the first narrow MCP execution path: Akane exposes a built-in
`web_search` tool that can call the configured `anysearch` MCP server for
read-only public search/extract/sub-domain queries. Generic discovered MCP tools
remain catalog-only and `exposedToPrompt:false`; the model sees `web_search`,
not arbitrary MCP tool IDs.
Phase 7A adds the first desktop browser action boundary: Akane exposes
`open_browser` only in desktop-pet mode. The backend does not open the browser
itself; it emits a `browser_open_requested` tool event for a public `http/https`
URL, and the Tauri desktop client validates and opens it through the system
browser. It does not read pages, click, download, upload, or fill forms.
Phase 7E adds the first managed browser boundary: Akane exposes `browser_page`
only in desktop-pet mode. It operates on an Akane-owned visible browser window
and supports only public URL navigation, current-page status, bounded page
state snapshots, page scrolling, and visible element summaries. The primary
observation surface is a visible link/video candidate list plus a Playwright
accessibility snapshot with element refs, filtered toward the current viewport
so scrolling changes what the model sees.
Playwright is optional; when the local
runtime does not have it installed, the tool reports `missing_executor` /
`playwright_not_installed` instead of pretending to read the page. It still does
not control the user's already-open browser tabs, log in, download, upload,
expose JavaScript execution, or read private/local URLs.
Phase 7F adds the first controlled browser action boundary behind the same
`browser_page` tool: `click`, `fill`, and `press` can execute only when the
profile approval policy is `trusted_auto_allow` or a future injected approval
checker accepts the action. The default `ask_each_time` policy returns a
structured `capability_approval_required` event and does not touch the page.
Control actions keep hard selector/text/key validation and still do not support
login, payment, order, delete, publish, upload, download, file picker, or
JavaScript evaluation.
Click actions may target a current visible candidate by 1-based
`candidate_index`; the runner resolves that candidate to a public URL and
navigates directly. This is preferred for dynamic video/card pages where DOM
click behavior or popup handling is unreliable.

Files:

- `companion_v01/local_capability_catalog.py`
  - Builds a read-only capability catalog from existing `engine.tool_handlers`,
    prompt-time capability modules, and lightweight provider status checks.
  - Adds read-only `kind: "workflow"` entries, starting with
    `workshop.portrait.cutout` / `workflow.comfyui.portrait_cutout`.
  - Workflow status is derived from provider config without pretending the
    workflow is executable: missing provider config stays `missing_config`,
    configured or health-ready provider status still becomes `missing_workflow`
    until an actual workflow binding/validation layer exists.
  - Keeps concrete products in `adapter`, not in base `type` or `source`.
  - Exposes profile-scoped config path and machine-local discovery path as safe
    metadata.
- `companion_v01/routes/capabilities.py`
  - `GET /capabilities`
  - `GET /capabilities/approval-policy`
  - `POST /capabilities/approval-policy`
  - `GET /capabilities/approval-requests`
  - `POST /capabilities/approval-requests`
  - `GET /capabilities/approval-requests/{requestId}`
  - `POST /capabilities/approval-requests/{requestId}/decision`
  - `POST /capabilities/local-environment-check`
  - `GET /capabilities/providers`
  - `GET /capabilities/workflows`
  - `POST /capabilities/workflows/{workflowId}/config`
  - `POST /capabilities/workflows/{workflowId}/validate`
  - `POST /capabilities/workflows/{workflowId}/preflight`
  - `POST /capabilities/workflows/{workflowId}/jobs`
  - `GET /capabilities/workflow-jobs/{jobId}`
  - `GET /capabilities/workflow-jobs/{jobId}/outputs/{outputHandle}`
  - `POST /capabilities/providers/{providerId}/config`
  - `POST /capabilities/providers/{providerId}/health-check`
  - The local environment check probes only known localhost services and never
    auto-enables providers.
  - Without an injected runner, workflow job routes remain inert. They return
    `missing_config`, `invalid_request`, `unknown_workflow`, or
    `not-implemented`; the only stored job state is `queued-but-inert`, scoped
    to the resolved `profile_user_id`, with no outputs.
  - With the production ComfyUI runner injected, job routes submit work
    to the existing background task runner on the `workflow` lane and expose
    structured progress/status only. Completed job status exposes safe output
    handles but not image bytes.
  - Output bytes are available only from the profile-scoped output route for
    completed jobs. The response is image bytes with `no-store` and `nosniff`;
    wrong profile, missing output, and not-ready jobs do not leak data.
- `companion_v01/local_workflow_execution.py`
  - Defines the narrow workflow runner request/result boundary.
  - Normalizes runner results and sanitizes public outputs to safe
    `{ handle, kind, contentType }` records.
  - Adds `WorkflowExecutionAsset` for internal byte handoff. Public job status
    never echoes `_outputAssets` or raw image bytes.
  - Accepts bounded image bytes/base64/data URLs only after magic-byte image
    validation.
  - Drops unsafe output handles and suppresses raw runner exceptions or
    path/secret-bearing reason strings.
- `companion_v01/local_capability_config.py`
  - Stores profile-scoped provider endpoint config at
    `users_data/<profile_user_id>/capabilities/capabilities.yaml`.
  - Uses JSON-compatible YAML content for now, avoiding a new YAML dependency.
  - Allows only loopback `http` / `https` provider endpoints, strips path/query
    fragments, rejects credentials, and writes config atomically.
  - Re-normalizes loaded config before exposure so hand-edited or legacy
    endpoint path/query/token fields and unknown sensitive fields are not echoed
    by `/capabilities` or `/capabilities/providers`.
  - Invalid config files return structured `configStatus` / `warnings` and are
    not silently overwritten by save or health-check routes.
  - Health checks are discovery/status only; they never auto-enable providers.
  - Stores workflow binding skeletons in the same profile-scoped config file
    under `workflows`.
  - Workflow paths must be safe relative JSON paths such as
    `workflows/comfyui/portrait_cutout.json`; absolute paths, URL-like strings,
    traversal, credentials, token/query fields, and non-json paths are rejected.
  - Slot mappings accept only known slot names and short symbolic values. Extra
    unknown slots are ignored rather than echoed.
  - Workflow validation is config-level only and returns `executionReady:false`
    until a real runner/binding validator exists.
  - Workflow preflight accepts only safe opaque `inputImageHandle` /
    `outputImageHandle` values. It rejects local paths, URLs, traversal-like
    strings, and obvious secret-bearing values before any future runner can see
    them. The current preflight result remains inert with `canRun:false` and
    `executionReady:false`.
  - `resolve_workflow_config_file_path()` resolves workflow JSON references
    under `users_data/<profile_user_id>/capabilities/` only.
- `companion_v01/local_workflow_runners/comfyui.py`
  - Provides a small loopback-only ComfyUI HTTP adapter.
  - Normalizes endpoints through the same local HTTP policy used by provider
    config.
  - Supports upload, prompt queueing, history reads, and output image reads.
  - Accepts only safe opaque file/subfolder/client/prompt values; local paths,
    URL-like strings, and obvious secret-bearing values are rejected before
    requests are made.
  - Provides `apply_comfyui_input_slots()` for applying slot values to a copied
    workflow JSON object. It currently supports input paths only:
    `node_id.inputs.field` and `node_id.inputs.nested.field`.
  - Provides `extract_comfyui_output_images()` for reading safe image refs from
    ComfyUI history output payloads. It discards unsafe path-like, URL-like, or
    secret-bearing values.
  - Adds `ComfyUiWorkflowRunner` for `workflow.workshop.portrait.cutout`. It
    reads profile-scoped provider/workflow config, requires explicit input
    bytes from the request, safely loads the configured workflow JSON, applies
    configured input/output slots, uploads the source image, queues/polls
    ComfyUI, fetches the first safe output image, and returns an internal
    `WorkflowExecutionAsset`.
  - The runner is reachable only through the capabilities job route after
    preflight/config checks. It does not read character-pack files, local
    absolute paths, prompts, chat messages, clipboard content, or screenshots.
- `desktop_pet_next/src-tauri/src/main.rs`
  - Adds `import_generated_portrait_image`, a blocking-worker Tauri command for
    generated portrait writes.
  - Reuses `sanitize_pack_id`, `sanitize_asset_id`, `safe_child_path`, magic-byte
    image format detection, size limits, and temp-file rename.
  - Requires `overwrite:true` before replacing an existing emotion image.
  - The workshop uses it after fetching a completed backend workflow output;
    the backend itself never writes character-pack files.
- `companion_v01/app.py`
  - Registers the capabilities router with the existing engine background task
    scheduler and `ComfyUiWorkflowRunner(config_base_dir=Path(config.DATA_DIR))`.
- `tests/test_backend_route_modules.py`
  - Verifies read-only catalog shape, existing backend tool exposure, provider
    classification, safe config paths, no obvious sensitive fields, and local
    discovery not being enablement.
  - Verifies provider config save/health-check boundaries for ComfyUI and
    GPT-SoVITS, including no external endpoints, no secret query persistence,
    profile-scoped config, and no auto-enable on health check.
  - Verifies manually edited provider config is sanitized on read and invalid
    config files are reported without silent overwrite.
  - Verifies the workflow catalog remains read-only, exposes safe-handle slot
    names only, and does not mark `workshop.portrait.cutout` ready merely
    because the ComfyUI provider endpoint was saved or health-checked.
  - Verifies workflow binding config rejects unsafe paths and slot mappings,
    persists only safe relative workflow references, and remains
    `executionReady:false` after validation.
  - Verifies workflow preflight rejects unsafe asset handles, reports missing
    config cleanly, and returns `not-implemented` rather than fake success when
    provider/workflow config is present but no runner exists.
  - Verifies workflow job routes are profile-scoped, inert, and do not echo
    image bytes, URL inputs, local paths, tokens, or secrets.
  - Verifies injected-runner jobs can complete, expose safe public outputs, keep
    internal output assets out of job status, and return image bytes only from
    the correct profile-scoped output route.
  - Verifies an injected workflow runner uses the background `workflow` lane,
    updates job status, filters unsafe outputs, and reports structured failure
    when the runner raises.
- `tests/test_local_workflow_runners.py`
  - Verifies the ComfyUI adapter uses normalized loopback endpoints, calls the
    expected public ComfyUI routes, and rejects unsafe path-like values or bad
    prompt ids without making real network calls.
  - Verifies input slot mapping updates a workflow copy without mutating the
    original workflow and rejects non-input, missing-node, or unsafe slot paths.
  - Verifies ComfyUI history output extraction returns only safe image refs and
    rejects ambiguous history payloads without making real network calls.
  - Verifies `ComfyUiWorkflowRunner` loads a safe profile-scoped workflow JSON
    config, uploads request-provided portrait bytes, queues/polls ComfyUI, reads
    the generated output image, and returns an internal output asset.
- `tests/test_desktop_pet_frontend_contract.py`
  - Verifies the generated portrait import command exists, runs on a blocking
    worker, uses safe character-pack paths, and writes through temp-file rename.
- `desktop_pet_next/src/control-center/data-sources.js`
  - Reads `/capabilities` alongside the control-center runtime data.
  - Treats it as optional: missing or invalid catalog data does not block
    snapshot hydration or legacy fallback.
  - Groups raw `tool.*`, `provider.*`, and `prompt_module.*` entries into
    readable dashboard modules such as "音频与语音" and "本地模型与执行器".
  - Converts `kind: "workflow"` entries into short workflow cards such as
    "透明背景处理", with user-facing status text like "未配置" or "待绑定".
    It does not expose workflow ids, slot ids, model paths, or node details in
    the normal ability dashboard.
  - Routes `abilities.workflow.config.save` and
    `abilities.workflow.validate` to
    `/capabilities/workflows/{workflowId}/config` and
    `/capabilities/workflows/{workflowId}/validate`.
  - Mock sources return `not-implemented` for workflow write actions so the UI
    cannot fake a saved binding.
  - Routes `abilities.provider.config.save` and
    `abilities.provider.healthCheck` to
    `/capabilities/providers/{providerId}/config` and
    `/capabilities/providers/{providerId}/health-check`.
  - Mock sources return `not-implemented` for provider write actions so the UI
    cannot fake a saved provider config.
- `desktop_pet_next/src/control-center-lab.js`
  - Renders module status labels from the catalog-derived dashboard model.
  - Renders workflow status badges/details for read-only workflow entries.
  - Adds a compact workflow binding drawer for catalog workflow entries. It
    shows `workflowPath`, input slot, output slot, and enable binding controls.
    The collapsed card remains user-facing and does not expose raw internal slot
    ids.
  - Renders a compact "本地能力环境" section only when configurable provider
    catalog entries exist. The default view shows provider name, purpose, status,
    endpoint summary, and next-step reason; endpoint editing appears only after a
    local expand action.
  - Does not expose raw ids such as `transcribe_media` in the normal ability
    dashboard.
- `desktop_pet_next/src/control-center/action-router.js`
  - Adds provider config actions:
    `abilities.provider.config.open` (client-handled),
    `abilities.provider.config.save` (backend-route), and
    `abilities.provider.healthCheck` (backend-route).
  - Adds workflow binding actions:
    `abilities.workflow.config.open` (client-handled),
    `abilities.workflow.config.save` (backend-route), and
    `abilities.workflow.validate` (backend-route).
- `desktop_pet_next/src/workshop.js` / `workshop.html` / `workshop.css`
  - Adds a compact portrait-tab "透明背景处理" status row.
  - Reads `/capabilities/workflows` with the same backend/profile boundary used
    by the workshop, but a fixed non-chat `desktop` capability session so it
    does not reuse the live desktop-pet or workshop test chat memory scope.
  - Caches the read briefly so high-frequency desktop snapshots do not trigger
    repeated capability hydration.
  - On failure, leaves ordinary portrait management usable and only shows
    "能力状态未同步".
  - The "去配置" button opens the control center/settings window.
  - The "自动抠图" button is hidden until the workflow is `ready` and
    `executionReady:true`. When clicked, it reads the currently previewed
    portrait through Tauri `read_portrait_image`, starts a backend workflow job
    with explicit image bytes, polls job status, fetches the completed output
    bytes, and imports the generated image through
    `import_generated_portrait_image` as a non-overwriting `<emotion>_cutout`
    portrait.
- `desktop_pet_next/src/control-center/action-surface-contract.js`
  - Classifies provider panel open as client-handled and save/health-check as
    bridged backend-route actions.
  - Classifies workflow panel open as client-handled and save/validate as
    bridged backend-route actions.
- `desktop_pet_next/scripts/control-center-runtime-probe.mjs`
  - Verifies optional catalog enrichment, catalog failure degradation, and
    no raw capability id leakage in module text, provider summaries, or
    workflow summaries.
- `desktop_pet_next/scripts/control-center-action-bridge-smoke.mjs`
  - Verifies provider actions use dedicated capabilities routes, not the inert
    `/control-center/actions/{actionId}` endpoint.
  - Verifies workflow binding actions also use dedicated capabilities routes,
    not the inert `/control-center/actions/{actionId}` endpoint.

Verification:

```bash
python -m unittest tests.test_backend_route_modules
python -m unittest tests.test_local_workflow_runners
python -m unittest tests.test_desktop_pet_frontend_contract
python -m py_compile companion_v01/local_capability_config.py companion_v01/local_capability_catalog.py companion_v01/local_workflow_execution.py companion_v01/routes/capabilities.py companion_v01/app.py tests/test_backend_route_modules.py
python -m py_compile companion_v01/local_workflow_runners/__init__.py companion_v01/local_workflow_runners/comfyui.py tests/test_local_workflow_runners.py
cargo check                         # from desktop_pet_next/src-tauri
cd desktop_pet_next && npm run smoke:control-center-actions
cd desktop_pet_next && npm run probe:control-center-runtime
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
  "approvalMode": "trusted_auto_allow",
  "approvalReason": "trusted_runtime_boundary",
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
  "slotMapping": {
    "input_image_handle": "12.inputs.image",
    "output_image_handle": "20.inputs.filename_prefix"
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

Implemented slice:

- Configurable providers:
  - `provider.comfyui.local`
  - `provider.tts.gpt_sovits.local`
- Routes:
  - `GET /capabilities/providers`
  - `POST /capabilities/providers/{providerId}/config`
  - `POST /capabilities/providers/{providerId}/health-check`
- Config rules:
  - profile-scoped file:
     `users_data/<profile_user_id>/capabilities/capabilities.yaml`
  - endpoint must be loopback `http` or `https`
  - path/query/fragment are stripped before persistence
  - loaded config is re-normalized before exposure; old hand-edited endpoint
    paths, query strings, token-like fields, and unknown provider fields are not
    returned to the frontend
  - invalid config files expose structured `configStatus` and `warnings`
    instead of degrading silently
  - save/health-check do not overwrite an invalid config file implicitly
  - URL credentials are rejected
  - health checks are bounded socket probes and never auto-enable providers
- Catalog integration:
  - configurable providers now appear in `GET /capabilities`
  - unconfigured providers show `missing_config`
  - configured-but-not-checked providers show `configured`
  - enabled providers with a saved health check can show `ready` or
    `unreachable`

Frontend configuration entrance:

- Ability page renders a compact "本地能力环境" provider section when
  configurable provider entries are present in `/capabilities`.
- Provider rows show only user-facing information: name, purpose, status,
  endpoint summary, usage area, and a short reason. They do not show workflow
  JSON, node ids, model paths, slot mappings, token values, or local absolute
  paths.
- `abilities.provider.config.open` is client-handled and only expands/collapses
  the local panel.
- `abilities.provider.config.save` and `abilities.provider.healthCheck` are
  real backend-route actions. They go through the action router and data source,
  then call the provider config/health-check routes.
- `abilities.provider.ttsTest` is now a real backend-route action for
  `provider.tts.gpt_sovits.local`. It sends only a localhost endpoint, short
  test text, and optional safe `voiceProfileId` to
  `/capabilities/providers/{providerId}/tts-test`, receives a bounded audio
  sample, and plays it in the control center.
- `abilities.provider.voiceProfile.inspectFolder` is a real backend-route action
  for `provider.tts.gpt_sovits.local`. It sends only a user-entered local model
  folder path to `/capabilities/providers/{providerId}/voice-profiles/inspect-folder`
  and returns suggested form fields from `tts_infer.yaml`, reference audio,
  `.ckpt`, and `.pth` files. It does not persist config, load weights, switch
  the external GPT-SoVITS runtime, or auto-enable the provider.
- `abilities.provider.voiceProfile.save` persists a profile-scoped GPT-SoVITS
  voice profile through
  `/capabilities/providers/{providerId}/voice-profiles/{voiceProfileId}/config`.
  Public catalogs expose only safe summary fields such as reference audio
  basename and prompt text length.
- `abilities.provider.voiceProfile.assignToCurrentCharacter` is a Tauri invoke
  action. It writes only the current character pack `voice.provider` and
  `voice.profile_id` fields through `set_character_voice_profile`, using atomic
  file replacement. It does not write private model paths into the character
  pack and does not change the external GPT-SoVITS runtime weights.
- `abilities.provider.voiceProfile.clearCurrentCharacter` is a Tauri invoke
  action. It clears the current character pack voice preference through
  `clear_character_voice_profile`, using atomic file replacement. It restores
  the default TTS fallback for that character and does not delete saved voice
  profiles, private model paths, or external GPT-SoVITS runtime weights.
- Mock source does not fake provider write success; it returns
  `not-implemented`.

Still not implemented:

- no ComfyUI workflow execution
- no GPT-SoVITS folder picker dialog, model download manager, or runtime weight
  switcher
- no workflow slot mapping

### Phase 3: Workflow Skeleton

Goal:

- support workflow entries with slot mapping
- do not build a full workflow editor

Recommended first workflow:

- `workshop.portrait.cutout` via ComfyUI

Implemented Phase 3A:

- `GET /capabilities` includes a read-only workflow entry:
  `workflow.workshop.portrait.cutout`.
- `GET /capabilities/workflows` returns the same workflow slice without tool or
  provider entries.
- Public fields describe what the workflow is for:
  `capabilityId`, `workflowId`, `providerId`, `target`, `output`, safe slot
  names, `inputSchema.pathPolicy: "safe-handle-only"`, status, and reason.
- The workflow does not expose ComfyUI JSON, node ids, prompt text, local model
  paths, asset paths, cached paths, image content, or token/query fields.
- Status is intentionally conservative:
  - no ComfyUI endpoint -> `missing_config`
  - invalid ComfyUI endpoint -> `invalid_config`
  - disabled ComfyUI provider -> `disabled`
  - unreachable ComfyUI provider -> `unreachable`
  - configured or health-ready ComfyUI provider -> `missing_workflow`
- `missing_workflow` means "provider exists, but no workflow binding/validation
  contract exists yet". It must not be rendered as a runnable "auto cutout"
  feature.
- The control center ability page may show "透明背景处理" as a compact status
  card, but it must not add an execution button until Phase 4 creates the real
  workshop boundary.

Implemented Phase 3B:

- `POST /capabilities/workflows/{workflowId}/config` saves a workflow binding
  skeleton for known workflows only.
- `POST /capabilities/workflows/{workflowId}/validate` verifies provider config,
  workflow path presence, and required symbolic slot mappings.
- The saved config lives in the existing profile-scoped capability config:

```json
{
  "schemaVersion": 1,
  "providers": {},
  "workflows": {
    "workflow.workshop.portrait.cutout": {
      "enabled": true,
      "workflowPath": "workflows/comfyui/portrait_cutout.json",
      "slotMapping": {
        "input_image_handle": "12.inputs.image",
        "output_image_handle": "20.inputs.filename_prefix"
      }
    }
  }
}
```

- The config store deliberately uses a safe relative `workflowPath` reference,
  not a full local path and not the workflow JSON content.
- The workflow JSON must exist under the same profile-scoped capability
  directory, for example:
  `users_data/<profile_user_id>/capabilities/workflows/comfyui/portrait_cutout.json`.
- Required slot mappings must point to existing ComfyUI node input paths in that
  JSON. Use paths such as `12.inputs.image` for the uploaded source image and
  `20.inputs.filename_prefix` for the generated output prefix. Output slots such
  as `31.outputs.images[0]` are intentionally rejected because Akane reads the
  generated image from ComfyUI history after execution.
- `validate` can return `validated_config`, but still reports
  `executionReady:false` with reason `workflow_runtime_not_bound`.
- The control center translates `configured` workflow state into "已绑定" with a
  warning tone and explanatory detail, not a runnable ready state.
- At Phase 3B, there was no ComfyUI prompt submission, workflow JSON parsing,
  node id validation, model path picker, asset write, or workshop "自动抠图"
  button.

Implemented Phase 3C:

- The abilities page can expand the "透明背景处理" workflow card.
- The expanded panel exposes only:
  - `workflowPath`
  - input slot label
  - output slot label
  - enable binding toggle
- Save and validate use real backend-route actions:
  - `abilities.workflow.config.save`
  - `abilities.workflow.validate`
- Mock source returns `not-implemented`, so the UI cannot fake a saved workflow.
- The collapsed workflow card stays simple and user-facing. It may keep the safe
  public catalog workflow id for routing, but it does not display raw ComfyUI
  workflow ids, internal required slot ids, model paths, node ids, or secrets.
- At Phase 3C, this is still configuration only. Phase 4I creates the workshop
  "自动抠图" action through the backend job and Tauri import boundaries.

Implemented Phase 4A:

- The character workshop portrait tab consumes the workflow catalog read-only.
- The visible label is "透明背景处理" instead of raw workflow/capability ids.
- Status examples:
  - `missing_config` -> "需要配置本地 ComfyUI"
  - `missing_workflow` -> "需要绑定抠图工作流"
  - `configured` / `validated_config` -> "已绑定，执行入口待开放"
  - `unreachable` -> "ComfyUI 暂时未连接"
- The read path uses `GET /capabilities/workflows` and includes
  fixed `user_id` / `session_id` values of `desktop`, plus `real_user_id` and
  `client=desktop_pet`.
- The result is cached briefly to avoid repeatedly hydrating capabilities on
  desktop snapshot updates.
- Failure to read the capability catalog does not block creating outfits,
  importing images, previewing expressions, setting default portraits, or
  calibration.
- At Phase 4A, the workshop did not show or run an actual "自动抠图" execution
  button. No image bytes were sent to ComfyUI, no workflow JSON was parsed, and
  no generated portrait asset was written in that phase. Phase 4I supersedes
  this read-only boundary.

Implemented Phase 4B:

- The backend exposes a preflight-only boundary:
  `POST /capabilities/workflows/{workflowId}/preflight`.
- The known first workflow is still `workflow.workshop.portrait.cutout`.
- Preflight checks:
  - provider config exists and is enabled
  - workflow binding exists and is enabled
  - required symbolic slot mapping exists
  - request includes safe opaque `inputImageHandle` and `outputImageHandle`
- Accepted image values are handles, not paths. Local absolute paths, URL-like
  strings, slash/backslash paths, overly long values, and obvious secret-bearing
  strings are rejected with `status:"invalid_request"`.
- When all config and request handles are valid, the current response is still
  `{ ok:false, status:"not-implemented", reason:"workflow_runner_not_bound",
  canRun:false, executionReady:false }`.
- This endpoint is intentionally not wired to the workshop UI yet. It does not
  parse workflow JSON, submit a prompt to ComfyUI, send image bytes, poll a
  queue, or write output assets.

Implemented Phase 4C:

- Added `ComfyUiClient` in `companion_v01/local_workflow_runners/comfyui.py`.
- The client is a protocol adapter only:
  - `upload_image()` -> ComfyUI `/upload/image`
  - `queue_prompt()` -> ComfyUI `/prompt`
  - `get_history()` -> ComfyUI `/history/{promptId}`
  - `get_image()` -> ComfyUI `/view`
- Endpoints are loopback-only through `normalize_local_http_endpoint()`.
- File names, subfolders, client ids, prompt ids, and view targets must be safe
  short opaque values. Paths, URLs, traversal-like values, and secret-bearing
  values are rejected.
- Tests use fake sessions only. No real ComfyUI process is required and no
  network call is made during verification.
- This client is not connected to `/capabilities/workflows/{workflowId}/preflight`
  yet. It does not create jobs, mutate workflow JSON, or write character-pack
  assets.

Implemented Phase 4D:

- Added `apply_comfyui_input_slots()` to the ComfyUI runner module.
- It applies Akane slot values to a workflow JSON copy using safe input paths
  such as `12.inputs.image` or `20.inputs.options.padding`.
- It deliberately rejects output paths such as `31.outputs.images[0]`; output
  image selection must be handled from ComfyUI history in a later slice.
- It rejects missing nodes, missing `inputs`, unsafe slot names, and malformed
  slot paths.
- It does not load workflow JSON from disk, run ComfyUI, poll jobs, or write
  character-pack assets.

Implemented Phase 4E:

- Added `extract_comfyui_output_images()` to the ComfyUI runner module.
- It reads common ComfyUI history structures:
  `history[prompt_id].outputs[node_id].images[]` or a direct object with
  `outputs`.
- It returns safe `ComfyUiImageRef` values only.
- Unsafe filenames, subfolders, URLs, path traversal, token/secret/password/API
  key-looking values, and invalid image types are discarded before any future
  `/view` request.
- Ambiguous multi-prompt history requires an explicit `prompt_id`.
- It does not fetch images, execute workflows, or choose which output should be
  imported into a character pack.

Implemented Phase 4F:

- Added Tauri command `import_generated_portrait_image`.
- Parameters are `packId`, `outfit`, `emotion`, `imageBytes`, optional
  `extension`, optional `mimeType`, and `overwrite`.
- The command runs on `spawn_blocking`, validates pack/outfit/emotion through
  existing sanitizers, detects image format from magic bytes, rejects mismatched
  extension/MIME hints, and limits image size to the existing portrait limit.
- It writes through safe character-pack paths and temp-file rename, then returns
  the refreshed outfit asset list.
- It requires explicit `overwrite:true` before replacing an existing emotion.
- Phase 4I wires this command to `workshop.js` after backend job completion;
  the visible "自动抠图" button is still gated by workflow readiness.

Implemented Phase 4G:

- Added inert workflow job routes:
  - `POST /capabilities/workflows/{workflowId}/jobs`
  - `GET /capabilities/workflow-jobs/{jobId}`
- The start route calls the existing preflight boundary first.
- Missing config, unknown workflow, and invalid handles do not create jobs.
- When preflight reaches `workflow_runner_not_bound`, the route creates a
  profile-scoped `queued-but-inert` job record and returns
  `status:"not-implemented"`, `canRun:false`, and `executionReady:false`.
- Job status reads are scoped to the resolved profile id and do not expose
  internal scope fields.
- Job records do not contain raw image bytes, local paths, URL inputs, tokens,
  secrets, or outputs.
- No background task is submitted yet, and no ComfyUI request is made.

Implemented Phase 4H:

- Added `companion_v01/local_workflow_execution.py`.
- The new boundary defines `WorkflowExecutionRequest`,
  `WorkflowExecutionResult`, and a runner callable/protocol shape.
- Runner results are normalized before they touch public job state:
  unsafe output handles are discarded; public outputs are limited to
  `{ handle, kind, contentType }`; raw exceptions, local paths, URLs, tokens,
  secrets, passwords, and API-key-looking strings are not echoed.
- `build_capabilities_router()` now accepts optional `workflow_runner` and
  `background_tasks` parameters.
- If no runner is injected, behavior remains unchanged:
  preflight returns `workflow_runner_not_bound`, job start creates only
  `queued-but-inert`, and no work executes.
- If a runner is injected and preflight/config/request checks pass:
  preflight returns `ready`, job start submits a background task on the
  `workflow` lane, and status polling reports `queued`, `running`,
  `completed`, or `failed`.
- `companion_v01/app.py` passes the existing `engine.background_tasks` scheduler
  to the router. Phase 4I also passes a concrete ComfyUI runner.
- Tests cover both injected-runner success and injected-runner exception
  failure without exposing unsafe fields.

Implemented Phase 4I:

- Added `ComfyUiWorkflowRunner` as the first real local workflow runner.
- `companion_v01/app.py` binds it to the capabilities router with the existing
  background task scheduler.
- The runner resolves the configured workflow JSON under
  `users_data/<profile_user_id>/capabilities/`, requires request-provided input
  image bytes, applies configured ComfyUI input slots, queues the prompt, polls
  history, fetches the first safe output image, and returns an internal output
  asset.
- `POST /capabilities/workflows/{workflowId}/jobs` accepts
  `inputImageBytes` / `imageBytes` into an internal input asset boundary. Public
  job status still exposes only structured state and safe output handles.
- `/capabilities` and `/capabilities/workflows` mark a configured workflow as
  `ready` only after the profile-scoped workflow JSON exists, parses as an
  object, and all required slot mappings point to existing ComfyUI node
  `inputs` paths. Failure reasons stay as safe short enums such as
  `workflow_file_missing`, `workflow_file_invalid_json`, or
  `slot_mapping_target_missing`.
- Control center abilities UI provides a real "导入 JSON" action for this
  workflow. The frontend reads a user-selected ComfyUI API workflow `.json`
  file, sends only `workflowPath` and `workflowJson` through
  `abilities.workflow.file.import`, and the backend stores it through
  `POST /capabilities/workflows/{workflowId}/file` under the current profile's
  capability directory. Users do not need to create
  `users_data/<profile>/capabilities/...` folders by hand. The import response
  returns only the safe relative `workflowPath`; it does not expose absolute
  paths, execute ComfyUI, or write character assets.
- `GET /capabilities/workflow-jobs/{jobId}/outputs/{outputHandle}` returns
  completed image bytes only for the same resolved profile id. It returns 404
  for wrong profile or unknown output and 409 before completion.
- The workshop now shows "自动抠图" only when
  `workflow.workshop.portrait.cutout` is `ready` and `executionReady:true`.
- The workshop execution path is:
  Tauri `read_portrait_image` -> backend workflow job -> job polling -> output
  byte fetch -> Tauri `import_generated_portrait_image`.
- Generated images are imported as non-overwriting `<emotion>_cutout` portraits.
- The backend never reads character-pack source image files and never writes
  generated character-pack assets; both file boundaries stay Tauri-side.
- Job status, catalog payloads, logs, and snapshots must not expose local
  absolute paths, workflow JSON contents, prompt text, chat messages, image
  bytes, clipboard/screenshot content, tokens, passwords, or API keys.

Acceptance:

- workflow JSON path can be configured and the JSON file can be imported from
  the control center without manual folder placement
- required slots are validated
- test run can fail with structured reason
- configured cutout can run through ComfyUI and write back through Tauri import

### Phase 4: Workshop Integration

Goal:

- character workshop consumes cutout capability

Acceptance:

- "自动抠图" appears only when a real workflow is ready
- missing setup shows clear guidance
- output writes through safe character-pack boundaries
- UI stays responsive during long run

Current Phase 4I boundary:

- The real ComfyUI portrait cutout path is implemented and production-bound.
- "自动抠图" remains hidden unless provider/workflow config and runner binding
  make the workflow `ready` with `executionReady:true`. `ready` requires the
  configured JSON file to exist and required ComfyUI input slot paths to resolve.
- The workshop sends explicit portrait image bytes to the backend job route,
  never local file paths.
- The backend executes ComfyUI through the runner, stores output bytes only in
  internal job state, and exposes completed outputs through the profile-scoped
  output byte route.
- Tauri imports the fetched output into the character pack through safe path
  validation and non-overwriting generated emotion ids.
- Remaining work is usability and validation polish: better setup guidance,
  optional workflow JSON structural validation before marking a row ready, and
  richer progress text for long ComfyUI runs.

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

Implemented Phase 5:

- `/capabilities` now exposes voice provider entries and resolution records.
  Built-in provider entries include `provider.tts.edge`,
  `provider.voice.text_only`, `provider.asr.faster_whisper`, and
  `provider.asr.text_input`; the external configurable provider
  `provider.tts.gpt_sovits.local` remains under the provider config routes.
- `resolutions.voice.tts.character` uses a `first_ready` policy. A character
  pack may request a voice provider through `character.json.voice.provider` and
  `character.json.voice.profile_id`. The response records
  `requestedProviderId`, `activeProviderId`, optional `fallbackProviderId`,
  `voiceProfileId`, `status`, and safe short `reason` values.
- Missing GPT-SoVITS voice profile information degrades to Edge TTS with
  `reason: requested_voice_profile_missing`. If Edge is unavailable, the
  explicit fallback is `provider.voice.text_only`, meaning Akane still replies
  through text bubbles rather than pretending audio synthesis succeeded.
- `resolutions.voice.input.asr` resolves faster-whisper first, then degrades to
  `provider.asr.text_input` when local ASR is missing or unavailable. Text input
  is represented as a fallback provider, not as fake speech recognition.
- `DesktopPetCharacterResourceService.build_character_voice_preference()` reads
  only declarative voice hints from a character pack. It does not read model
  files, grant execution permissions, or expose local absolute paths.
- The control center voice page renders compact provider status rows for TTS
  and ASR. It shows the active provider, whether the route is degraded, and the
  gentle reason, without exposing raw local paths, tokens, prompt text, or model
  internals.
- The `/tts` runtime route now uses the same character voice hints and
  profile-scoped provider config. When a character requests
  `provider.tts.gpt_sovits.local`, has a safe `profile_id`, and the user has
  enabled a loopback GPT-SoVITS endpoint, Akane calls the external
  GPT-SoVITS-compatible `/tts` API and returns its audio media type to the
  desktop client.
- GPT-SoVITS synthesis failure degrades to Edge TTS instead of surfacing a hard
  desktop error when Edge is available. The response includes safe provider
  headers such as `X-Akane-TTS-Provider`, `X-Akane-TTS-Requested-Provider`,
  `X-Akane-TTS-Fallback`, and `X-Akane-TTS-Reason`; logs record provider ids
  and exception type only, not external error text that may contain paths.
- The desktop pet `/tts` request now sends `real_user_id`, `session_id`, and
  `character_pack_id`, so synthesis can resolve the same per-profile capability
  config that the control center edits.
- The control center GPT-SoVITS provider row now has a short test control. It can
  test a typed sentence against the configured/local endpoint, optionally pass a
  safe `voiceProfileId`, and play the returned audio sample locally. This action
  is bounded, returns structured failure states, renders a fallback audio player
  when audio is available, does not save model/profile fields, and does not
  auto-enable the provider.
- The control center GPT-SoVITS provider row now also has a compact voice profile
  form. It saves profile-scoped request fields through
  `/capabilities/providers/{providerId}/voice-profiles/{voiceProfileId}/config`:
  `displayName`, `enabled`, `textLang`, `promptLang`, `mediaType`,
  `refAudioPath`, and `promptText`. Public catalog/list responses expose only
  safe summaries such as `referenceAudioName` and `promptTextLength`; full local
  paths and prompt/reference text stay inside
  `users_data/<profile_user_id>/capabilities/capabilities.yaml`.
- The same provider row has a folder inspect helper. A user can paste a local
  GPT-SoVITS voice model folder such as `F:\models\dania`; the backend scans a
  bounded local file set, reads simple scalar values from `tts_infer.yaml`, and
  suggests `voiceProfileId`, display name, language/media fields, reference audio
  path, and reference text for the form. This is a private action response for
  form filling only: it is not included in public catalogs, does not persist
  anything, and does not load `.ckpt` / `.pth` weights.
- `/capabilities/voice-profiles` lists saved voice profile summaries for the
  current profile. The abilities page reads it as an optional catalog: failure to
  read profile summaries does not block the rest of the control center.
- The `/tts` runtime passes a saved voice profile payload to the external
  GPT-SoVITS-compatible client when a character pack requests
  `provider.tts.gpt_sovits.local` plus a matching safe `profile_id`. This means
  Akane can send reference audio path / reference text / language fields to a
  local wrapper API, while still falling back to Edge TTS if the external call
  fails.

Still intentionally not done:

- Akane does not yet manage GPT-SoVITS downloads, GPT/Sovits weight selection,
  or profile-to-model binding workflows. The folder inspect helper only suggests
  request profile fields for an external local API; it does not load `.pth` /
  `.ckpt` weights by itself. If a downloaded voice model requires selecting
  weights in GPT-SoVITS first, that still happens in the external GPT-SoVITS app
  or a user-provided wrapper.
- RVC chaining is still reserved for a later voice conversion provider phase.
- The voice page does not expose model file pickers or raw advanced
  GPT-SoVITS/RVC parameters.

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

Implemented Phase 6 foundation:

- Profile-scoped MCP server config now lives in
  `users_data/<profile_user_id>/capabilities/capabilities.yaml` under
  `mcpServers`. V1 supports `stdio` servers with private `command`, `args`,
  `cwd`, and safe non-secret `env` values.
- Public MCP server responses expose only safe summaries: `serverId`,
  display name, transport, command file name, args/env counts, discovery status,
  and tool count. Full command paths, cwd, args, env values, API keys, tokens,
  passwords, and secrets must not appear in `/capabilities`,
  `/capabilities/mcp-servers`, snapshots, logs, or prompt context.
- Backend routes:
  - `GET /capabilities/mcp-servers`
  - `POST /capabilities/mcp-servers/{serverId}/config`
  - `POST /capabilities/mcp-servers/{serverId}/discover`
- Discovery is explicit and requires a real `mcp_tool_discoverer` binding. If no
  discoverer is bound, the discover route returns structured
  `not-implemented` with `reason: mcp_discoverer_not_bound`; it does not fake a
  successful connection or invent tools.
- Production now binds a minimal `McpStdioToolDiscoverer` for stdio discovery.
  Discovery performs only MCP `initialize` plus `tools/list`, never
  `tools/call`, uses `create_subprocess_exec` without shell interpolation,
  applies a bounded timeout, and discards stderr from public responses.
- Production also has a minimal `McpStdioToolCaller` for one bounded
  `tools/call` request. It is not exposed as a generic MCP execution surface;
  V1 uses it only behind the built-in `web_search` handler for the configured
  `anysearch` server.
- Discovered MCP tools are sanitized and stored as summaries only. Tool IDs are
  normalized as `mcp.{serverId}.{toolName}` with duplicate tool names made
  unique. Input schemas are reduced to bounded object schemas containing safe
  property names, types, descriptions, and required fields only; defaults,
  examples, and secret-looking fields are discarded.
- `/capabilities` now merges configured/discovered MCP servers into the catalog:
  the server appears as a provider (`provider.mcp.{serverId}`), and discovered
  tools appear as `kind: "mcp_tool"`, `source: "mcp"`, `adapter:
  "mcp_stdio"`, `executionMode: "external"`.
- MCP tools are intentionally marked `exposedToPrompt: false` in this slice.
  Generic discovered MCP tools remain catalog/discovery only. The prompt-time
  execution surface is the built-in `web_search` tool, which maps to a
  conservative AnySearch allowlist instead of exposing raw MCP tool IDs.
- Risk defaults are applied at discovery time. Read-only search/fetch/extract
  style tools can run under a low-friction policy when the user explicitly asks
  for them. Browser click/write/delete/upload/shell style tools are `risk: high`
  and `requiresConfirmation: true`.
- The control-center abilities page renders MCP servers in a dedicated status
  panel. It shows display name, transport, command basename, discovered tool
  count, broad user-facing tool categories, prompt exposure state, and
  risk/confirmation hints. Expanding a row exposes a compact stdio config form
  and a manual discovery button. It does not render raw tool IDs, input schema
  parameter walls, full command paths, cwd, args, env values, or execution
  buttons. Saved private command/cwd/args/env values are intentionally not
  echoed back; modifying an existing server requires re-entering private
  command details.
- The MCP panel offers a compact AnySearch search preset. It pre-fills the
  stdio proxy command and uses `${ANYSEARCH_API_KEY}` as a non-secret
  environment placeholder. Users with an AnySearch key set that variable in the
  backend launch environment or `.env`; users without a key can remove the
  header args and use anonymous AnySearch access with lower limits.
- `CapabilityRegistry` now includes a built-in `web_search` tool in the `web`
  layer for desktop pet, QQ text, and web scene modes. The handler supports
  `search`, `batch_search`, `extract`, and `get_sub_domains`, clamps result and
  text sizes, rejects localhost/private/file URLs for extraction, returns
  structured unavailable followup when AnySearch is not configured, and redacts
  known key/header/local-path material from followup context.

Still intentionally not done:

- Akane does not execute arbitrary discovered MCP tools.
- Discovered MCP tools are not merged into `CapabilityRegistry.select()` and
  are not available to prompt turns as raw `mcp.{serverId}.{toolName}` actions.
- The frontend MCP form is config/discovery only. It does not call tools, expose
  MCP tools to prompt selection, or manage real secret-bearing env values.
- No secret store exists yet. Real API keys and tokens are not accepted in MCP
  args/env fields; only explicit environment-variable placeholders such as
  `${ANYSEARCH_API_KEY}` may be stored.
- Browser control/click/form-fill/download/upload and arbitrary MCP execution
  still require a later confirmation UX and a separate execution policy before
  any `tools/call` support beyond the AnySearch read-only allowlist.

### Phase 7: Permission And Confirmation UX

Goal:

- high-risk tools ask before executing
- user sees what Akane wants to do and why
- low-risk user-requested web search/open actions stay smooth

Acceptance:

- user-requested public web search can run without per-call confirmation
- opening a user-provided URL can run without per-call confirmation
- automatically inferred searches that include private context can ask first
- browser control/click/form-fill/download/upload requires confirmation
- arbitrary file read/write requires confirmation or explicit UI initiation
- denied actions return structured followup context
- user can choose an approval mode per capability/provider, such as
  `ask_each_time`, `trusted_auto_allow`, or `disabled`

Implemented Phase 7A foundation:

- `CapabilityRegistry` includes `open_browser` only for desktop pet mode under
  the `desktop_browser` layer.
- `OpenBrowserToolHandler` accepts only explicit public `http` / `https` URLs,
  rejects localhost, private IPs, `file:` URLs, credential-bearing URLs, and
  whitespace/control characters, and returns a `browser_open_requested` event.
- The backend never opens a browser directly. It only proposes the desktop
  action through `ToolExecutionResult.stream_events`.
- `desktop_pet_next/src/main.js` handles `browser_open_requested` from streamed
  tool events and final `tool_events`, deduplicates per URL, revalidates the
  URL in the client, and invokes Tauri `open_external_url`.
- `desktop_pet_next/src-tauri/src/main.rs` exposes `open_external_url`, performs
  another public URL validation pass, and opens the URL with the OS default
  browser without shell interpolation.
- Browser click/form-fill/download/upload/current-page extraction are still not
  implemented. They remain future higher-risk actions requiring explicit
  confirmation policy and UI.

Implemented Phase 7B foundation:

- Every public capability catalog entry now carries `approvalMode` and
  `approvalReason` in addition to `risk` and `requiresConfirmation`.
- Supported modes are `trusted_auto_allow`, `ask_each_time`, and `disabled`.
- Ready low/medium risk tools such as `web_search` and public `open_browser`
  are marked `trusted_auto_allow`; they still keep their existing validation
  boundaries and do not gain new execution privileges.
- High-risk discovered MCP tools, such as browser click/navigation/download
  tools, are marked `ask_each_time`.
- Not-ready entries, including disabled providers, missing configs, unavailable
  platforms, and unbound workflows, are marked `disabled`.
- The control center preserves these fields from `/capabilities` and displays a
  compact MCP policy label such as "自动允许", "每次确认", or "暂不可用".
- This is metadata only. It does not implement the final approval modal or make
  raw MCP tools available to the prompt.

Implemented Phase 7C foundation:

- `companion_v01/capability_approval.py` provides an in-memory approval request
  queue for the current backend process.
- `/capabilities/approval-requests` lists pending approval requests for the
  current profile. `include_resolved=1` includes approved, denied, and expired
  items for lightweight diagnostics.
- `POST /capabilities/approval-requests` creates a pending request only for
  `ask_each_time` or high-risk/confirmation-required operations. Requests that
  are already `trusted_auto_allow` return `not_required`, and disabled
  capabilities return `disabled`.
- `POST /capabilities/approval-requests/{requestId}/decision` accepts
  `approved` or `denied`. Approved requests receive a short-lived
  `approvalGrant` object for future execution binding.
- Request previews are public-only: API keys, tokens, authorization headers,
  password/secret fields, and local absolute paths are removed or redacted
  before they reach HTTP responses, logs, snapshots, or prompt context.
- The control center reads the approval queue as a degradable runtime source and
  shows pending request counts in the abilities safety panel.
- This still does not execute browser click/form-fill/download/upload or raw
  MCP tools. Those actions require a later binding that consumes approval grants
  and revalidates the actual operation.

Implemented Phase 7D profile policy:

- The profile-scoped capability config stores `approvalPolicy.defaultMode`.
  The default is conservative: `ask_each_time`.
- `GET /capabilities/approval-policy` returns the public policy summary and the
  two supported global modes: `ask_each_time` ("请求批准") and
  `trusted_auto_allow` ("完全访问").
- `POST /capabilities/approval-policy` saves only the selected `defaultMode`.
  Unknown modes return `invalid_config`; arbitrary extra payload fields are not
  persisted.
- `/capabilities` and `/capabilities/workflows` apply the policy when projecting
  public catalog entries. When the policy is `trusted_auto_allow`, high-risk or
  confirmation-required ready entries are reported as
  `approvalMode: "trusted_auto_allow"` with
  `approvalReason: "user_policy_trusted_auto_allow"`.
- Disabled/not-ready entries remain `approvalMode: "disabled"` regardless of
  policy. "完全访问" skips per-call approval only; it does not bypass public URL
  validation, safe-handle path policy, local/private address restrictions,
  secret redaction, config validation, or missing-runner checks.
- The control center abilities safety panel exposes the two-mode policy switch
  through `abilities.approvalPolicy.save`, a dedicated backend-route action for
  `POST /capabilities/approval-policy`.

Implemented Phase 7E managed browser read V1:

- `BrowserPageToolHandler` exposes `browser_page` as a prompt-visible tool only
  for desktop-pet mode through the `desktop_browser` capability layer.
- Tool selection is intentionally split: `web_search` finds candidate public
  pages and extracts public page text without opening a browser window,
  `open_browser` opens a selected URL in the user's system browser without
  reading it, and `browser_page` opens/operates an Akane-managed visible
  browser window for model context. The managed window is not the user's
  arbitrary already-open Edge/Chrome tab. `open_for_user:true` remains a
  compatibility flag for also emitting a separate `browser_open_requested`
  event, but it is normally unnecessary when the intended surface is the
  managed browser window.
- Supported read actions are `navigate`, `read_text`, `current`, `snapshot`,
  `scroll`, and `elements`. `navigate` requires a public `http` / `https` URL.
  `read_text` can either read the current Akane-managed browser window or
  navigate to a supplied public URL first. `snapshot` returns the current page
  state without navigating. `scroll` only scrolls the managed window and returns
  a bounded page-state snapshot after scrolling. `elements` returns a bounded
  visible link/button/input summary for orientation before any future approved
  action.
- Page-state snapshots use Playwright's accessibility snapshot in AI mode with
  element refs such as `[ref=e12]`, filtered by viewport box coordinates when
  available. This follows the browser-agent pattern used by Playwright MCP /
  browser-use style tools: observe the page, pick a ref, then act on that ref.
- Snapshots also prepend a bounded `Visible link/video candidates` list. A
  click action can pass `candidate_index` to open one of those visible public
  links directly, which is the preferred path for Bilibili-style video cards.
- Browser Control V1 adds high-risk actions `click`, `fill`, and `press`.
  `click` requires a `candidate_index`, snapshot ref, or bounded selector;
  `fill` requires either a snapshot ref or a bounded selector plus bounded text;
  `press` accepts a snapshot ref/selector plus only a small key whitelist such
  as Enter, Escape, Tab, arrow keys, PageUp/PageDown, Home, and End.
  Secret-looking selectors/text, password/token fields, and obvious
  destructive/login/payment/upload/download targets are rejected before any
  approval policy is considered.
- By default, high-risk control actions return a `capability_approval_required`
  stream event with safe payload preview and no page side effect. If the
  profile policy is switched to `trusted_auto_allow`, the same actions execute
  through the managed Playwright page while preserving the selector/text/key
  validation. A future approval-grant binding can be injected through
  `approval_checker` without exposing raw MCP browser tools to the prompt.
- The tool rejects localhost, private IPs, `.local` hosts, `file:` paths,
  credential-bearing URLs, whitespace/control-character URLs, and obvious
  secret-bearing query parameters such as `token=` or `api_key=`.
- Execution uses `ManagedBrowserPageRunner`, a small optional Playwright runner
  serialized through a single worker thread. By default it launches a visible
  browser window, using the system Edge channel on Windows and falling back to
  bundled Chromium when the channel is unavailable. Playwright is not a hard
  project dependency; missing runtime support returns structured `unavailable`
  / `playwright_not_installed` followup context.
- Stream events include only action/status/title/URL metadata. Page body text
  appears only in bounded followup context for the model continuation and is
  sanitized for common secret/header/local-path patterns.
- The capability catalog reads `capability_status()` from tool handlers when
  available, so `browser_page` can appear as `missing_executor` until the local
  browser runner is installed.
- `python scripts/probe_browser_page.py` probes the current runtime. Without
  Playwright installed it exits successfully with `missing_executor` metadata;
  add `--require-ready` when a real browser read must be treated as mandatory.
  The probe supports `--action scroll`, `--action elements`, and
  `--candidate-index` for candidate-based click checks; when a URL is supplied
  for current-page actions, it first navigates to the URL and then performs the
  requested action.
- On Windows, `powershell -ExecutionPolicy Bypass -File scripts/install_browser_page_runner.ps1`
  installs the optional Playwright/Chromium runner into the project `.venv` when
  present, then runs the mandatory probe. This is an explicit local setup step;
  the backend does not install browser binaries by itself.
- Full arbitrary browser automation remains out of scope. Upload, download,
  file picker, JavaScript evaluation, login/payment/order/destructive flows, and
  arbitrary MCP browser tools still require a later stronger approval-grant
  execution binding.

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
