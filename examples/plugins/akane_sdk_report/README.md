# Query to document through public program calls

Install this source project, `akane_sdk_catalog`, and the existing
`akane.document-writer` plugin through normal extension management. Supply an
HTTP(S) URL serving the catalog schema described in `akane_sdk_catalog/README.md`.
Invoke `example.report.create` with `url`; `send_to_user` defaults to true.

The function calls `example.catalog.fetch`, passes its rows to
`akane.document-writer.compose.v1`, opens the returned document handle through
the resource port, and returns the final CSV as a managed artifact. It does not
contain an HTTP client, CSV renderer, model client or delivery implementation.
The target tools retain their normal permissions and lifecycle checks.

Only the final report requests user delivery. Internal program calls return
canonical values/metadata, create no model preview, and do not request a model
turn. Final delivery remains a host request until the client acknowledges it.
Missing/disabled targets, wrong query output and document failures preserve
their structured errors; they do not produce a success report.

Permissions: `capability.prompt.invoke`, `capability.invoke`, `resource.read`,
`artifact.write`. The project imports only `akane_plugin`, requires SDK 0.14.0 and
CapCore 0.1.3, and declares its file output/effect using the existing contracts.

Repository verification:

```powershell
python -m unittest tests.test_plugin_sdk_composition -v
```

The test installs these actual projects plus the document writer, uses a real
local HTTP server, opens and checks the generated CSV, and exercises the desktop
file-delivery adapter. Its HTTP data and client transport are test fixtures;
it does not claim a public data-provider or live desktop delivery evaluation.
