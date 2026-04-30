# Client Mode Architecture

This document is the current extension map for Akane's client modes. It records how Web scene, QQ text, desktop pet, and future clients should share the same core without mixing prompts, tools, or renderer assumptions.

The short version:

```text
ClientMode chooses the body.
ModeProfile chooses runtime capabilities.
PromptProfile chooses prompt blocks and context modules.
CapabilityRegistry chooses visible tools.
OutputAdapter normalizes what the client actually receives.
MemoryStore, Persona, Reminder, Attachment, GeneratedFile, Gift, and Artifact stay shared core services.
```

## Current Modes

### `scene_static`

Role: Web scene and small-world experience.

It can use scene resources, BGM, character outfit resources, gifts, artifacts, NPC calls, and Web-style visual state. It is allowed to reason about `scene.major`, `scene.minor`, `scene.background`, `scene.bgm`, and `character.outfit`.

Prompt blocks:

- `SCENE_STATIC_SYSTEM_BLOCKS`
- includes `scene_visual_resources`

Tool layers:

- `common`
- `web_scene`

### `scene_live2d`

Role: reserved Live2D scene mode.

It currently degrades to `scene_static` because the renderer profile is not implemented yet. Keep its prompt/tool design close to Web scene until a real Live2D output adapter and renderer exist.

### `qq_text`

Role: text chat, attachments, files, media processing, and bot delivery.

It should not inherit Web scene rendering rules. It does not render `character`, `scene`, `background`, BGM, desktop activity, or pet playback `activity`.

Prompt blocks:

- `QQ_TEXT_SYSTEM_BLOCKS`
- includes `qq_text_mode`
- excludes `scene_visual_resources`
- excludes `desktop_pet_visual`

Tool layers:

- `common`
- `shared_attachment_workspace`
- `shared_document`
- `shared_media`
- `shared_file_authoring`
- `qq_delivery`

QQ-only delivery belongs in `qq_delivery`, for example sticker sending or platform-specific file sending.

### `desktop_pet`

Role: desktop companion, local workspace, music/audio control, screen/desktop context, and later local-file workflows.

It should not inherit QQ delivery logic or Web scene rules. Desktop pet can reuse shared media/document tools, but the local desktop behavior belongs in desktop layers.

Prompt blocks:

- `DESKTOP_PET_SYSTEM_BLOCKS`
- includes `desktop_pet_visual`
- includes `desktop_pet_activity`
- excludes `scene_visual_resources`
- excludes `qq_text_mode`

Tool layers:

- `common`
- `shared_attachment_workspace`
- `shared_document`
- `shared_media`
- `shared_file_authoring`
- `desktop_workspace`
- reserved `desktop_environment`

Desktop pet can reuse media tools such as `transcribe_media`, `convert_media_file`, and `clean_voice_track` when a file is already in the workspace. Future automatic local file discovery should be added through `desktop_workspace` or `desktop_environment`, not through QQ delivery.

## Code Map

### Client Mode And Capabilities

File: `companion_v01/client_protocol.py`

Owns:

- `ClientMode`
- `ClientCapability`
- default capability lists
- `ClientProtocolContext`

Add a new client mode here first.

### Mode Resolution

File: `companion_v01/mode_profiles.py`

Owns:

- `ModeProfile`
- `ModeProfileRegistry`
- capability-based degradation

This decides the effective mode. Example: a requested mode can degrade if it is not implemented or lacks required capabilities.

### Prompt Rules

Files:

- `companion_v01/prompt_blocks.py`
- `companion_v01/prompt_profiles.py`

`prompt_blocks.py` owns reusable rule blocks such as:

- common JSON/schema blocks
- Web scene visual rules
- QQ text rules
- desktop pet visual and activity rules

`prompt_profiles.py` owns:

- which context modules are injected
- which system block ids are mounted
- each mode's output schema prompt

Important rule:

```text
Do not put platform-specific behavior into common prompt blocks.
```

Common blocks can say how JSON, tool calls, memory tags, bubbles, code snippets, choices, time awareness, and persona state work. Client-specific blocks say what the current client can actually render or execute.

### Tool Visibility

File: `companion_v01/capability_registry.py`

Owns:

- tool layer constants
- `CapabilityModule`
- `CapabilitySelection`
- `CapabilityRegistry.select()`
- `CapabilityRegistry.tool_names_for_mode()`

This is the only place that should decide whether a client can see a tool family. Prompt rendering and execution normalization both use this registry, so a hidden tool should also be impossible to execute.

Important rule:

```text
Reuse tools through shared layers.
Do not reuse another platform's delivery layer.
```

For example, desktop pet can reuse `shared_media`, but should not inherit `qq_delivery`.

### Output Normalization

Files:

- `companion_v01/final_output_engine.py`
- `companion_v01/output_adapters.py`

`final_output_engine.py` handles common fallback, speech normalization, tool call normalization, persona request normalization, visual defaults, and desktop `activity`.

`output_adapters.py` trims or annotates the output for a client. QQ removes scene and character rendering fields. Desktop pet uses `DesktopPetOutputAdapter` to keep `character.outfit`, `emotion`, speech fields, tools, persona, and `activity`, while removing Web-only `scene`, `live2d`, and `pet` renderer fields from the response payload.

Important rule:

```text
The model output shape can vary by client.
The backend runtime state stays normalized.
```

## Adding A New Client

For a future `telegram_text`, `wechat_service_account`, or `terminal_cli`, follow this path:

1. Add a value to `ClientMode`.
2. Add default capabilities in `client_protocol.py`.
3. Register a `ModeProfile`.
4. Decide whether the mode is implemented or should degrade.
5. Add or reuse prompt blocks.
6. Register a `PromptProfile`.
7. Add tool layers in `CapabilityRegistry`.
8. Add an `OutputAdapter` if the client needs field trimming.
9. Write the thin platform adapter, such as webhook, polling loop, or CLI loop.
10. Add boundary tests.

The core engine should not need platform-specific branches for normal client additions. Platform adapters should translate external events into Akane's internal request shape, then translate Akane's normalized response back to the platform.

## Boundary Rules

### Prompt Boundaries

- Web scene prompt rules can mention scene, background, BGM, gift, artifact, and resource projection.
- QQ text prompt rules can mention text chat and platform delivery, but not Web scene rendering or desktop activity.
- Desktop pet prompt rules can mention local character pack resources, desktop activity, and playback `activity`, but not QQ stickers or Web scene planning.
- Common prompt blocks must stay platform neutral.

### Tool Boundaries

- `common` can be shared by all clients.
- `shared_document` and `shared_media` can be shared by QQ and desktop pet.
- `qq_delivery` is QQ-only.
- `desktop_workspace` is desktop-only.
- `web_scene` is Web scene-only.
- Future filesystem powers should start as desktop-only and low-permission.

### Memory Boundaries

- `profile_user_id` decides ownership.
- `session_id` decides the current conversation/workspace.
- Client mode can influence context and tools, but should not split Akane into separate personalities.
- Desktop observation, screenshots, window titles, and local paths should be treated as temporary context by default.
- Long-term memory should only receive user-confirmed or conversation-absorbed facts.

### File And Media Boundaries

- QQ attachments and desktop files should enter the same attachment/generated-file core when possible.
- Shared media tools should operate on workspace handles such as `audio_001`, `file_001`, or `gen_001`.
- Automatic local file discovery must be added behind explicit user permission or a trusted workspace boundary.
- Do not add full-disk scanning as a hidden capability.

## Review Checklist

Before merging a new client or a new tool family:

- Does `ClientMode` have a clear value?
- Does `ModeProfileRegistry` know whether it is implemented or degraded?
- Does `PromptProfileRegistry` mount only the prompt blocks this client needs?
- Does `CapabilityRegistry` expose tools through the right layer?
- Is a hidden tool also blocked by `_normalize_tool_call()`?
- Does `OutputAdapterRegistry` trim fields that the client should not see?
- Are there tests proving QQ/Web/Desktop do not inherit each other's special behavior?
- Did we avoid hardcoding a character name or platform name in common prompt text?

## Current Direction

The current product direction is:

```text
Prompt blocks by client
-> Tool capabilities by client
-> Documentation and guard tests
-> Desktop workspace/file/media entry points
-> Desktop pet workbench UI
-> Optional new clients such as CLI, Telegram, or WeChat
```

The main idea is not to make every client identical. It is to let the same Akane core use different bodies cleanly.
