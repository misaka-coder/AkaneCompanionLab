# Retired host tool surfaces

The following legacy host tools are no longer part of Akane's model-visible or user-visible capability surface:

- `set_reminder`
- `list_reminders`
- `cancel_reminder`
- `manage_task_workspace`
- `delegate_task`
- `call_npc`
- `check_inventory`
- `manage_gift`
- `manage_artifact`
- `manage_persona`
- `focus_workspace`
- `sync_attachment_workspace`

They were removed because the reminder path only persisted rows without a reliable cross-channel wake-and-deliver loop, while the delegated worker used a separate limited agent loop that duplicated the main agent and could not provide the same capabilities or continuation semantics.

Removal is end to end: capability specs, handler assembly, prompts, HTTP routes, desktop workspace cards, task polling and web reminder notifications no longer advertise or invoke them. The main agent loop, project workspaces, file tools, MemCore tool traces and the shared `BackgroundTaskRunner` used by real internal services remain unchanged.

The second group belonged to an early web-world and persistent-focus design. Its public schemas no longer matched the executors, and the two focus tools claimed durable model context that the normal response path did not actually project. Their replacements are existing, narrower capabilities: character-pack context libraries, `inspect_attachment`, `load_material`, `read_attachment_section`, `clear_attachment_focus`, `list_workspace`, `read_workspace`, `register_workspace_items`, and the plugin/MCP extension surface. `send_sticker` remains supported with one canonical required `sticker` id.

Legacy reminder, task-workspace, gift, artifact, persona-card and focus records remain readable for migration compatibility. They are not projected to the model or control center. A future replacement may migrate these records, but must first provide one working end-to-end capability contract.
