# Statistics service consumer

Install alongside `../akane_sdk_statistics`. The `example.statistics-report.summarize`
tool delegates its numeric dataset to the public `statistics` version 1 service.
The consumer depends only on `akane-plugin`, with no provider package dependency
or host import. The service call returns canonical JSON and makes no LLM request.

`example.statistics-report.describe_service` uses
`ctx.services.list("statistics")` to return providers, selection, status and full
input/output method schemas. It does not execute a statistics calculation.

The host enforces the method's input/output schemas and existing execution policy.
The manifest declares `ServiceDependency("statistics", version=1)`. Missing or
ambiguous providers keep this plugin in `waiting_dependency`; its model tool
appears only after the dependency is resolved. Installing this consumer before
the provider is supported. Disabling the provider withdraws the consumer until
the dependency is restored. Use the instance overlay's `service_bindings` field
to select an implementation when multiple providers are enabled.
