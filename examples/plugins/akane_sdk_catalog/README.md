# HTTP catalog query

This independently packaged SDK example contributes `example.catalog.fetch`.
Its `url` argument names an HTTP(S) endpoint returning a JSON array of
`{"name": string, "quantity": integer, "price": number}` rows. It performs a
real GET; network, JSON and output-contract failures are structured failures.
There is no built-in sample-success response, credential or file output.

Requires the SDK 0.14.0 and CapCore 0.1.3 release wheels. Build/install with normal
Python tools for development, or stage this source project and install its stage
through Akane's existing extension management service.

Permissions: `capability.prompt.invoke`, `network.read`. Read the actual returned
data using `ctx.tools.call("example.catalog.fetch", {"url": url})`; no file
argument or model followup is needed. The `akane_sdk_report` example consumes
this query through that public entry point.
