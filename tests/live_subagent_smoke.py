"""Opt-in external-model smoke: python -m tests.live_subagent_smoke --live.

Uses the configured LLM transport and real local file tools, Broker, MemCore
projection and Host Job. Identity/delivery are isolated test fixtures; embeddings
are a local stub (retrieval is not exercised). No QQ/desktop delivery is made.
"""
import argparse
import json
import time

import config
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.bot_runtime import _host_job_completion_request
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_subagent_jobs import HostSubagentJobRuntime
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.llm_runtime import LLMRuntime
from companion_v01.project_workspace import ProjectWorkspaceService
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.store import MemoryStore
from companion_v01.subagent_engine import model_route_fingerprint
from companion_v01.subagent_runtime import InProcessSubagentProvider, SubagentProviderRegistry
from companion_v01.tool_handlers.project_workspace import ProjectInspectToolHandler, WorkspaceWriteToolHandler
from tests.test_subagent_engine import SubagentEngineTests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    settings = BotSettingsView.from_config(config)
    fixture = SubagentEngineTests()
    fixture.setUp()
    try:
        engine, root = fixture.engine, fixture.root
        engine.llm = LLMRuntime(settings=settings, log_dir=root / "llm-logs")
        # Bound only this smoke, not the production task policy.
        engine._max_tool_rounds = lambda: 8
        target = engine.llm.resolve_turn_execution_target(has_real_images=False)
        store = MemoryStore(root / "memory")
        service = ProjectWorkspaceService(store=store, execution_workspace_root=root / "execution")
        executor = TrustedLocalExecutor(workspace_root=root / "execution", run_log_dir=root / "runlogs")
        project = root / "sample"
        project.mkdir()
        (project / "source.py").write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")
        engine.store, engine.execution_provider = store, executor
        engine.tool_handlers = {
            "project_inspect": ProjectInspectToolHandler(service=service, execution_provider=executor),
            "workspace_write": WorkspaceWriteToolHandler(service=service, execution_provider=executor),
        }
        observed_calls = []
        execute = engine._execute_tool_call_with_hooks
        def observe(**kwargs):
            call = kwargs["call"]
            observed_calls.append({key: call.get(key) for key in ("type", "action", "path")})
            return execute(**kwargs)
        engine._execute_tool_call_with_hooks = observe
        background = BackgroundTaskRunner({"subagents": 1})
        fixture.addCleanup(background.close)
        jobs = HostJobStore(root / "jobs.db")
        delivered = []
        completions = HostToolJobRuntime(engine=engine, store=jobs, background_tasks=background,
            terminal_callback=lambda job: delivered.append(_host_job_completion_request(job)) or True)
        registry = SubagentProviderRegistry()
        registry.register(InProcessSubagentProvider(fixture.driver))
        runtime = HostSubagentJobRuntime(store=jobs, providers=registry, provider_name="in_process",
            background_tasks=background, completion_publisher=completions.publish_completion)
        fixture.addCleanup(runtime.request_shutdown)
        owner = HostJobOwner("alice", "parent")
        started_at = time.monotonic()
        started = runtime.submit(owner=owner, task=(
            "Work only inside the supplied sample directory. Read source.py with project_inspect, "
            "then write audit.md with workspace_write. The report must describe what total does "
            "and its result for an empty list. Read audit.md back to verify the write. "
            "Do not claim tests were executed: you have only file tools. Return a short summary."),
            working_directory=str(project), allowed_tools=tuple(engine.tool_handlers),
            model=target.model, reasoning_effort=engine.llm._configured_reasoning_effort(target.bundle),
            execution_context={"client_mode": "desktop_pet", "model_role": target.role,
                "thinking_mode": settings.llm_thinking_mode, "route_fingerprint": model_route_fingerprint(target)},
            conversation_ref="isolated-smoke-parent", channel="desktop_pet", tool_call_id="live-smoke")
        if not started.get("ok"):
            print(json.dumps({"status": "failed", "reason": started.get("reason")}, ensure_ascii=False))
            return 1
        print(json.dumps({"status": "accepted", "model": target.model,
                          "admission_seconds": round(time.monotonic() - started_at, 3)}), flush=True)
        if not background.wait_idle(lane="subagents", timeout=240):
            runtime.request_shutdown()
            print('{"status":"failed","reason":"smoke_timeout_cancellation_requested"}')
            return 1
        background.wait_idle(lane="host-job-completions", timeout=5)
        job = jobs.get(started["job_id"], owner=owner)
        report = project / "audit.md"
        report_text = report.read_text(encoding="utf-8") if report.exists() else ""
        sequence = [(call["type"], call["path"]) for call in observed_calls]
        expected = [("project_inspect", "source.py"), ("workspace_write", "audit.md"),
                    ("project_inspect", "audit.md")]
        passed = (job.status == "succeeded" and bool(report_text.strip()) and len(delivered) == 1
                  and sequence == expected)
        print(json.dumps({"status": "passed" if passed else "failed", "job_status": job.status,
            "reason": job.last_error, "summary": job.result_summary, "report": report_text,
            "completion_events": len(delivered), "seconds": round(time.monotonic() - started_at, 2),
            "observed_tools": observed_calls,
            "real_channel_delivery": False, "retrieval_tested": False}, ensure_ascii=False))
        return 0 if passed else 1
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    raise SystemExit(main())
