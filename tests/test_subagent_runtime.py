from __future__ import annotations

from pathlib import Path
import unittest

from companion_v01.subagent_runtime import (
    InProcessSubagentProvider,
    SubagentProviderCapabilities,
    SubagentProviderRegistry,
    SubagentRunResult,
    SubagentStartRequest,
)

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def _request(**overrides) -> SubagentStartRequest:
    values = {
        "task": "审计项目的并发边界并给出报告。",
        "child_session_id": "subagent_0123456789abcdef0123456789abcdef",
        "parent_profile_user_id": "master",
        "parent_session_id": "qq_group_shared_87",
        "label": "concurrency-audit",
        "working_directory": REPO_ROOT,
        "allowed_tools": ("project_inspect", "exec_run"),
    }
    values.update(overrides)
    return SubagentStartRequest(**values)


class SubagentRuntimeTests(unittest.TestCase):
    def test_valid_request_runs_once_and_returns_only_normalized_result(self) -> None:
        calls = []

        def runner(request, *, cancelled):
            calls.append((request, cancelled()))
            return SubagentRunResult(
                status="succeeded",
                child_session_id=request.child_session_id,
                summary="完成审计。",
                artifacts=(
                    {"handle": "gen_report", "source": "generated_file"},
                    {"handle": "gen_report", "source": "duplicate"},
                ),
            )

        registry = SubagentProviderRegistry()
        registry.register(InProcessSubagentProvider(runner))

        result = registry.execute("in_process", _request(), cancelled=lambda: False)

        self.assertEqual(len(calls), 1)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.summary, "完成审计。")
        self.assertEqual(result.artifacts, ({"handle": "gen_report", "source": "generated_file"},))

    def test_provider_capability_is_checked_before_runner(self) -> None:
        calls = []
        provider = InProcessSubagentProvider(
            lambda *_args, **_kwargs: calls.append(True),
            capabilities=SubagentProviderCapabilities(tool_filter=False, workspace=False),
        )
        registry = SubagentProviderRegistry()
        registry.register(provider)

        result = registry.execute("in_process", _request(), cancelled=lambda: False)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "subagent_workspace_unsupported")
        self.assertEqual(calls, [])

    def test_cancelled_request_never_starts_provider(self) -> None:
        calls = []
        registry = SubagentProviderRegistry()
        registry.register(InProcessSubagentProvider(lambda *_args, **_kwargs: calls.append(True)))

        result = registry.execute("in_process", _request(), cancelled=lambda: True)

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.reason, "subagent_cancelled")
        self.assertEqual(calls, [])

    def test_late_cancel_does_not_replace_provider_terminal_result(self) -> None:
        for status in ("succeeded", "failed", "cancelled"):
            with self.subTest(status=status):
                cancellation = [False]
                def run(request, **_kwargs):
                    cancellation[0] = True
                    return SubagentRunResult(
                        status=status, child_session_id=request.child_session_id,
                        summary="actual result", reason="worker_confirmed" if status != "succeeded" else "",
                    )
                registry = SubagentProviderRegistry()
                registry.register(InProcessSubagentProvider(run))
                result = registry.execute("in_process", _request(), cancelled=lambda: cancellation[0])
                self.assertEqual(result.status, status)

    def test_malformed_tool_filter_is_rejected_without_throwing(self) -> None:
        registry = SubagentProviderRegistry()
        registry.register(InProcessSubagentProvider(lambda *_args, **_kwargs: None))

        result = registry.execute(
            "in_process",
            _request(allowed_tools=("exec_run", 7)),
            cancelled=lambda: False,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "subagent_tool_filter_invalid")

    def test_empty_success_and_wrong_child_identity_are_rejected(self) -> None:
        for provider_result in (
            SubagentRunResult(
                status="succeeded",
                child_session_id=_request().child_session_id,
            ),
            SubagentRunResult(
                status="succeeded",
                child_session_id="subagent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                summary="wrong child",
            ),
        ):
            with self.subTest(provider_result=provider_result):
                registry = SubagentProviderRegistry()
                registry.register(InProcessSubagentProvider(lambda *_args, **_kwargs: provider_result))

                result = registry.execute("in_process", _request(), cancelled=lambda: False)

                self.assertEqual(result.status, "failed")
                self.assertEqual(result.reason, "subagent_result_invalid")

    def test_provider_failure_and_missing_provider_are_structured(self) -> None:
        registry = SubagentProviderRegistry()
        missing = registry.execute("in_process", _request(), cancelled=lambda: False)
        self.assertEqual(missing.reason, "subagent_provider_unavailable")

        def explode(*_args, **_kwargs):
            raise RuntimeError("secret detail")

        registry.register(InProcessSubagentProvider(explode))
        failed = registry.execute("in_process", _request(), cancelled=lambda: False)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.reason, "subagent_provider_RuntimeError")
        self.assertNotIn("secret", failed.reason)

    def test_duplicate_provider_registration_is_rejected(self) -> None:
        provider = InProcessSubagentProvider(
            lambda request, **_kwargs: SubagentRunResult(
                status="succeeded",
                child_session_id=request.child_session_id,
                summary="done",
            )
        )
        registry = SubagentProviderRegistry()
        registry.register(provider)

        with self.assertRaisesRegex(ValueError, "subagent_provider_duplicate"):
            registry.register(provider)


if __name__ == "__main__":
    unittest.main()
