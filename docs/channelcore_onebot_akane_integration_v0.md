# channelcore-onebot Akane integration v0

Status: inbound normalization + event-admission + group-trigger slices integrated.

## Decision

`channelcore-onebot` is one independent Python package, not a premature
`channelcore` plus adapter split. Its applied slices remain deliberately
narrow: immutable inbound contracts, OneBot event/message-segment
normalization, admission, and neutral group-trigger calculation.

The runtime path is:

```text
OneBot/NapCat webhook
  -> Akane webhook and per-Bot self_id authorization
  -> channelcore_onebot.OneBotEventAdmission.admit(...)
  -> channelcore_onebot.normalize_inbound_event(...)
  -> channelcore_onebot.GroupTriggerPolicy.evaluate(...)
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
- sensitive attachment locators hidden from repr and public summaries;
- per-Bot `self_id` comparison;
- seconds/milliseconds timestamp normalization and stale-event decisions;
- thread-safe, atomic TTL replay claims with Bot-account-isolated fingerprints;
- neutral group mention/wake-word and same-actor attachment-follow decisions,
  keyed by Bot account, group, and actor.

## Akane-owned

- FastAPI routes, webhook secrets, deployment profiles, and runtime selection;
- webhook-secret/profile binding and HTTP status mapping for self-id failures;
- session/profile/character/model/reply-mode and memory mapping;
- group vision settings and product commands (the host only supplies the
  package's `allow_attachment_follow` strategy input);
- quoted-message HTTP lookup and scope policy;
- safe attachment download/materialization, vision, MemCore, model, and TTS;
- choice of response media and all outbound OneBot actions.

`QQMessageContext` therefore remains in Akane, but its protocol parsing inputs
come from `InboundMessage`. Compatibility methods such as
`extract_attachments()` contain projection only; they do not reimplement CQ or
OneBot segment rules. `deployment_security.py` maps the package's neutral
identity result back to the existing `qq_self_id_mismatch` HTTP response.

## User-visible effect

These slices are behavior-preserving. Existing Bots still use their own Akane
profiles and product settings. The practical improvement is that their common
message, attachment, reply, mention, wake-word, and poke shapes now pass
  through one reusable protocol authority. Replay races now have an atomic
  winner, but no vision, reply-send, or new sticker behavior is advertised.

## Remaining protocol slices

Later work may replace, one boundary at a time:

1. quoted-message lookup and quoted attachment scope validation;
2. text/image/voice/file/mface outbound actions with one sanitized structured
   result contract;
3. real reply-segment sending and face/mface host consumption.

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
