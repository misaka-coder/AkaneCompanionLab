# Akane Gentle Check-in sample plugin

This installable mixed plugin proves Akane's public plugin contracts together:

- observes direct/group inbound events without opening extra Agent turns;
- stores configuration and activity only in the plugin-scoped data directory;
- exposes one honest local-state CapCore capability (no fake network access);
- contributes a progressively loaded `gentle-checkin` Skill from the wheel;
- runs one supervised background service;
- requests an explicit host Agent turn using a host-issued conversation reference;
- delivers through the same structured QQ rendering path as a normal reply.

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
The model capability configures the current QQ conversation without asking for
or guessing a recipient ID. Group command configuration still requires owner/admin authority.

The sample does not access arbitrary paths, does not read the network, does not
hold Engine/QQ gateway references, and does not write directly to MemCore.

## Permissions

- `capability.prompt.invoke`: expose the private-chat configuration tool.
- `storage.write`: persist only this plugin's configuration and activity ledger.
- `job.run`: run the supervised quiet-period watcher.
- `agent.turn.request`: request the ordinary Agent path with the captured conversation reference.
- `qq.command.register`: register `/checkin` for explicit QQ configuration.
- `event.subscribe`: observe direct/group inbound activity without waking an Agent.
- `skill.contribute`: publish the bundled Skill through the shared Skill registry.

No `network.read` permission is requested because local plugin state is not a
network effect.

## Local validation and installation

Use Akane's plugin management interface. Stage this source directory first,
review the returned contributions and exact permission list, then install that
immutable stage. Installation publishes and activates one complete candidate
generation; a failed candidate leaves the last-good generation running. Do not
pip-install the wheel or edit instance configuration manually.
