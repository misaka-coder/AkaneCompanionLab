# channelcore-onebot Akane integration v0

Status: inbound normalization + event-admission + group-trigger + quoted-message slices integrated.

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
  -> channelcore_onebot.resolve_quoted_message(..., call_action=bot_transport)
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
- `get_msg` action selection, OneBot status/retcode validation, quoted message
  normalization, and group/private reply scope validation;
- fail-closed `scope_unverifiable` for private replies without enough participant
  data.

## Akane-owned

- FastAPI routes, webhook secrets, deployment profiles, and runtime selection;
- webhook-secret/profile binding and HTTP status mapping for self-id failures;
- session/profile/character/model/reply-mode and memory mapping;
- group vision settings and product commands (the host only supplies the
  package's `allow_attachment_follow` strategy input);
- Bot-bound OneBot action transport for quoted-message and attachment-cache
  lookup (fixed action allowlist, dedicated non-proxying Session, redirect
  rejection, and separate HTTP/status/retcode validation);
- safe attachment download/materialization, vision, MemCore, model, and TTS;
- choice of response media and all outbound OneBot actions.

Akane's materialization boundary treats `AttachmentRef.locator` as untrusted
input. QQ event `path`, `local_path`, and `workspace_uri` never authorize a
local read, and `file` remains an opaque OneBot token. Bot-bound OneBot cache
responses may expose a local path only when it resolves inside an
explicit `QQ_ONEBOT_CACHE_ROOTS` directory; otherwise Akane requires base64 or
the later URL download path. Materialization failures persist only stable codes
and public reasons, not raw paths, URLs, or exceptions.

NapCat may provide a path-shaped `file` value as an opaque `/get_image` or
`/get_file` token. Akane may return it only through the action transport bound
to the same Bot; it never becomes local-read authority and is not persisted or
rendered. An invalid supplied token falls back only to a sanitized
`origin_name` basename. Direct media URLs remain PUBLIC_ONLY, including
loopback URLs on the configured OneBot origin.

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
  winner, and quoted attachments from another group/private peer are rejected
  before materialization. No vision, reply-send, or new sticker behavior is
  advertised.

## Remaining protocol slices

Later work may replace, one boundary at a time:

1. text/image/voice/file/mface outbound actions with one sanitized structured
   result contract;
2. real reply-segment sending and face/mface host consumption.

Each applied slice must delete or thin the corresponding Akane authority in the
same change. In particular, HTTP 200 with a non-zero OneBot retcode must not be
reported as success, and outbound results must not expose local paths, tokens,
attachment URLs, raw exceptions, or raw OneBot payloads.

The Akane-owned remote materialization repair now validates public HTTP/HTTPS
URLs before creating a pending item, requires every resolved A/AAAA address to
be public, disables environment proxies, follows at most three redirects
manually, revalidates every hop, and checks the connected peer before reading
the response body. Direct QQ URL fallback has no local OneBot-origin exception:
it remains fail-closed for loopback, private, link-local, reserved, multicast,
unspecified, mixed public/private DNS, and unverifiable peers. Downloads use a
bounded `.part` file and atomically replace the destination only after success.

Full remote URLs are transient transport inputs. New material records persist a
SHA-256 fingerprint as `source_event_id`; source detail and prompts retain only
the public origin, platform, uploader, and non-reversible fingerprints. Legacy
`source_url` / `webpage_url` detail is projected through the same origin-only
renderer, so query tokens, userinfo, fragments, and paths do not enter prompts
or structured failure results. After a QQ remote-fetch attempt, the original
turn message and delivery metadata are also projected to the public origin (or
an explicit restricted-link marker) before they enter the engine, MemoryStore,
MemCore, or model prompt.

`yt-dlp` is only entered for the existing explicit provider set (Bilibili,
YouTube, Douyin, Ixigua, and Kuaishou hosts); arbitrary public media-file
direct links continue through the guarded downloader. Unknown webpages degrade
with a structured `remote_media_provider_not_allowed` result and never receive
configured cookies. The Generic extractor and environment proxies are disabled,
browser Cookie import is rejected, and an explicit cookies.txt is loaded only
when all of its cookie domains belong to the supported provider set. Short URLs
that require the Generic extractor now degrade until they are expanded to a
canonical provider URL or a separately isolated resolver is available.

Residual risk: `requests` performs its own hostname resolution when connecting,
so pre-resolution plus peer verification reduces exposure but cannot fully
eliminate DNS rebinding between validation and the first request. Peer checking
also happens after the HTTP request has been sent. `yt-dlp` owns its internal
API/media/manifest network stack after a non-Generic allowlisted extractor is
selected. Production deployments should therefore enforce outbound egress
firewall rules or use an isolated, IP-pinned download service in addition to
these application checks. Historical material rows are not rewritten in this
repair; their legacy URL fields are hidden by the prompt renderer, while the
fingerprint/origin-only persistence rule applies to newly written rows.

## Validation

```powershell
uv run --extra dev python -m unittest discover -s tests -v
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run python examples/minimal_inbound_event.py
uv run --extra dev python -m build

python -m unittest tests.test_qq_channelcore_integration tests.test_qq_gateway tests.test_qq_multi_bot_dispatch -v
python -m unittest tests.test_public_url_policy tests.test_attachment_ingest tests.test_attachment_inbox tests.test_backend_route_modules -v
python -m unittest tests.test_package_independence tests.test_package_reintegration_policy -v
git diff --check
```
