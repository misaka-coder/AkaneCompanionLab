# QQ Outbound Message Planning Audit V1

## Goal

Make automatic QQ delivery consume one neutral protocol policy without moving
Akane product behavior into `channelcore-onebot` or creating a second outbound
message-chain implementation.

## Reference finding

AstrBot decorates a result with quote/mention segments only when every result
component is plain text or image. Voice, file, forward, and other standalone
content bypass that decoration. The useful rule is the capability boundary,
not AstrBot's product prompt or pipeline shape.

## Authority split

`channelcore-onebot` owns:

- neutral OneBot targets and segment/action shapes;
- logical acknowledgement normalization;
- the stable content capability matrix;
- the bounded, thread-safe one-visible-reply claim ledger.

Akane owns:

- which product content is sent and in what order;
- safe local-file authorization, path projection, staging, retries, and fallback;
- user-visible delivery feedback;
- explicit model-authored `onebot_action` operations.

MemCore, model prompts, tool trajectories, and inbound message projection do
not change in this slice.

## Frozen capability matrix

| Content | Action family | May share reply | Standalone |
|---|---|---:|---:|
| text | message | yes | no |
| image | message | yes | no |
| record | message | no | yes |
| mface | message | no | yes |
| music | message | no | yes |
| file | upload | no | yes |
| forward | forward | no | yes |

The matrix prevents two concrete QQ failures: duplicate visible quote frames,
and record/card delivery being combined with a reply segment that some OneBot
clients do not deliver reliably. Standalone content does not consume the claim,
so a later text or image can still quote the inbound message.

## Reintegration outcome

The Akane-owned claim dictionary, lock, eviction constant, and policy helper are
deleted. `NapCatQQGateway` retains only a thin adapter that passes its target,
source message id, and content families to the package ledger. Product media
fallback remains in the gateway because the package cannot authorize Akane
paths or decide user-facing degradation.

No dynamic system prompt, tail hint, MemCore event, or additional model field
is introduced. Therefore this slice does not change prompt-cache prefixes or
model-visible context.
