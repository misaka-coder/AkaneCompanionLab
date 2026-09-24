# Public event producer

Install this normal Python project and `../akane_sdk_event_calculator` through
Akane's source staging/install path, granting their declared permissions.
Both depend on the independently installable `akane-plugin` SDK 0.14.0.

Call `example.event-source.publish_numbers` with `{"a": 2, "b": 40}`. The result
contains `dispatch_id`, `status`, `complete`, and per-subscriber deliveries.
`accepted` means processing was scheduled. Query
`example.event-source.dispatch_status` in the same conversation until
`complete` is true. A completed receipt links the calculator's downstream
receipt, whose returned value contains `{"sum": 42, "input_event_id": "..."}`.

The two event handlers perform no model calls and have global calculation
scopes. Their source filters use runtime-verified plugin identities. The
publisher's conversation is used only for access to its receipt; the calculator
does not borrow it. Disabling either subscriber removes its subscription and
cancels its queued/running event work. Receipts are transient, with no restart
recovery or automatic business retry. See the SDK README for retention and
conversation binding semantics.
