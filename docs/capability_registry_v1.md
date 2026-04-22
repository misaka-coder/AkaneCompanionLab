# Akane Capability Registry V1

## 1. Goal

`Capability Registry` is a lightweight layer between `client_mode` and concrete tool prompts.

It should keep Akane aware that she has broad abilities, while only exposing the full tool manuals that are useful in the current scene.

Core outcome:

- Empty workspace: Akane sees short ability hints, not every detailed tool schema.
- User sends a document: document tools become fully visible.
- User sends audio/video: media tools become fully visible.
- User clears the workspace: detailed tool prompts disappear again and fall back to short ability hints.

This is not a heavy plugin/skill system yet. V1 is a small registry and rule-based selector.

## 2. Two-Layer Prompt Model

### 2.1 Light Capability Hints

Light hints are short and can stay visible by client mode.

Example for QQ:

```text
你具备临时附件、文档整理、媒体处理、生成文件管理等能力。
当聊天里出现对应附件或生成物时，系统会把更具体的工具说明放到你手边。
```

Purpose:

- Preserve Akane's capability awareness.
- Avoid making her think she cannot process files/media just because no file is currently present.
- Keep idle-chat prompts clean.

### 2.2 Heavy Tool Instructions

Heavy instructions are the current `ToolHandler.build_prompt_instruction()` outputs.

They are only injected when the current runtime state needs them.

Example:

- QQ/Desktop always: inject `fetch_media_from_url` so Akane can pull public media links into the temporary workspace.
- Has text/PDF/Office attachment: inject `read_attachment_section`, `compose_file`, `revise_generated_file`, `apply_style_to_existing_file`.
- Has audio/video attachment: inject `inspect_media_info`, `separate_audio_stems`, `clean_voice_track`, `transcribe_media`, `prepare_voice_dataset`, `convert_media_file`.
- Has attachment file: inject `send_file` with the attachment workspace tools.
- Has generated file: inject `inspect_generated_file`, `send_file`, `manage_generated_file`.
- Has pending gift/artifact context: inject gift/artifact management tools.

## 3. Capability Modules

V1 modules:

| Module | Light Hint | Full Tools | Trigger |
| --- | --- | --- | --- |
| `base` | reminders and persona cards | `set_reminder`, `list_reminders`, `cancel_reminder`, `manage_persona` | always when tool actions are enabled |
| `remote_media_fetch` | can download public audio/video links into temporary workspace | `fetch_media_from_url` | QQ/Desktop always |
| `attachment_workspace` | can receive, send, and organize temporary attachments | `sync_attachment_workspace`, `inspect_attachment`, `retry_attachment`, `clear_attachment_focus`, `send_file` | QQ/Desktop with any active attachment, pending/ready/failed |
| `conversation_file_authoring` | can generate a file from current conversation even without attachments | `compose_file` | QQ/Desktop always |
| `document_workbench` | can read, summarize, convert, style documents | `read_attachment_section`, `compose_file`, `revise_generated_file`, `apply_style_to_existing_file` | active document/text attachment, or generated document/table file |
| `media_workbench` | can inspect and polish audio/video | `inspect_media_info`, `separate_audio_stems`, `clean_voice_track`, `transcribe_media`, `prepare_voice_dataset`, `convert_media_file` | active audio/video attachment, or generated media file |
| `generated_file_management` | can inspect, resend, archive, delete generated outputs | `inspect_generated_file`, `send_file`, `manage_generated_file` | generated file exists in current session |
| `web_scene_world` | can manage gifts, artifacts, and inventory | `call_npc`, `check_inventory`, `manage_gift`, `manage_artifact` | Web scene modes |

Future modules:

- `desktop_environment`: desktop observation, active window, selected file, shortcuts.
- `external_query`: weather/search/map/news. This should use intent routing, not only state routing.
- `video_workbench`: extract frames, clip video, make GIF, subtitles.

## 4. Trigger Semantics

### 4.1 State-Triggered Capabilities

These are the safest V1 target because the system can inspect runtime state without guessing user intent.

Examples:

- Attachment Inbox contains ready/pending/failed items.
- Attachment detail says `file_kind=pdf/docx/xlsx/txt`.
- Attachment detail contains `media_info`.
- GeneratedFileStore has ready/failed generated files.
- Gift system has pending assets.

### 4.2 Mode-Triggered Capabilities

These remain simple.

Examples:

- `scene_static` / `scene_live2d` can see world-scene tools.
- `qq_text` can see QQ attachment/file/media capability hints.
- `desktop_pet` can see desktop capability hints and, later, desktop tools.

### 4.3 Intent-Triggered Capabilities

Some tools cannot be detected from current state.

Examples:

- Weather.
- Web search.
- Map lookup.
- Real-time news.
- Shopping/product lookup.

V1 should not mix these into the resource-triggered registry. Later we can add a tiny intent index such as:

```text
如果用户明确要求查询实时信息、天气、网页、新闻、价格或地图，可请求外部查询能力。
```

Then a future `CapabilityRouter` can expand the full tool instructions.

## 5. Runtime Flow

```text
client_mode + client_capabilities
        |
        v
runtime state snapshot
  - active attachments
  - generated files
  - pending gifts
  - current client mode
        |
        v
CapabilityRegistry.select()
        |
        +--> light hints
        |
        +--> heavy tool names
        |
        v
engine builds tool prompt
  - render light hints first
  - render only selected full tool instructions
  - execute only selected full tools
```

Important rule:

Tools hidden from the prompt should also be hidden from execution normalization. If Akane hallucinates a hidden tool call, it must be ignored.

## 6. Initial Implementation Plan

### 6.1 Add Runtime Snapshot Helpers

Small helper methods in `AkaneMemoryEngine` or a new `capability_registry.py`:

```python
snapshot = {
    "has_any_attachment": bool(...),
    "has_document_attachment": bool(...),
    "has_media_attachment": bool(...),
    "has_generated_file": bool(...),
    "client_mode": client_context.effective_mode,
}
```

Attachment classification can reuse stored `kind`, `file_ext`, `detail.file_kind`, and `detail.media_info`.

Generated file classification can use `output_format`.

### 6.2 Introduce Capability Definitions

Suggested new module:

```text
companion_v01/capability_registry.py
```

Data shape:

```python
@dataclass(frozen=True)
class CapabilityModule:
    name: str
    modes: tuple[ClientMode, ...]
    tools: tuple[str, ...]
    light_hint: str
    trigger: Callable[[CapabilitySnapshot], bool]
```

Keep V1 plain and readable. No dynamic Python imports, no plugin loading.

### 6.3 Replace Pack-Only Selection

Current path:

```text
MODE_TOOL_PACKS -> TOOL_PACKS -> selected tool names
```

V1 path:

```text
CapabilityRegistry -> selected module names -> selected tool names + light hints
```

`base` still always selected.

### 6.4 Prompt Rendering

`_build_tool_prompt_context()` should render:

```text
【可用能力概览】
...light hints...

【当前可调用工具】
...full selected tool instructions...

如果不需要工具，tool_call 输出 null。一次只调用一个工具。
```

If a module is not fully triggered, its light hint may still appear if the current mode should keep capability awareness.

### 6.5 Execution Guard

`_normalize_tool_call()` should use the same selected tool set as prompt rendering.

This preserves the safety invariant:

> If a tool was not selected for this turn, the model cannot execute it even if it emits the name.

## 7. V1 Trigger Rules

### QQ Text

Always light hints:

- base
- attachment workspace
- document workbench
- media workbench
- generated file management

Full tools:

- `base`: always.
- `attachment_workspace`: when active attachments exist.
- `conversation_file_authoring`: always, because users can ask Akane to write/export a file from the current conversation without uploading anything.
- `document_workbench`: when active text/document attachment exists, or generated document/table file exists.
- `media_workbench`: when active audio/video/media attachment exists, or generated media file exists.
- `generated_file_management`: when generated file exists.

### Web Scene

Always light hints:

- base
- web scene world

Full tools:

- `base`: always.
- `web_scene_world`: always in `scene_static` / `scene_live2d` for now.

This intentionally preserves "empty-hand" world interactions, such as virtual/story gifts created through dialogue rather than uploaded files.

Later, gift/artifact tools can become state-triggered if their prompt cost grows.

### Desktop Pet

For now, mirror QQ attachment/file/media logic plus desktop light hints.

Full desktop permission tools should stay unimplemented until the desktop client exists.

## 8. Non-Goals

V1 should not:

- Build a marketplace-like skill system.
- Add external web/weather/search routing.
- Rewrite each `ToolHandler`.
- Change tool execution semantics.
- Split existing powerful tools into many tiny tools.
- Remove existing mode safety.

## 9. Test Plan

Required tests:

- QQ with no attachment: full document/media tools are not rendered, but light hints mention file/media abilities.
- QQ with no attachment: `compose_file` still renders so Akane can generate a file from the current conversation.
- QQ with text attachment: document tools render.
- QQ with media attachment: media tools render.
- QQ with generated file: generated management tools render.
- Clearing attachments removes full attachment/media/document instructions next turn.
- Web scene still renders world tools.
- Hidden tool call is ignored by `_normalize_tool_call`.

## 10. Why This Matters

This gives Akane a more human-feeling ability model.

She does not carry every manual in her hands all the time. She knows what she is capable of, and when the right material appears, the detailed tools arrive naturally.

That matches the project's larger design principle:

> Let Akane have real agency inside clear, bounded, state-aware systems.
