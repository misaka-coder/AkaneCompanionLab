# Public SDK batch composition

This standalone project uses `akane-plugin >=0.8.4,<0.9` without host imports.
Build its wheel or pass the project to the normal `stage_source` → `install`
workflow, approving the returned `capability.invoke` permission.

Call `example.batch.batch` with `{"text":"hello","count":40,"parallel":false}`.
Each item calls `chunk`, which calls `measure`: 40 items reserve 80 dependency
attempts. `measure` returns the actual UTF-8 byte length. The result includes
before/after `ctx.tools.budget()` snapshots, requested/completed counts and outcomes.
`parallel=true` runs groups of up to eight and stops scheduling groups on failure.
The sequential form stops on its first failure. Already completed work is retained
in a `batch_incomplete` error; a failed target does not make the whole batch unstarted.

Set the deployment's `AKANE_EXECUTION_RESOURCE_POLICY` to
`{"max_dependency_calls":6}` and restart the Bot: five items cannot complete.
Use `{"max_dependency_calls":100}` to complete 40 sequential items. This is startup
configuration; the example cannot change host permissions or its budget.
An optional `peer` plugin id routes `chunk` calls to another installed copy. The
integration test installs two independent artifacts and exercises real worker RPC,
54 KiB text input, shared nested fanout limits and fresh top-level budgets.

This example measures text; it does not provide resumable jobs or durable checkpoints.
