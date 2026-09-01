# Akane plugin examples

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

Each example documents its permissions, runtime effect, and installation path
in its own README.
