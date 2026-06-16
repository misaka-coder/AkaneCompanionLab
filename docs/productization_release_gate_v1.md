# Productization Release Gate V1

Updated: 2026-06-16

This document is the product gate before AkaneCompanionLab is published as a
public Alpha. The goal is **not** to remove advanced features. The goal is to
turn already-built features into product-shaped surfaces: understandable,
configurable, diagnosable, and honestly documented.

## Product Principle

A feature is publishable only when a user can answer five questions without
reading the source code:

1. What is this feature for?
2. Where do I configure it?
3. How do I know it is working?
4. What do I do when it fails?
5. Is it core, optional, or experimental?

If any answer is unclear, the feature may remain in the repository, but it must
not be advertised as a finished capability.

## Lessons From AstrBot

AstrBot is worth studying because it does not present every integration as one
flat settings form. Its product surface is split into clear operational pages:

| Pattern | What AstrBot Does | Akane Product Lesson |
| --- | --- | --- |
| Install channels | Docker, uv, launcher, panel deployment, manual source install are described as separate paths | Akane must distinguish source Alpha, Web fallback, Windows desktop source-build, and future installer |
| Provider page | Provider types are separated: chat, agent runner, STT, TTS, embedding, rerank | Akane should not bury model, TTS, ASR, and local workflows in one vague "abilities" blob |
| Provider templates | Add-provider dialog lists templates by provider type and then renders structured config | GPT-SoVITS should have a guided provider/profile flow, not only raw endpoint fields |
| MCP page | MCP has its own tab, server cards, enable/edit/delete, config templates, connection test, tool list, sync entry | Akane MCP needs a real management surface before it is marketed as MCP support |
| Plugin marketplace | Installed plugins, market, config, changelog, enable/disable, conflicts, and logs are visible | Akane should expose capability status, conflicts, and diagnostics instead of only internal routing |
| Console and trace | Operational failures are visible in a product UI | Akane needs user-facing error/status surfaces for optional integrations |

The target is not to copy AstrBot. Akane is a companion/desktop-pet system, not
a bot platform. The useful part is the product discipline around optional
integrations.

## Public Claim Rules

| Claim Type | Allowed Wording | Not Allowed |
| --- | --- | --- |
| Core chat | "Local/Web/desktop companion chat with visible model configuration" | "No-setup AI companion" |
| Platform | "Windows-first desktop pet; backend/Web are source-runnable elsewhere" | "Cross-platform desktop app" |
| GPT-SoVITS | "External GPT-SoVITS-compatible TTS provider integration, productization in progress" until the gate passes | "Built-in GPT-SoVITS voice cloning" |
| MCP | "Experimental MCP server configuration/discovery" until the gate passes | "Full MCP platform" |
| Music | "Local drag-and-drop music playback and Windows system-music awareness are being stabilized" until the gate passes | "Complete music companion" |
| QQ | "Optional NapCat/OneBot adapter" | "Works with QQ out of the box" |

## Release Gate Table

Status meanings:

- **Ready**: can be advertised in README front matter.
- **Alpha**: can be documented, but must include setup limits and known gaps.
- **Productization Gap**: code exists, but the user experience is not yet clear
  enough to advertise as a product feature.
- **Experimental**: internal or power-user only.

| Capability | Current Status | Why | Product Gate Before Public Promotion |
| --- | --- | --- | --- |
| Model configuration | Ready | Visible provider page exists for OpenAI-compatible, OpenAI, DeepSeek, Gemini, Anthropic, Ollama; API keys are redacted; tests cover config read/write | Keep in first-run path; verify save/test/reload in exported snapshot |
| Core chat loop | Ready | Backend, Web, desktop pet, sessions, final output, and memory retrieval are real paths | Clean public snapshot smoke: first message succeeds or returns a precise model-config error |
| Memory system | Alpha | This is a real advantage: vector/semantic memory and time-based raw transcript lookup exist | Add a user-facing memory status/browser story: what is stored, where, how to inspect, how to disable/clear |
| Character packs | Alpha | Character package protocol, resources, persona files, and local storage are in place | Ship one redistributable starter pack or a first-run import path; validate import/export without private assets |
| Character knowledge folders | Alpha | Custom folders and on-demand knowledge loading are designed; infrastructure exists | Add one polished sample pack showing relationships/events/custom folder prompts and how the model discovers files |
| Windows Tauri desktop pet | Alpha | It launches, has control center, character resources, TTS, ASR, sensing, music, and workspace paths | Clean-account smoke with source Alpha: launch, open model config, choose character, send message, close/reopen without stale exe |
| Web client | Alpha | Runs from backend, useful fallback | Ensure first-run model settings and character pack limitations are visible; avoid desktop-only controls looking usable |
| QQ / NapCat | Productization Gap | Adapter exists and self-checks exist, but user setup depends on external NapCat | Add a QQ setup checklist page: required external service, ports, health check, send/receive test, common failure reasons |
| GPT-SoVITS TTS | Productization Gap | Backend routes, health check, short audio test, profile inspect/save, character assignment, and `/tts` fallback exist | Make one guided flow: endpoint -> health check -> folder/profile inspect -> short test -> assign to current character -> reply uses profile; document exact supported external APIs |
| MCP | Productization Gap | Config save/discovery exists; UI shows external MCP tools; built-in AnySearch preset is present | Add clear MCP manager: add custom server, stdio/http templates, test connection, discover tools, enable/disable/delete, tool list, exposure/approval state, failure logs |
| Local tools / workflows | Productization Gap | Workflow binding, import, validate, and job routes exist; ComfyUI portrait cutout is partially wired | One workflow must have an end-to-end guide and smoke: configure provider, import workflow JSON, validate, run from workshop, view result |
| Music: local drag-and-drop | Productization Gap | Queue, playback controls, lyrics, and prompt context exist | Stabilize queue/playback bugs; add state-machine tests for local file queue, pause/resume/next, lyrics absence, and clear queue |
| Music: system media awareness | Productization Gap | Windows SMTC awareness and lyric lookup surfaces exist | Document Windows-only boundary; add diagnostics for unsupported apps, missing lyrics, stale position, and disabled provider |
| Desktop sensing / screen vision | Alpha | Toggles and snapshot paths exist; privacy boundaries are documented | Add visible privacy/status explanation and one smoke path for "enabled but unavailable" |
| GPT/Comfy/local dependency profiles | Productization Gap | Many optional packages still sit near core dependencies | Split core vs extras or at least document import-safe degradation; core-only startup must be tested |
| Control center UI | Alpha | Unified settings page exists; old settings entry removed; action classification tests exist | Reduce ambiguity: every visible button must be Ready, client-handled, or disabled with reason; no high-value feature hidden behind unclear labels |
| Public export | Ready | History-free export, placeholder assets, audit, and asset licensing are implemented | Keep audit requiring this productization gate document and stale-launch/user-data-root checks |

## P0 Productization Work

These are the next engineering slices before public release.

### 1. Capability Inventory Page

Create a user-facing "能力状态" view that mirrors the gate:

- Core chat/model/memory/character
- Desktop pet
- QQ
- GPT-SoVITS
- MCP
- Music
- Local workflows

Each row should show: status, configured/not configured, last check result,
setup button, docs link, and whether it is core/optional/experimental.

### 2. MCP Manager

MCP should graduate from "configuration fields" to "server management":

- Add server button
- Templates: stdio, streamable HTTP, SSE
- Connection test
- Discover tools
- Tool list with counts
- Enable/disable/delete/edit
- Safe summary that does not expose full command secrets
- Explicit prompt exposure state: "not exposed", "available to selected tool",
  or "requires approval"

### 3. GPT-SoVITS Guided Flow

The GPT-SoVITS product flow should be one visible path:

1. Configure external endpoint.
2. Health check.
3. Inspect model/profile folder.
4. Save voice profile.
5. Run short audio test.
6. Assign profile to current character.
7. Send a desktop-pet reply and confirm the provider/fallback status.

The UI must state that Akane does not bundle GPT-SoVITS and does not load model
weights by itself unless the external service supports it.

### 4. Music Stabilization

Before advertising "一起听歌", write down and test the actual state machine:

- no track
- local file loaded
- playing
- paused
- ended -> next
- clear queue
- system media present
- system media stale
- lyrics found/not found

The prompt context should never claim playback succeeded when only a search page
was opened.

### 5. Platform Wording

The README and UI should use one consistent sentence:

> AkaneCompanionLab is Windows-first for the desktop pet. The backend and Web
> client can be run from source on other systems, but Linux/macOS desktop pet
> support is not promised yet.

## Product Acceptance Checklist

A feature is promoted from Productization Gap to Alpha only when all are true:

- It has a visible configuration entry or a documented CLI setup.
- It has a "test/check" operation.
- It has a status label that distinguishes ready, missing config, unavailable,
  unsupported, and failed.
- It has at least one focused backend/frontend test or smoke script.
- Its README/docs section says what external services are required.
- It fails closed and returns structured reasons.

A feature is promoted from Alpha to Ready only when all are true:

- It works in the exported public snapshot.
- It does not require private files or undeclared assets.
- It has clean first-run behavior.
- It has a troubleshooting path understandable without reading source code.
- It is covered by the public release audit or an equivalent CI check.

## Immediate Decision

Do not publish the current public Alpha as the main announcement yet. Use it as
an audited source snapshot for internal smoke testing. The next public-facing
milestone is a productized Alpha where GPT-SoVITS, MCP, and music either pass
their gates or are clearly marked as experimental with a visible path to finish
them.
