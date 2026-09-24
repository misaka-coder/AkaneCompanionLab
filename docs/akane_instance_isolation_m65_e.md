# Akane Instance Isolation M65-E

Status: E1 through E5 implemented; M65-E complete.

## Fixed deployment decisions

M65-E uses process isolation, not shared-process multi-tenancy:

```text
one Akane instance
= one process or container
+ one exclusive AKANE_DATA_ROOT
+ one listening port
+ one deployment environment and secret set
+ one QQ/NapCat channel profile
+ one log destination
```

- One QQ Bot account may bind to only one Akane instance. Sharing would require
  a separate upstream event router and is not part of M65-E.
- `instance.toml` supports only `channels.qq.enabled` and
  `channels.qq.profile_ref`. Tokens, passwords, and absolute paths are rejected;
  deployment secrets remain in the process environment.
- The first release copies character packs into each instance root. Runtime
  instances do not share a writable character directory. A future read-only
  asset mount must still keep `_local`, memory, Care, and state instance-owned.
- The server supports multiple isolated instances, while one desktop client is
  bound to one instance at a time. E4 must detect the backend instance id and
  namespace client state before backend switching is accepted.
- Public daily quota remains process-local and resets on restart. Persistence is
  a separate operations feature and does not block isolation acceptance.
- Public `/health` exposes only `status`, `instance_id`, and `root_binding`.
  Channel profile references, local paths, credentials, and process details are
  not public health data.

## E1 implemented boundary

`companion_v01.instance_runtime` is now the single authority for binding an
instance id to a physical root:

- a named instance requires `AKANE_DATA_ROOT` in the process environment;
- its manifest is validated before root-owned saved settings or databases load;
- `<data_root>/instance-binding.json` permanently records only schema version
  and instance id;
- `<data_root>/run/instance.lock` uses an operating-system exclusive lock;
- a second process cannot open the same root;
- a root cannot be reassigned to a different instance id;
- failures are structured and never include the absolute root;
- the lease remains held through application shutdown and is released only at
  process exit, so legacy handles cannot overlap a replacement process;
- automatic legacy copying remains available only for `local-default`.

The E1 negative tests cover missing explicit roots, immutable QQ channel
references, secret-field rejection, same-process and cross-process lock
conflicts, permanent binding conflicts, safe health projection, and idempotent
release.

## E2 implemented boundary

`InstanceRuntimeLayout` now owns the writable roots used by one process:

```text
<data_root>/users_data
<data_root>/characters
<data_root>/state
<data_root>/logs
<data_root>/workspace
<data_root>/cache
<data_root>/run
```

- Named instances default to `<data_root>/workspace`; a configured workspace or
  Memcore path is accepted only when its resolved path remains inside the bound
  root. `local-default` keeps its legacy desktop-workspace compatibility.
- Engine, routes, adapters, workflow runners, plugin storage, QQ state, model
  settings, and prompt audit receive paths from the same immutable layout.
- Prompt audit records include only the safe instance id and structural prompt
  metrics; they do not contain prompt text, credentials, or absolute paths.
- Markdown memory mirrors live below the instance Engine memory directory.
  Character-pack `_local/memory` is no longer written or cleared by runtime.
- Embedding reindex has a stop signal and is joined before Chroma closes.
  Background lanes, vision jobs, screen-vision jobs, Memcore, and Chroma have
  explicit close paths. QQ image follow-up tasks are supervised and cancelled
  during FastAPI shutdown.
- `<data_root>/migration-incomplete.json` blocks startup. A failed offline
  migration cannot be mistaken for a complete instance root.

### Offline migration

The migration command is deliberately one-way and offline:

```powershell
python scripts/migrate_akane_instance.py `
  --source-root C:\AkaneLegacyData `
  --target-root D:\AkaneInstances\finance-prod `
  --instance-id finance-prod `
  --character-pack-id akane_v1
```

If the old workspace used the legacy desktop location, pass it explicitly with
`--source-workspace`. The target must not already exist. The tool validates the
source SQLite files before copying and validates the target copies afterwards.
It copies the main database, Memcore, Care, QQ state, workspace layers, plugin
state, character content, user assets, and NPC memory. It does not copy Chroma,
character `_local`, locks, caches, logs, or saved model-service secrets. The
source is never modified. A mid-copy failure leaves a safe incomplete marker;
rollback means continuing to use the unchanged source root.

## E3 implemented boundary

`companion_v01.deployment_security` now binds the instance manifest to one
immutable deployment security snapshot before `AkaneMemoryEngine` is
constructed:

- `local-default` preserves the legacy `QQ_BRIDGE_ENABLED` and loopback-only
  management behavior when no secrets are configured;
- a named instance always requires its own `AKANE_ADMIN_TOKEN`;
- a named instance with `channels.qq.enabled = true` requires
  `QQ_CHANNEL_PROFILE_REF`, `QQ_BOT_QQ`, `QQ_WEBHOOK_SECRET`, and
  `QQ_ONEBOT_ACCESS_TOKEN`;
- the deployment profile ref must match the manifest exactly; missing or
  mismatched bindings fail startup before Engine databases open;
- the manifest remains authoritative for named-instance QQ enablement, so a
  stale global `QQ_BRIDGE_ENABLED` cannot silently attach another Bot;
- secret fields use hidden dataclass representations and are never projected
  into public health or settings values.

QQ ingress now authenticates the Bearer webhook secret before parsing the JSON
body. It then requires exact `self_id == QQ_BOT_QQ` before calling any Gateway,
Engine, recorder, cache, duplicate tracker, or follow-up path. Missing/wrong
credentials and wrong/missing `self_id` return structured failures. Every
OneBot GET/POST path uses the immutable endpoint and adds the configured
`Authorization: Bearer <access-token>` header. Plugin notifications continue
through the single Gateway already bound to this host; no second notification
authority was introduced.

All control-center/model-service POST routes, plugin administration routes,
and the Memcore backfill route use the same instance-bound admin policy. Named
instances require the admin Bearer token even when the real socket peer is
loopback, so a reverse proxy cannot turn a remote write into a trusted local
write. QQ status/self-check diagnostics use the same policy. Read-only public
`/health` remains exactly:

```json
{
  "status": "ok",
  "instance_id": "finance-prod",
  "root_binding": "valid"
}
```

The settings catalog marks instance channel endpoints, Bot id, workspace,
listen port, and management/channel secrets as deployment-owned. Secrets show
only `isSet`; they cannot be written through runtime overrides.

Deployment templates now use `akane@.service` plus one
`/etc/akane/instances/<instance>.env` per process. Nginx examples give every
instance a distinct upstream, port, server name, and access log; QQ ingress,
management routes, and QQ diagnostics have separate allowlists. Application
authentication remains mandatory even behind those proxy restrictions.

One Bot account binding to two instances remains prohibited by the deployment
contract. Akane does not create a cross-process global Bot registry: if shared
Bot routing is ever required, it must be a separate authenticated upstream
router with a new contract, not two hosts directly consuming one NapCat feed.

## E4 implemented boundary

`desktop_pet_next` now binds one desktop process to the instance selected by its
process environment and persisted `PetState.instanceId`:

- the client accepts a backend only after public `/health` returns `status=ok`,
  `root_binding=valid`, and an exact `instance_id` match;
- a backend URL edit remains pending until verification succeeds, repeated edits
  are coalesced, and a mismatch leaves the previous binding unchanged;
- startup, panel, workspace, workshop, and control-center reads all verify the
  same instance before consuming backend state;
- switching a verified backend aborts reply/TTS/voice work and clears resource,
  Care, workspace-task, and delivery caches before the new connection is used;
- a desktop process cannot change to another instance id. Switching instances
  requires a process launched with the other instance id and data root.

Browser storage uses `akane.instance.<instance_id>.*` keys. The active character
and workshop drafts are instance-scoped, and legacy unscoped values are imported
only for `local-default`. Backend URL, session, profile, and character state use
the instance-owned `pet_state.json`; control-center compatibility reads are also
scoped by the verified instance id. Equal session, profile, and character ids in
two instances therefore do not select the same client state.

Tauri mutable artifacts now stay below the instance root:

```text
<data_root>/cache/desktop_pet/attachments/audio
<data_root>/cache/desktop_pet/character_import
<data_root>/cache/desktop_pet/export_staging
```

Character packs remain below `<data_root>/characters` and desktop state remains
below `<data_root>/state`. Read-only Embedding, Whisper, RVC, and similar model
binary caches are not moved into this desktop cache and may still be shared by
deployment policy.

Named-instance control-center and workshop management POSTs use a narrow Tauri
proxy. The proxy reads `AKANE_ADMIN_TOKEN` only from the desktop process
environment, verifies the persisted backend instance again, restricts requests
to the bound backend origin and management route prefixes, and adds the Bearer
header inside Rust. The token is never returned to JavaScript or written to
localStorage, logs, prompts, snapshots, or public health. Missing named-instance
tokens and mismatched backend identities return structured failures.

Windows launchers accept and propagate `-InstanceId`, `-DataRoot`,
`-BackendPort`, and `-EnvFile`. Named instances fail closed without an explicit
data root from the argument, instance env file, or process environment. They do
not copy `local-default` legacy state. Backend reuse/restart decisions require an
exact health identity match; a different instance on the requested port is not
reused or stopped. Backend log file names contain the validated safe instance
id.

E4 validation covers equal A/B client ids with different localStorage values,
health identity rejection, launcher port ownership decisions, instance-root
desktop cache paths, local-default legacy behavior, secure management token
delivery, and the unchanged three-field public health contract. The real smoke
starts two named backends concurrently, checks authenticated management writes,
reuses the matching instance, and confirms an A launcher cannot reuse or stop B.

## E5 implemented boundary

The final acceptance runs two named processes concurrently with deliberately
equal profile, session, character-pack, attachment-handle, plugin, notification
idempotency, and client-storage ids. Their roots, backend ports, Bot accounts,
QQ profile references, webhook secrets, OneBot access tokens, model-service
credentials, and logs are distinct.

The real smoke verifies the complete mutable boundary:

- both processes expose the exact three-field public health response;
- a memory turn written through `/think_once` is visible only through the same
  instance's session API, even though both instances use the same session,
  profile, and character ids;
- a Care purchase mutates only the selected instance's authoritative
  `care_runtime.json` and snapshot;
- importing same-named files produces equal attachment handles backed by
  different instance workspace content, and an A-only handle is initially 404
  from B;
- the same installed stateful plugin id receives a different host-owned storage
  directory in each process and preserves different values across invocation;
- the same plugin notification idempotency key delivers once through each
  instance's own QQ gateway, with the correct OneBot token, while duplicates
  are suppressed only inside that process;
- model-service settings are saved, reloaded, used for a real local
  OpenAI-compatible completion, and read back from only the owning instance;
- equal browser session, profile, character, and draft ids remain separated by
  the E4 client-storage namespace;
- a second process cannot bind A's live root, stopping or restarting A does not
  disturb B, and A's memory, Care, files, plugin state, and model settings remain
  present after restart;
- backend logs live below separate roots with safe instance ids and contain none
  of the admin, QQ, or model-service secrets used by the acceptance.

The smoke also crosses the QQ boundaries deliberately: B's webhook secret is
rejected by A, and A rejects an authenticated event carrying B's Bot id before
any gateway, Engine, plugin, recorder, cache, or follow-up work runs.

Run the final acceptance with:

```powershell
python scripts/smoke_m65_e5_two_instance.py
```

M65-E has no remaining implementation gate. Operations work such as persistent
public quota, shared read-only model-cache policy, or a separate upstream
multi-Bot router remains outside this milestone and does not weaken the
one-process/one-root/one-Bot isolation contract.
