# Akane Timer plugin

This installable plugin schedules durable one-shot events for the current desktop or QQ
conversation. When an event becomes due, the plugin requests an explicit Agent
turn through the host-owned turn port. The host then restores the conversation,
character and MemCore context and uses the same structured reply, emotion,
TTS and channel presentation path as an ordinary turn.

The plugin stores only its own timer ledger and an opaque conversation
reference issued by the host. It does not reconstruct channel recipients, invoke a
second model loop, parse model JSON or send model text through the fixed-text
notification port.

The model-facing capability supports `create`, `status` and `cancel` on desktop and QQ.
Explicit QQ commands are also available:

```text
/timer create 30 提醒我喝水
/timer status
/timer cancel timer-...
```

Group commands that modify timers require the QQ owner or an administrator.

## Permissions

- `capability.prompt.invoke`: expose timer operations to the model.
- `storage.write`: persist the plugin-owned timer ledger.
- `job.run`: run the supervised due-event watcher.
- `agent.turn.request`: request the ordinary host Agent path when a timer is due.
- `qq.command.register`: register the explicit `/timer` command.

Use Akane's `manage_extension` workflow to test, stage and install this source
directory. A failed candidate does not replace the active generation.
