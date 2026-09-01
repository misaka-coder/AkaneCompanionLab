# Akane plugin examples

These packages use the same wheel and `akane.plugins.v1` entry-point contract
as installed Akane plugins. They are executable examples rather than host-only
fixtures.

- `akane_poke_streak`: observes QQ direct/group events and adds a request-local
  fact when the same actor pokes Akane repeatedly. It registers no model tool,
  writes no MemCore record, and sends no message by itself.

Each example documents its permissions, runtime effect, and installation path
in its own README.
