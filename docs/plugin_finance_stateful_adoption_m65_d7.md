# Private Finance Stateful Adoption M65-D7

Status: private installed artifact adoption implemented and source-blind accepted.

## Outcome

The private `akane.finance` artifact is the first real consumer of Akane's
restart-only stateful plugin contracts. Public Akane still contains no finance
subscription schema, finance polling configuration, finance command handler,
or finance notification implementation in its composition root. The unused
public `FinanceSubscriptionService` and direct-QQ `QQFinanceDeliveryAdapter`,
together with their authority tests, were deleted in this cutover.

The activated artifact now contributes:

- one plugin-owned SQLite schema below the host-provided instance/plugin data
  directory;
- five bounded exact-match QQ commands;
- one cooperative public-news polling job;
- durable seen-event and delivery-outbox state;
- proactive QQ text intents with stable idempotency keys.

This is one Akane instance plus an optional private plugin. It is not a second
bot project, finance branch, or concrete QQ gateway dependency.

## User-Visible Commands

```text
/财经订阅 <证券或关键词>
/财经取消 <证券或关键词|全部>
/财经订阅列表
/财经推送状态
/财经推送帮助
```

The sender QQ number owns every subscription. Private-message users manage
their own recipient directly. A command issued in a group may target that
group only when NapCat identifies the sender as group owner or administrator;
missing/ordinary-member roles fail closed. Another administrator still cannot
list or cancel the original sender's subscriptions. Invalid identity,
recipient, query, ambiguous security name, authorization, and storage failures
return bounded actionable text rather than falling through to an LLM turn.

## Delivery Semantics

The job does not replay the current public-news page as new notifications. The
first poll after the first active subscription establishes a durable baseline.
Later unseen matching items are written to the plugin outbox before any channel
call.

```text
public news poll
  -> validate bounded source items
  -> compare plugin-owned seen-event state
  -> match active owner-scoped subscriptions
  -> commit outbox rows and seen events atomically
  -> send host-owned NotificationIntent
  -> mark delivered or schedule bounded retry
```

Successful notification keys are deduplicated by both the durable private
outbox and the host's bounded process-lifetime ledger. Failed deliveries reuse
the same key and can retry; cancellation removes undelivered claims for the
disabled subscription. Removing the last active subscription resets the news
baseline so re-enabling later does not replay an unattended backlog.

Notification wording identifies the subscription, source time, source name,
and original link. It explicitly says the source needs verification, does not
prove price causality, and is not investment advice. No model-generated claim
is inserted into this deterministic delivery slice.

## Lifecycle and Isolation

- Import and zero-argument factory creation do not touch the network,
  filesystem, environment, or database.
- Registration occurs only inside `PluginHost.start()` after artifact audit.
- The database path comes only from `PluginRegistrar.get_storage_dir()`.
- The job waits briefly after activation, honours host shutdown, and closes its
  private HTTP client exactly once.
- The plugin receives only `NotificationPort` and QQ command request contracts;
  it never receives Engine, `NapCatQQGateway`, Care, or a host database path.
- Disabled, missing, or failed artifacts publish no finance commands, job, or
  storage mutations and do not block Akane core startup.

## Real Acceptance

Private unit coverage verifies owner isolation, deduplicated subscriptions,
baseline reset, safe validation, durable retry, cooperative shutdown, and
manifest registration.

The source-blind artifact smoke builds a wheel, installs it without the private
source tree, and then uses the real public host to:

1. audit and activate the installed artifact under the stateful contribution
   policy;
2. bind host-managed artifact, plugin storage, and notification ports;
3. dispatch `/财经订阅 日经225` through the immutable QQ command broker;
4. establish a first-poll baseline;
5. observe one later matching public-news item;
6. persist the plugin database and send one proactive group notification;
7. keep all six existing on-demand capability and managed-file paths working;
8. stop the job and adapters through the real host lifecycle.

## Migration Window Closure

M65-D8 completed the required file-by-file ownership probe and deleted the
runtime-disconnected public quote/news event, LLM analysis, moderation,
governance, shared finance store, and public-provider prototypes. The private
M65-D7 path remains the only active subscription/public-news-push authority.

The standalone EmQuant bridge keeps only its local SDK process and now owns its
validation contract directly. EmQuant plugin secrets/configuration, arbitrary
plugin routes, hot lifecycle, cloud multi-instance deployment, and trading
actions remain out of scope. See
`docs/plugin_finance_public_retirement_m65_d8.md`.
