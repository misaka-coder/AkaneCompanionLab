# channelcore-onebot Akane integration v0

Status: inbound normalization slice integrated.

## Decision

`channelcore-onebot` is one independent Python package, not a premature
`channelcore` plus adapter split. Its first applied slice is deliberately
narrow: immutable inbound contracts and stateless OneBot event/message-segment
normalization.

The runtime path is:

```text
OneBot/NapCat webhook
  -> Akane webhook and per-Bot self_id authorization
  -> channelcore_onebot.normalize_inbound_event(...)
  -> Akane QQMessageContext product projection
  -> Akane commands, safe attachment materialization, vision, MemCore/LLM, TTS
  -> current Akane OneBot delivery path
```

## Package-owned in this slice

- `ConversationRef`, `ActorRef`, `ReplyRef`, `AttachmentRef`,
  `InboundMessage`, and `InboundParseResult`;
- private/group message and poke-notice normalization;
- structured segments and raw CQ fallback;
- text, at, reply, image, voice, file, video, face, and mface parsing;
- per-Bot wake-word inputs and neutral trigger reasons;
- sensitive attachment locators hidden from repr and public summaries.

## Akane-owned

- FastAPI routes, webhook secrets, deployment profiles, and runtime selection;
- per-Bot self-id authorization before mutable replay state;
- stale/duplicate event state and the stateful group attachment window;
- session/profile/character/model/reply-mode and memory mapping;
- group vision settings and product commands;
- quoted-message HTTP lookup and scope policy;
- safe attachment download/materialization, vision, MemCore, model, and TTS;
- choice of response media and all outbound OneBot actions.

`QQMessageContext` therefore remains in Akane, but its protocol parsing inputs
come from `InboundMessage`. Compatibility methods such as
`extract_attachments()` contain projection only; they do not reimplement CQ or
OneBot segment rules.

## User-visible effect

This slice is behavior-preserving. Existing Bots still use their own Akane
profiles and product settings. The practical improvement is that their common
message, attachment, reply, mention, wake-word, and poke shapes now pass
through one reusable protocol authority. No vision, reply-send, or new sticker
behavior is advertised by this slice.

## Remaining protocol slices

Later work may replace, one boundary at a time:

1. self-id plus thread-safe stale/replay guards and stateful group trigger
   calculation;
2. quoted-message lookup and quoted attachment scope validation;
3. text/image/voice/file/mface outbound actions with one sanitized structured
   result contract;
4. real reply-segment sending and face/mface host consumption.

Each applied slice must delete or thin the corresponding Akane authority in the
same change. In particular, HTTP 200 with a non-zero OneBot retcode must not be
reported as success, and outbound results must not expose local paths, tokens,
attachment URLs, raw exceptions, or raw OneBot payloads.

## Validation

```powershell
uv run --extra dev python -m unittest discover -s tests -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run python examples/minimal_inbound_event.py
uv run --extra dev python -m build

python -m unittest tests.test_qq_channelcore_integration tests.test_qq_gateway tests.test_qq_multi_bot_dispatch -v
python -m unittest tests.test_package_independence tests.test_package_reintegration_policy -v
git diff --check
```
