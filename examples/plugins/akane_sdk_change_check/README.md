# Content change check

This independent plugin compares actual UTF-8 content using SHA-256. It does not
pretend to monitor a source: a caller provides each new version and the digest
returned by the previous check.

Build and install this directory through the normal plugin source installation
flow. It depends only on the public `akane-plugin>=0.14,<0.15` SDK.

Ask Akane to call `example.change-check.check` with `content` and an empty
`previous_digest` to obtain the initial digest. Call it again with the same
content and the returned digest: the result is stored normally, but it requests
no extra model reply. Change one character and it requests normal followup.
`example.change-check.check_background` performs the same comparison through the
host's background job runner in QQ or desktop conversations. It does not add an
artificial delay. Program callers always receive the data directly.

The accepted background call returns a `job_id`. An authenticated administrator
can read the full result and delivery receipt at
`GET /admin/plugins/jobs/{job_id}?profile_user_id=PROFILE&session_id=SESSION`.
This diagnostic read does not grant execution rights for the supplied owner.
No change means zero additional model requests after completion; the result
still includes `changed`, `digest`, and `utf8_bytes`. There is no empty completion
message. An explicitly required consumer or an unhandled error still needs
processing, and an explicit `ctx.request_turn()` remains independent of followup.
