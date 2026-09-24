# HTTP query with a declared connection

This independent project uses only `akane-plugin>=0.14,<0.15` and the Python
standard library. It registers the `example.http-query.query` tool and declares
its `query_api` configuration. Adding this connection requires no host name
whitelist or business branch.

Stage this directory with the existing source-install action, review the returned
permissions, and install the stage. Configure it through the host's authenticated
management API; use the actual profile that will invoke the tool:

```http
GET /admin/plugins/example.http-query/connections/query_api?profile_user_id=owner
```

The response includes the schema, public values, private-field presence and a
revision. Submit a JSON update with that revision:

```http
PUT /admin/plugins/example.http-query/connections/query_api?profile_user_id=owner
Content-Type: application/json

{
  "expected_revision": 0,
  "enabled": true,
  "values": {
    "endpoint": "https://your-service.example/query",
    "credential": "your-service-credential",
    "timeout_seconds": 10
  }
}
```

`endpoint` must answer GET requests with a JSON object. The plugin appends `q`
and supplies `Authorization: Bearer …`. Invoke the tool with `{"text":"hello"}`
from a normal bound conversation or authorized plugin scope. The administrative
capability probe has no conversation identity and therefore cannot resolve this
private connection. Failed HTTP or JSON operations return `query_request_failed`.

The management response never echoes the credential. Omit it to preserve it,
or replace it in `values` to rotate it for subsequent invocations. Explicitly
clear it with `clear_fields: ["credential"]` and `enabled: false`; an incomplete
configuration cannot be enabled. Concurrent edits return a revision conflict.

The automated acceptance test installs this source through the real extension
manager and calls a local HTTP fixture through a worker. It verifies complete
lists, missing/disabled configurations, slow-request rotation, and rejected
credential echoes. The SDK release verifier independently installs its wheel
and performs HTTP in a fresh virtual environment where the host is unavailable.
This is a backend configuration example; the generic control-center form is a
separate P9 delivery.
