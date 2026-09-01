# Akane Gentle Check-in sample plugin

This installable mixed plugin proves Akane's public plugin contracts together:

- observes direct/group inbound events without opening extra Agent turns;
- stores configuration and activity only in the plugin-scoped data directory;
- exposes one honest local-state CapCore capability (no fake network access);
- contributes a progressively loaded `gentle-checkin` Skill from the wheel;
- runs one supervised background service;
- asks Akane's normal reasoning/MemCore loop to compose the check-in;
- delivers through the host notification port with a stable idempotency key.

The behavior is intentionally small: after a configured conversation has been
quiet for the selected interval, Akane sends one natural check-in. It will not
send another until a new inbound message starts a new quiet period.

Private QQ conversations can be configured through the model tool or with:

```text
/checkin on 30
/checkin status
/checkin off
```

Group configuration uses the same command and requires QQ owner/admin role.
The model capability deliberately rejects group enablement because the generic
CapCore invocation context does not carry group administrator authority.

The sample does not access arbitrary paths, does not read the network, does not
hold Engine/QQ gateway references, and does not write directly to MemCore.

## Permissions

- `capability.prompt.invoke`: expose the private-chat configuration tool.
- `storage.write`: persist only this plugin's configuration and activity ledger.
- `job.run`: run the supervised quiet-period watcher.
- `notification.send`: deliver the completed check-in through the host port.
- `model.reasoning`: enter Akane's normal Engine/MemCore reasoning path.
- `qq.command.register`: register `/checkin` for explicit QQ configuration.
- `event.subscribe`: observe direct/group inbound activity without waking an Agent.
- `skill.contribute`: publish the bundled Skill through the shared Skill registry.

No `network.read` permission is requested because local plugin state is not a
network effect.

## Local build and enable

```powershell
python -m build --wheel examples/plugins/akane_gentle_checkin
python -m pip install --no-deps examples/plugins/akane_gentle_checkin/dist/akane_gentle_checkin-0.1.0-py3-none-any.whl
```

```toml
[[plugins]]
id = "akane.sample.gentle-checkin"
enabled = true
```

The current lifecycle is restart-only. Set `enabled = false` and restart the
plugin host to withdraw the capability, events, command, service, and Skill
together. Staged install/uninstall and last-good switching belong to M67-E.
