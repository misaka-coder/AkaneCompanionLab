# SDK-first turn game

`example.sdk-turn-game` is a deterministic game that exercises the public
`akane_plugin` contract through a real installed generation worker.

The plugin creates a host-owned Task through `ctx.task.create()` with
`pause_mode="cooperative"`. The Task identity and lifecycle are backed by the
existing `HostJobStore`; the plugin does not create a database, scheduler or
worker pool. Each safe game state is written through `task.checkpoint()` with
the domain `state_version`. `pause_at_boundary()`, `resume()` and `cancel()`
return real host receipts and the plugin checks the Task receipt before every
action.

After a worker or host restart, a caller supplies the durable `task_id` to a
tool and the plugin calls `ctx.task.open(task_id, recover=True)`. This reloads
only the latest checkpoint. The plugin explicitly resumes the host task and
starts a fresh action request; the host never replays an unknown model turn,
domain action, timeline write or completion event.

The game state has its own `state_version`. It is deliberately separate from
the host-signed `Event.version`: the former validates business actions, while
the latter identifies the transient event stream. A stale action returns
`status="stale"`, `side_effect_applied=false`, the latest state and
`next_action="observe_state"` without changing the game.

`game.state_changed` is observed and persisted through the existing event,
Observation and Timeline ports. Nothing in those handlers requests a model
turn. `request_game_turn()` and `resume_game()` explicitly use
`ctx.request_turn()` with the latest observation and a per-game coalesce key.
When the game is won, lost or cancelled, further actions and turn requests are
rejected.

The game demonstrates the pause boundary honestly: a request received while
the task is running is not reported as `paused` until a checkpoint exists and
the plugin confirms the cooperative boundary. A non-cooperative executor must
return `pause_unavailable` instead. Cancellation is also cooperative for a
running task: `cancel()` requests stopping, and the game confirms
`update(status="cancelled")` only after its checkpointed safe boundary. A
worker that disappears during an external side effect is reported as
`recovery_unknown`; recovery reads the latest checkpoint but never replays the
unknown action, model turn, timeline write or completion delivery.

Install this directory with the normal `stage_source` -> `install` workflow.
The plugin imports only `akane_plugin`.
