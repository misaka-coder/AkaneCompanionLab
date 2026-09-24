# SDK-first state events

This independent project demonstrates the public `akane_plugin` contract for a
stateful event source. The module imports only `akane_plugin`; it does not know
about the Akane host, worker protocol, storage, channels or model runtime.

## Contract

- `example.sdk-event-state.observe` stores the latest state as a volatile
  observation and returns the host-assigned observation version.
- `example.sdk-event-state.bind` creates the conversation-scoped event binding.
- `example.sdk-event-state.publish` makes one structured event after a real
  `ctx.tools.call` to the plugin's canonicalizer. The event data carries the
  business `state_version`; the host supplies `event_id`, `source`, `scope`,
  stream `version`, `occurred_at_ms` and `received_at_ms`.
- The bound event is handled independently for observation and timeline
  persistence. A separate `decide` tool requests a normal host Agent turn, so
  the plugin never sends a message or starts a second model loop.
- `example.sdk-event-state.decide` requests a normal turn with
  `stale="reject"`, so an old observation version is rejected at the real
  decision boundary instead of silently using newer state.
- `checkpoint` is a `long_task` capability. Pause, resume and stop remain host
  Job operations; the plugin only computes its result.

Install this directory with the normal `test_source` -> `stage_source` ->
`install` workflow. A source test should show the declared event subscriptions,
the `long_task` descriptor and the `agent.turn.request` permission before the
artifact is activated.
