# Akane plugin examples

This directory is exposed to the execution host as `alias:akane-sdk`. Treat it
as the current release's read-only reference: inspect or copy an example into
the selected project, and make changes in that project rather than here. The
public contract is demonstrated by these current-release examples. Read the
nearest implementation when a registrar method or result shape matters; do
not search physical release directories or rely on remembered APIs.

These packages use the same wheel and `akane.plugins.v1` entry-point contract
as installed Akane plugins. They are executable examples rather than host-only
fixtures.

For new function plugins, start with the current SDK release (**0.14.0**) and the
single public import `akane_plugin`. Install the matching SDK and CapCore 0.1.3
release wheels in an ordinary virtual environment; the SDK does not require the
Akane source tree. `alias:akane-sdk` remains a discovery convenience, not the SDK
distribution. `scripts/verify_plugin_sdk_docs.py` keeps this file, the SDK README
and the plugin-development Skill aligned with the packaged version.

- `akane_sdk_math`: a complete typed function plugin and standalone tests.
- `akane_sdk_change_check`: sync/background SHA-256 comparison with a per-result
  followup choice; unchanged content creates no extra model reply.
- `akane_sdk_catalog`: an actual HTTP JSON query without a file-output requirement.
- `akane_sdk_report`: query → existing document writer → final CSV through
  `ctx.tools.call` / `call_result`; internal calls create no model consumer.
- `akane_sdk_event_source` and `akane_sdk_event_calculator`: two independent
  projects using public `emit` / `on` / receipt inspection. Events run a pure
  calculation and publish its result without requesting a model or writing chat.
  See the SDK README for scope binding, cancellation and transient retention.
- `akane_sdk_event_state`: one SDK-only state event project covering host-signed
  event identity, business `state_version`, observations, timeline persistence,
  explicit stale-checked turns and host-managed background checkpoints.
- `akane_sdk_turn_game`: an SDK-only deterministic turn game covering a
  host-owned Task, state-version-checked actions, stale results, pause/resume,
  cancellation, event idempotency and explicit Agent turns.
- `akane_sdk_board`: current board observation, explicit model decisions through
  the normal session queue, cancellation and linked event receipts. It uses no
  private host imports or history parameters.

- `akane_poke_streak`: an SDK-first plugin that subscribes to the public
  `conversation.poke.inbound` event and updates one current observation when the
  same actor pokes Akane repeatedly. It registers no model tool, requests no model
  turn, writes no timeline record, and sends no message by itself.
- `akane_hacker_news_report`: reads a fixed public Hacker News feed through one
  CapCore capability and returns structured facts plus a host-managed Markdown
  report. It has no arbitrary URL, credential, or local-path input.
- `akane_gentle_checkin`: an SDK-first mixed plugin that combines conversation
  observation, scoped configuration, a declared Skill, a supervised background
  service and a QQ command. It requests at most one normal Agent turn per quiet
  period and does not pretend to need network access for local plugin state.
- `akane_timer`: an SDK-first plugin that persists one-shot events for the
  current conversation and, when due, requests a normal Agent turn through the
  host queue instead of creating a second model or delivery pipeline.

Each example documents its permissions, runtime effect, and installation path
in its own README.

## Public results

SDK function tools may return JSON directly; advanced adapters return a
`CapabilityResult` with public JSON in `content`: an object, array,
string, number, boolean or null. Data is not capped at 64 rows/4096 characters;
`token_count`, `/help` and paths needed for the user's task remain intact.
Non-finite numbers, cycles, non-string object keys and non-JSON objects return
specific errors. The existing v1 reserved metadata keys still require their
typed payloads.

With CapCore 0.1.3, descriptors may declare a complete Draft 2020-12
`input_schema` (leave `inputs=()`) and an `output_schema` for the returned JSON
value. Nested properties, conditions, unions and references to embedded schema
definitions are preserved and validated. Invalid declarations fail activation;
`stage_source`/`stage_wheel` return `schema_errors` with the declaration field.
Legacy slots still work, including `raw={"schema": object_schema}` for a complex
slot; known malformed constraints are no longer silently ignored.

`Result(value=data, followup="none")` releases the result's implicit model
consumer while preserving its data and normal delivery. Full schemas work with
all followup modes; the host does not inject a `finish_turn` argument. See
`docs/plugin_followup_migration_v2.md` for the V1 compatibility boundary.

The successful Host result has `has_value=True` and the validated canonical JSON
in `value`, including scalar, array and null values. `content` remains the v1
presentation envelope. Adding `result_experience` or `managed_artifacts` changes
presentation only; it does not change `value`. An adapter may explicitly provide
`CapabilityResult(value=data, content=presentation)` when those differ. Output
schema failures report `plugin_result_schema_mismatch`, `execution_status=completed`
and `retryable=false`; fix the producer, and do not automatically repeat effects.

`format`/`default` are annotations under the default JSON Schema vocabulary,
not extra validation or default injection. Schemas are self-contained: references
do not fetch remote URLs or read local files. Required arguments are required by
presence; use `minLength` and `type` to control empty strings and null.

Connection snapshots are private. Do not copy credentials or host storage
details into public results. The host tracks literal private values obtained
through connection ports; this is not a sandbox for malicious plugin code.

Programmatic results retain their complete value. Model previews longer than
the deployment's `AKANE_PLUGIN_RESULT_PREVIEW_CHARS` budget (default 16000)
reference a real JSON material in the current conversation's file store.
`inspect_generated_file` reads it without re-executing the original capability.
The material is not automatically sent. Storage failure is reported as an
incomplete projection and does not pretend the original action failed to run.

Model-driven source projects should keep ordinary `unittest` tests using these
real imports. With the SDK installed, run tests normally in the project's own
environment. `manage_extension(test_source)` also supplies the current release
SDK for the managed development path; do not copy or fake SDK modules inside a
plugin project. Then use `stage_source` and `install` for runtime validation.

## Owned subprocesses

The current release also provides the standard-library-only public helper
`akane_plugin.PluginProcessRunner`. Create one per adapter,
call `await runner.run(argv, capture=False, timeout=1800)` for an owned direct
child, and delegate adapter shutdown to `await runner.aclose()`. The result is
`(returncode, stdout_bytes)`; map nonzero exits and `asyncio.TimeoutError` to
domain-specific structured failures, and let `CancelledError` propagate.
Creation, repeated cancellation, timeout and close drain actual child exit;
Windows children are hidden. This is not a process-tree manager, permission
gate or job queue: do not launch unowned descendants, and keep long work on
the existing Host Job path. Do not copy the runner into each plugin.

## 模型工具的常驻与按需

示例继续通过现有 SDK 声明工具用途、输入输出、风险和可见性。用户在控制中心“能力与权限 → 工具常驻与按需”按工具选择；同一插件可以混用两种方式。

常驻工具按原生名字调用；按需工具按精确 ID `capability_load` 后，以返回的 `contract_ref` 经 `capability_invoke` 调用。加载不扩大原生 schema，不需要先搜索。插件 generation 更新后旧引用会失效，应重新加载。纯提示词、Skill、程序服务贡献不需要增加虚构工具。

参见 [SDK 说明](../../akane_plugin/README.md)与[压缩边界及迁移记录](../../docs/tool_exposure_memcore_implementation_20260910.md)。
