# Versioned statistics service

This independently packaged SDK example calculates real descriptive statistics
with the Python standard library. It registers `statistics` version 1 with a
`summarize(values)` method and requests `service.provide`. It exposes no model tool.

Install this source directory and `../akane_sdk_statistics_report` through
Akane's extension manager. The report tool delegates to the service contract;
it does not import this package or duplicate the calculation. For input
`[1, 2, 6]`, the result has count 3, mean 3, median 2, minimum 1 and maximum 6.
Empty input returns a structured invocation failure from the provider.

The consumer declares a required `statistics` version 1 dependency. It can be
installed first and remains in `waiting_dependency` until this provider is
installed and enabled. With multiple providers, select one using the existing
instance overlay's `service_bindings` field, then reload extensions. See the
public SDK README for the configuration shape.

Each top-level service call captures a fresh generation snapshot. Nested calls
retain it until completion or cancellation, including across worker callbacks.
The next top-level call observes the latest provider. Explicit disable or
revocation withdraws authority immediately.
