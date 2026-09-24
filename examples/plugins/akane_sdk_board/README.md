# Board observations and explicit decisions

Install this independent Python project with `akane-plugin` 0.5. Enable
`example.board` through the host's normal plugin admission process. It uses
only the public SDK; it does not provide a game engine or choose legal moves.

Call `example.board.update` with a typed state such as
`{"state":{"moves":["A1"],"finished":false,"score":0}}`. This returns an
observation version and creates no model request. Call `example.board.decide`
with that version when you want the character to choose the next move.
Repeated decisions queued in the same conversation merge into one request
using the latest board. `status` reports model execution and channel delivery
separately; a desktop delivery of `queued` means handed to the desktop output
queue, not proof that audio has finished playing.

For event-driven decisions, call `enable` in the intended conversation, then
`publish` with the new board. The handler explicitly requests a decision and
returns its receipt. The event remains pending until that decision reaches a
terminal state. Keep the returned scope ID to `disable` the subscription.
Publishing before enabling returns `unobserved` and creates no model work.

`cancel` accepts the request ID. A running request may remain `cancelling`
until the normal model runner reaches a safe stop point. Conversation bindings,
observations and request receipts are transient across host restarts. Global
event handlers cannot choose a target by putting identity fields in their data.
