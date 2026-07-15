# Plugin Stateful Runtime Foundation M65-D6

Status: public host contracts implemented; private finance state migration not yet started.

## Boundary

M65-D6 opens the minimum restart-only surfaces needed by the next private
finance consumer without restoring any finance implementation in public Akane:

- one host-owned storage directory per instance and plugin;
- at most one supervised background job per plugin;
- one plugin-scoped proactive notification port;
- bounded exact-match QQ slash-command registrations.

These are trusted in-process plugin contracts. They do not sandbox Python,
expose a QQ gateway, permit runtime registration, mount plugin routes, or let a
plugin select an arbitrary host path.

## Lifecycle and Failure Semantics

All contributions are staged during `PluginHost.start()`. Storage resolution,
registration, job creation, and command publication happen only after artifact
audit and manifest policy acceptance. A failed plugin contributes no adapter,
capability, job, or command.

Jobs start after capability activation. An exception, cancellation, or clean
exit before host shutdown is a runtime failure: the host becomes `degraded` and
diagnostics expose only `job_failed`, `job_cancelled`, or `job_exited`. On
shutdown the host rejects new capability, command, and notification work,
signals jobs, invokes bounded `stop()`, then closes adapters in reverse
activation order.

## Storage Ownership

`storage.write` grants a plugin only this resolved directory:

```text
<AKANE_DATA_ROOT>/instances/<instance_id>/plugins/<plugin_id>/
```

Akane owns path validation and directory creation. The private plugin owns its
schema, transactions, migrations, backup compatibility, and recovery behavior
inside that directory. M65-D6 does not open or migrate the retired public
finance database.

## Notification Semantics

`notification.send` provides a plugin-scoped wrapper around the host channel
port. QQ text currently accepts only `group:<numeric-id>` and
`user:<numeric-id>`, with bounded text and structured delivery results.

Every intent requires an idempotency key. Successful keys are deduplicated per
plugin in a bounded recent process-lifetime history, including concurrent
calls; failed deliveries may retry with the same key. Durable finance
deduplication remains the responsibility of the private delivery/outbox schema.
The private plugin must authorize a subscription owner before constructing a
recipient; the trusted host port is not an authorization database.

## QQ Command Semantics

`qq.command.register` contributes bounded exact-match slash commands. Duplicate
commands within a plugin or across active plugins fail activation instead of
silently choosing a winner. Built-in Akane commands keep precedence.

The broker passes sender QQ number, group identity, arguments, and a stable
source-event idempotency key. The private handler owns subscription authorization
and domain validation. Timeout, exception, invalid result, and host shutdown
produce safe structured reasons and a user-visible retry message; traceback and
raw exception text never enter QQ.

## Contribution Policy

The production composition root uses `trusted.stateful-plugin.v1`. It preserves
the existing low-risk prompt-visible network-read descriptor requirements while
allowing explicitly declared optional permissions:

- `storage.write`
- `job.run`
- `notification.send`
- `artifact.write`
- `qq.command.register`

The policy is permission- and descriptor-based, not finance-plugin-id-based.
Enabled plugin failure still degrades only the plugin host and does not block
Akane core startup.

## Explicitly Not Implemented Here

- private subscription/event/outbox schema or migration;
- finance polling, analysis, retry, or notification content;
- EmQuant configuration, secrets, or external process lifecycle;
- runtime plugin install, hot enablement, arbitrary routes, or code sandboxing;
- cloud deployment or multi-instance hosting.

## Validation

```powershell
python -m unittest tests.test_plugin_storage tests.test_plugin_jobs
python -m unittest tests.test_plugin_notifications tests.test_plugin_qq_commands
python -m unittest tests.test_plugin_host tests.test_plugin_engine_bridge
python -m unittest tests.test_qq_voice_delivery tests.test_backend_route_modules
python -m ruff check companion_v01 tests
git diff --check
```

The next slice is private-plugin adoption: create the finance-owned subscription
schema in the scoped directory, expose authorized QQ commands, then attach one
cooperative supervised job and idempotent notification flow. EmQuant remains a
later, separate contract.
