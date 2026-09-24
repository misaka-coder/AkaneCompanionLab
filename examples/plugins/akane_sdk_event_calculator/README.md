# Public event calculator

Install with `../akane_sdk_event_source`. The subscriber receives
`example.numbers-ready`, calls its ordinary `add` tool through host admission
and the program result path, then publishes `example.sum-ready`. It returns the
child receipt, so the original delivery remains pending until that child ends.

The handler has no implicit conversation access. It needs no memory parameters,
Agent request, direct host imports, or special followup setting on `add`.
`event.subscribe` is contributed by `@plugin.on`; the other two permissions are
declared explicitly. The source allowlist prevents unrelated plugins or host
events from triggering this subscriber by claiming a source in their data.

This project and its peer contain the business logic. The SDK declares the
ordinary tools/subscriptions; the installed Akane runtime owns scheduling,
scope, cancellation, validation, and per-subscriber results.
