# Akane Instance Isolation M65-E

Status: E1 fail-closed instance root implemented; E2 through E5 pending.

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

## Remaining gates

### E2 — instance-owned mutable paths and shutdown

Move the workspace, memory mirror, constrained Memcore path, logs, and remaining
runtime path reads behind the instance layout. Stop or join writers before the
lease is released. Add the offline one-instance migration tool.

### E3 — QQ and administration isolation

Bind the immutable QQ profile to deployment secrets, authenticate ingress,
require matching `self_id`, add the OneBot token, protect management writes,
and instantiate systemd/Nginx/environment templates.

### E4 — desktop and launcher isolation

Namespace Tauri cache and browser state by verified backend instance id. A
desktop client still connects to only one instance at a time.

### E5 — two-instance acceptance

Run personal and finance-oriented processes together with deliberately equal
user, session, character, and plugin ids but different roots, Bot accounts,
secrets, ports, and logs. No memory, Care, file, plugin, notification, model
configuration, client-cache, or lifecycle state may cross the boundary.
