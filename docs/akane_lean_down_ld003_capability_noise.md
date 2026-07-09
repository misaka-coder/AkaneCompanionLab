# AKANE-LD-003 Capability Noise Cleanup

Status: implemented
Date: 2026-07-09

## Decision

`desktop_environment` was removed from `CapabilityRegistry`.

It had:

- no tools;
- a `_never` trigger;
- a future-facing light hint shown in desktop-pet mode.

That made the runtime and prompt surface claim a planned desktop observation
ability before Akane had real desktop context, active-window data, shortcut
actions, or permission boundaries.

## Rule

Future-only capabilities belong in docs or tickets until they have real
behavior. They should not appear as prompt hints, runtime modules, status
fields, or UI controls.

This does not block future desktop observation work. It only means the future
module must return with:

- real runtime data or real tools;
- an explicit safety boundary;
- tests proving the model sees and can call only what is actually available.

## Validation Focus

- Desktop-pet still exposes real desktop layers such as workspace, browser, and
  music request.
- Desktop-pet no longer exposes `desktop_environment` as a module or layer.
- Policy docs now say package/backfill work should reduce Akane complexity, not
  preserve placeholder noise.
