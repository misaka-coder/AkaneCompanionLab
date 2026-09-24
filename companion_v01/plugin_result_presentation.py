"""Model previews over the existing artifact store, never a second result store."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from capcore import CapabilityResult, InvocationContext

from .plugin_api import ManagedArtifactDraft
from .plugin_managed_artifacts import ManagedArtifactSink, normalize_managed_artifact_reference
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .tool_handlers.core import ToolFollowupEnvelope


DEFAULT_PLUGIN_RESULT_PREVIEW_CHARS = 16_000


async def project_model_result(
    *,
    result: CapabilityResult,
    rendered: str,
    capability_id: str,
    context: InvocationContext,
    sink: ManagedArtifactSink | None,
    preview_chars: int,
    stored_material: str | None = None,
) -> ToolFollowupEnvelope:
    """Save canonical JSON, or an explicitly supplied final display text, for paging.

The ordinary GeneratedFileService owns scope, storage and subsequent reads.
This function does not execute the capability again or request file delivery.
"""

    from .tool_handlers.core import ToolFollowupEnvelope

    diagnostics = {"total_chars": len(rendered), "preview_budget_chars": preview_chars}
    if len(rendered) <= preview_chars:
        return ToolFollowupEnvelope(
            content=rendered, producer_bounded=True, complete=True, diagnostics=diagnostics,
        )
    reference = None
    reason = "plugin_result_storage_unavailable"
    if sink is not None:
        try:
            # Use the existing file-backed artifact path, avoiding both an
            # additional inline byte ceiling and another full bytes copy.
            with tempfile.TemporaryDirectory(prefix="akane-plugin-result-") as directory:
                path = Path(directory) / "result.json"
                if stored_material is None:
                    with path.open("w", encoding="utf-8", newline="\n") as stream:
                        json.dump(result.content, stream, ensure_ascii=False, allow_nan=False, indent=2)
                    output_format = "json"
                    mime_type = "application/json"
                    summary = "同一次插件调用的完整结构化结果；用于续读，不代表已发送给用户。"
                else:
                    path = path.with_suffix(".txt")
                    path.write_text(str(stored_material), encoding="utf-8", newline="\n")
                    output_format = "txt"
                    mime_type = "text/plain"
                    summary = "同一次插件调用的最终模型展示文本；用于续读，不代表已发送给用户。"
                draft = ManagedArtifactDraft(
                    path=path, title=f"plugin-result-{capability_id}"[:120],
                    output_format=output_format, mime_type=mime_type,
                    summary=summary,
                    send_to_user=False,
                )
                created = await sink.materialize(draft, context=context, capability_id=capability_id)
                reference = normalize_managed_artifact_reference(created, draft=draft, capability_id=capability_id)
            if reference is None:
                reason = "plugin_result_storage_invalid_reference"
        except Exception:
            # A storage failure must not erase an already executed action or
            # expose physical paths/exception text in model feedback.
            reason = "plugin_result_storage_failed"
    preview = rendered[:preview_chars]
    diagnostics["shown_chars"] = len(preview)
    if reference is None:
        diagnostics["reason"] = reason
        return ToolFollowupEnvelope(
            content=(
                f"{preview}\n\n【结果展示不完整】完整结果材料未能保存（{reason}）。"
                "以上仅为预览，不能据此声称已读取全部结果；不要为续读自动重跑有副作用的动作。"
            ),
            producer_bounded=True, complete=False, diagnostics=diagnostics,
        )
    handle = reference["generated_handle"]
    diagnostics["result_handle"] = handle
    return ToolFollowupEnvelope(
        content=(
            f"{preview}\n\n【结果展示不完整】本页展示 {len(preview)} 字，共 {len(rendered)} 字。"
            f"同次执行的完整 JSON 已保存为 {handle}，未自动发送。"
            f'需要完整数据时调用 inspect_generated_file(target="{handle}", section="content")；'
            "结果仍长时沿该读取工具返回的 cursor 继续，无需重新执行原工具。"
        ),
        producer_bounded=True, complete=False,
        continuation={"type": "inspect_generated_file", "target": handle, "section": "content"},
        diagnostics=diagnostics,
    )
