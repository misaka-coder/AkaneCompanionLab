"""HTTP query to an existing document tool, using public program calls only."""

from akane_plugin import (
    CapabilityIOSlot, CapabilityResult, ManagedArtifactDraft, ManagedArtifactPayload,
    Plugin, ToolCallError, ToolContext,
)

plugin = Plugin("example.report", permissions=("capability.invoke", "resource.read", "artifact.write"))


@plugin.tool(
    effects=("filesystem",),
    outputs=(CapabilityIOSlot("report", "file", required=True, max_bytes=2_000_000, delivery="generated_file"),),
    output_schema={"type": "object", "properties": {"row_count": {"type": "integer"}}, "required": ["row_count"]},
)
async def create(url: str, ctx: ToolContext, send_to_user: bool = True):
    """Query the requested JSON catalog and deliver a CSV report using the installed document writer."""
    rows = await ctx.tools.call("example.catalog.fetch", {"url": url})
    document = await ctx.tools.call_result("akane.document-writer.compose.v1", {
        "table_rows": [["Name", "Quantity", "Price"], *[
            [row["name"], row["quantity"], row["price"]] for row in rows
        ]],
        "output_format": "csv", "output_title": "Catalog report", "send_to_user": False,
    })
    if document.is_error:
        raise ToolCallError("akane.document-writer.compose.v1", document)
    artifact = document.content["managed_artifacts"][0]
    resource = await ctx.resources.open(artifact["generated_handle"])
    if not resource.ok:
        return CapabilityResult(is_error=True, status=resource.status, reason=resource.reason)
    return ManagedArtifactPayload(
        content={"row_count": len(rows)},
        artifact=ManagedArtifactDraft.from_file(
            resource.path, title="Catalog report",
            summary=f"Catalog query returned {len(rows)} rows.", send_to_user=send_to_user,
            source_handles=(artifact["generated_handle"],),
        ),
    )


def create_plugin():
    return plugin
