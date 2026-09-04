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

- `akane_poke_streak`: observes QQ direct/group events and adds a request-local
  fact when the same actor pokes Akane repeatedly. It registers no model tool,
  writes no MemCore record, and sends no message by itself.
- `akane_hacker_news_report`: reads a fixed public Hacker News feed through one
  CapCore capability and returns structured facts plus a host-managed Markdown
  report. It has no arbitrary URL, credential, or local-path input.
- `akane_gentle_checkin`: combines quiet conversation observation, scoped
  configuration, a progressively loaded Skill, a supervised service, normal
  Akane reasoning, and idempotent QQ delivery. It sends at most once per quiet
  period and does not pretend to need network access for local plugin state.
- `akane_timer`: persists one-shot events for the current conversation and
  submits due events through the host-owned Agent path instead of creating a
  second model or delivery pipeline.

Each example documents its permissions, runtime effect, and installation path
in its own README.

Model-driven source projects should keep ordinary `unittest` tests using these
real imports. Run them with `manage_extension(test_source)` so the current
release SDK is supplied by the host; do not copy or fake SDK modules inside a
plugin project. Then use `stage_source` and `install` for runtime validation.
