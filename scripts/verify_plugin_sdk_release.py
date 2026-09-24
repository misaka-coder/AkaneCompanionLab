"""Build/install the public SDK and a separate plugin in a clean environment."""

from __future__ import annotations

import argparse
from email.parser import Parser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
POLICY_PRESETS = {name: json.loads((ROOT / "examples/plugins/akane_sdk_execution_policy/presets" / (name + ".json")).read_text(encoding="utf-8"))
                  for name in ("personal", "shared")}


def run(arguments, *, cwd, env):
    completed = subprocess.run(arguments, cwd=cwd, env=env, text=True, capture_output=True, timeout=300)
    if completed.returncode:
        raise RuntimeError(f"SDK verification command failed:\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capcore-wheel", type=Path, required=True)
    args = parser.parse_args()
    capcore_wheel = args.capcore_wheel.resolve(strict=True)
    with zipfile.ZipFile(capcore_wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode())
        if metadata.get("Name") != "capcore" or metadata.get("Version") != "0.1.3":
            raise ValueError("expected the CapCore 0.1.3 release wheel")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="akane-sdk-release-") as temporary:
        work = Path(temporary)
        wheelhouse = work / "wheels"
        run([sys.executable, "-m", "build", "--outdir", str(wheelhouse), str(ROOT)], cwd=work, env=env)
        sdk_wheel = next(wheelhouse.glob("akane_plugin-*.whl"))
        with zipfile.ZipFile(sdk_wheel) as archive:
            names = archive.namelist()
            if not all(name.startswith(("akane_plugin/", f"akane_plugin-{SDK_VERSION}.dist-info/")) for name in names):
                raise AssertionError("SDK wheel contains unrelated host files")
            if not {"akane_plugin/contracts.py", "akane_plugin/tools.py", "akane_plugin/events.py",
                    "akane_plugin/service_contracts.py", "akane_plugin/connections.py",
                    "akane_plugin/policies.py",
                    "akane_plugin/observations.py", "akane_plugin/turns.py", "akane_plugin/results.py", "akane_plugin/py.typed"} <= set(names):
                raise AssertionError("SDK wheel is missing its public implementation")
        with tarfile.open(next(wheelhouse.glob("akane_plugin-*.tar.gz"))) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                relative = member.name.split("/", 1)[1]
                if relative.startswith(("akane_plugin/", "akane_plugin.egg-info/")):
                    continue
                if relative not in {"pyproject.toml", "MANIFEST.in", "setup.cfg", "PKG-INFO", "LICENSE", "NOTICE"}:
                    raise AssertionError(f"SDK source archive contains unrelated file: {relative}")
        run([sys.executable, "-m", "build", "--wheel", "--outdir", str(wheelhouse),
             str(ROOT / "examples/plugins/akane_sdk_math")], cwd=work, env=env)
        plugin_wheel = next(wheelhouse.glob("akane_sdk_math_example-*.whl"))
        for project in ("akane_sdk_event_source", "akane_sdk_event_calculator", "akane_sdk_board", "akane_sdk_change_check",
                        "akane_sdk_statistics", "akane_sdk_statistics_report", "akane_sdk_http_query", "akane_sdk_batch",
                        "akane_sdk_execution_policy"):
            run([sys.executable, "-m", "build", "--wheel", "--outdir", str(wheelhouse),
                 str(ROOT / "examples/plugins" / project)], cwd=work, env=env)
        image_source = work / "image-source"
        shutil.copytree(ROOT / "plugins/akane_image_generation", image_source,
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", "dist"))
        run([sys.executable, "-m", "build", "--wheel", "--outdir", str(wheelhouse),
             str(image_source)], cwd=work, env=env)
        example_wheels = [*wheelhouse.glob("akane_sdk_event_*.whl"), *wheelhouse.glob("akane_sdk_board_*.whl"),
                          *wheelhouse.glob("akane_sdk_change_check_*.whl"), *wheelhouse.glob("akane_sdk_statistics*.whl"),
                          *wheelhouse.glob("akane_image_generation-*.whl"), *wheelhouse.glob("akane_sdk_http_query*.whl"),
                          *wheelhouse.glob("akane_sdk_batch*.whl"), *wheelhouse.glob("akane_sdk_execution_policy*.whl")]
        assert len(example_wheels) == 10
        venv.EnvBuilder(with_pip=True).create(work / "venv")
        python = work / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
             str(capcore_wheel), str(sdk_wheel), str(plugin_wheel), *(str(path) for path in example_wheels)], cwd=work, env=env)
        check = work / "check.py"
        check.write_text('''import asyncio
import importlib.util
import json
from importlib.metadata import entry_points, version
from akane_plugin import InvocationContext, Plugin, EventReceipt, ObservationReceipt, TurnReceipt, ToolContext, Result
from capcore import build_tool_spec, validate_tool_spec_args

assert importlib.util.find_spec("companion_v01") is None
assert version("akane-plugin") == "SDK_VERSION_PLACEHOLDER"
assert version("capcore") == "0.1.3"
factory = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "example.math").load()
plugin = factory()
assert isinstance(plugin, Plugin)
class Registrar:
    def add_capability_adapter(self, adapter): self.adapter = adapter
    def get_capability_port(self): return None
    def get_events_port(self): return None
    def get_resource_port(self): return None
    def get_connection_port(self): return None
    def add_event_subscription(self, subscription, handler):
        self.subscriptions = getattr(self, "subscriptions", []) + [subscription]
registrar = Registrar()
plugin.register(registrar)
async def verify():
    policy_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "example.execution-policy").load()()
    assert set(policy_plugin.manifest.permissions) == {"policy.provide", "service.provide"}
    policy_registrar = Registrar()
    policy_plugin.register(policy_registrar)
    descriptors = await policy_registrar.adapter.list_capabilities()
    descriptor = next(item for item in descriptors if item.id.endswith(".before"))
    transform_descriptor = next(item for item in descriptors if item.id.endswith(".transform"))
    present_descriptor = next(item for item in descriptors if item.id.endswith(".present"))
    wrap_descriptor = next(item for item in descriptors if item.id.endswith(".wrap"))
    assert len(descriptors) == 4 and all(not item.prompt_exposed for item in descriptors)
    request = {"tool_id": "example.math.add", "arguments": {"a": 41}, "invocation_id": "one",
               "executor": "server_local", "effects": [], "risk": "low", "client_mode": "web", "options": {}}
    allowed = await policy_registrar.adapter.invoke(descriptor.id, request, InvocationContext())
    assert allowed.content == {"decision": "allow"}
    request["options"] = {"blocked_tools": ["example.math.add"]}
    blocked = await policy_registrar.adapter.invoke(descriptor.id, request, InvocationContext())
    assert blocked.content == {"decision": "deny", "reason": "deployment_effect_disabled"}
    presets = POLICY_PRESETS_PLACEHOLDER
    request["effects"] = ["network"]
    for name, expected in (("personal", "allow"), ("shared", "deny")):
        request["options"] = presets[name][0]["options"]
        result = await policy_registrar.adapter.invoke(descriptor.id, request, InvocationContext())
        assert result.content["decision"] == expected
    request["outcome"] = {"status": "ok", "reason": "", "value": 42}
    request["options"] = {"numeric_factors": {"example.math.add": 2}}
    transformed = await policy_registrar.adapter.invoke(transform_descriptor.id, request, InvocationContext())
    assert transformed.content == {"action": "replace", "value": 84}
    request["options"] = {}
    unchanged = await policy_registrar.adapter.invoke(transform_descriptor.id, request, InvocationContext())
    assert unchanged.content == {"action": "keep"}
    request["rendered"] = "producer text"
    request["options"] = {"display_prefix": "display: "}
    presented = await policy_registrar.adapter.invoke(present_descriptor.id, request, InvocationContext())
    assert presented.content == {"action": "replace", "text": "display: producer text"}
    wrap_request = {"tool_id": "example.math.add", "arguments": {"a": 41}, "invocation_id": "one",
                    "executor": "server_local", "effects": [], "risk": "low", "client_mode": "web",
                    "options": {"max_attempts": 2, "retry_statuses": ["temporary_failure"]},
                    "phase": "before", "attempt": 1}
    wrapped_before = await policy_registrar.adapter.invoke(wrap_descriptor.id, wrap_request, InvocationContext())
    assert wrapped_before.content == {"action": "continue", "max_attempts": 2}
    wrap_request.update({"phase": "after", "outcome": {"status": "temporary_failure", "reason": "busy"}})
    wrapped_after = await policy_registrar.adapter.invoke(wrap_descriptor.id, wrap_request, InvocationContext())
    assert wrapped_after.content == {"action": "retry"}
    await policy_registrar.adapter.aclose()
    from akane_plugin import Tools, Services, ToolCallError
    class BudgetPort:
        async def call_result(self, target, arguments):
            assert target == {"operation": "resource_budget"} and arguments == {}
            return Result(value={"used_dependency_calls": 0})
    assert await Tools(BudgetPort()).budget() == {"used_dependency_calls": 0}
    assert await Services(BudgetPort()).budget() == {"used_dependency_calls": 0}
    try:
        await Tools(None).budget()
    except ToolCallError as exc:
        assert exc.result.reason == "capability_invoke_permission_required"
    else:
        raise AssertionError("unbound budget must fail")
    batch_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "example.batch").load()()
    batch_registrar = Registrar()
    batch_plugin.register(batch_registrar)
    measured = await batch_registrar.adapter.invoke("example.batch.measure", {"text": "hello"}, InvocationContext())
    assert not measured.is_error and measured.content == 5
    await batch_registrar.adapter.aclose()
    adapter = registrar.adapter
    descriptor, = await adapter.list_capabilities()
    spec = build_tool_spec(descriptor)
    assert spec.input_schema["required"] == ["a"]
    assert validate_tool_spec_args(spec, {"a": 41}).ok
    assert not validate_tool_spec_args(spec, {"a": "41"}).ok
    result = await adapter.invoke(descriptor.id, {"a": 41}, InvocationContext())
    assert not result.is_error and result.content == 42
    await adapter.aclose()
    query_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "example.http-query").load()()
    from akane_plugin import ConnectionSpec, PluginConnectionResult
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            body = b'{"rows":[1,2,3]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    class Port:
        async def resolve(self, name):
            assert name == "query_api"
            return PluginConnectionResult(True, "configured", options={
                "endpoint": f"http://127.0.0.1:{server.server_port}/query", "credential": "clean-venv-test", "timeout_seconds": 5},
                private_option_fields=("credential",))
    class QueryRegistrar(Registrar):
        def get_connection_port(self): return Port()
    query_registrar = QueryRegistrar()
    query_plugin.register(query_registrar)
    try:
        spec, = query_plugin.manifest.connections
        assert ConnectionSpec.from_dict(spec.as_dict()) == spec
        assert spec.private_fields == ("credential",)
        query = await query_registrar.adapter.invoke("example.http-query.query", {"text": "hello"}, InvocationContext())
        assert not query.is_error and query.content == {"rows": [1, 2, 3]}
        assert received == ["Bearer clean-venv-test"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    image_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "akane.image-generation").load()()
    image_registrar = Registrar()
    image_plugin.register(image_registrar)
    image_methods = {method.id: method for method in await image_registrar.adapter.list_capabilities()}
    image_service_id = "akane.image-generation.service.image_generation.v1.generate"
    assert set(image_methods) == {"akane.image-generation.run.v1", image_service_id}
    assert image_methods["akane.image-generation.run.v1"].prompt_exposed
    assert not image_methods[image_service_id].prompt_exposed
    assert image_methods[image_service_id].raw["service"] == {"service_id": "image_generation", "version": 1, "method": "generate"}
    assert build_tool_spec(image_methods[image_service_id]).input_schema["required"] == ["prompt"]
    assert build_tool_spec(image_methods[image_service_id]).output_schema["properties"]["output_count"]["type"] == "integer"
    assert [(item.service_id, item.version) for item in image_plugin.manifest.requires_services] == [("image_generation", 1)]
    unbound_image = await image_registrar.adapter.invoke("akane.image-generation.run.v1", {"prompt": "test"}, InvocationContext())
    assert unbound_image.is_error and unbound_image.reason == "capability_invoke_permission_required"
    await image_registrar.adapter.aclose()
    service_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "example.statistics").load()()
    assert service_plugin.manifest.permissions == ("service.provide",)
    service_registrar = Registrar()
    service_plugin.register(service_registrar)
    method, = await service_registrar.adapter.list_capabilities()
    assert not method.prompt_exposed
    assert method.raw["service"] == {"service_id": "statistics", "version": 1, "method": "summarize"}
    service_result = await service_registrar.adapter.invoke(method.id, {"values": [1.0, 2.0, 6.0]}, InvocationContext())
    assert not service_result.is_error
    assert service_result.content == {"count": 3.0, "mean": 3.0, "median": 2.0, "minimum": 1.0, "maximum": 6.0}
    await service_registrar.adapter.aclose()
    consumer_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "example.statistics-report").load()()
    assert [(item.service_id, item.version) for item in consumer_plugin.manifest.requires_services] == [("statistics", 1)]
    consumer_registrar = Registrar()
    consumer_plugin.register(consumer_registrar)
    methods = {method.id: method for method in await consumer_registrar.adapter.list_capabilities()}
    assert set(methods) == {"example.statistics-report.summarize", "example.statistics-report.describe_service"}
    assert all(method.prompt_exposed for method in methods.values())
    discovery = await consumer_registrar.adapter.invoke("example.statistics-report.describe_service", {}, InvocationContext())
    assert discovery.is_error and discovery.reason == "capability_invoke_permission_required"
    await consumer_registrar.adapter.aclose()
    change_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == "example.change-check").load()()
    change_registrar = Registrar()
    change_plugin.register(change_registrar)
    for descriptor in await change_registrar.adapter.list_capabilities():
        assert descriptor.raw["followup"] == "required"
        first = await change_registrar.adapter.invoke(descriptor.id, {"content": "actual content"}, InvocationContext())
        assert isinstance(first, Result) and first.followup == "required" and first.value["changed"] is True
        unchanged = await change_registrar.adapter.invoke(descriptor.id,
            {"content": "actual content", "previous_digest": first.value["digest"]}, InvocationContext())
        assert unchanged.followup == "none" and unchanged.value["changed"] is False
        modified = await change_registrar.adapter.invoke(descriptor.id,
            {"content": "actual content!", "previous_digest": first.value["digest"]}, InvocationContext())
        assert modified.followup == "required" and modified.value["changed"] is True
    await change_registrar.adapter.aclose()
    observation = Plugin("example.observation", permissions=("context.observe",))
    @observation.tool
    async def update(data: int, ctx: ToolContext) -> ObservationReceipt:
        return await ctx.observe("board", data)
    observation_registrar = Registrar()
    observation.register(observation_registrar)
    descriptor, = await observation_registrar.adapter.list_capabilities()
    assert build_tool_spec(descriptor).output_schema == ObservationReceipt.json_schema()
    result = await observation_registrar.adapter.invoke(descriptor.id, {"data": 0}, InvocationContext())
    assert result.is_error and result.reason == "context_observe_permission_required"
    await observation_registrar.adapter.aclose()
    for plugin_id in ("example.event-source", "example.event-calculator", "example.board"):
        event_plugin = next(e for e in entry_points(group="akane.plugins.v1") if e.name == plugin_id).load()()
        event_registrar = Registrar()
        event_plugin.register(event_registrar)
        assert len(event_registrar.subscriptions) == 1
        assert "event.emit" in event_plugin.manifest.permissions
        for descriptor in await event_registrar.adapter.list_capabilities():
            spec = build_tool_spec(descriptor)
            if descriptor.id.endswith("publish_numbers"):
                assert spec.output_schema == EventReceipt.json_schema()
            if descriptor.id == "example.board.decide":
                assert spec.output_schema == TurnReceipt.json_schema()
                result = await event_registrar.adapter.invoke(descriptor.id, {"version": 1}, InvocationContext())
                assert result.is_error and result.reason == "agent_turn_request_permission_required"
        await event_registrar.adapter.aclose()
asyncio.run(verify())
print(json.dumps({"sdk": version("akane-plugin"), "capcore": version("capcore"),
                  "host_importable": False, "installed_plugin_result": 42, "result_followup": "verified"}))
'''.replace("SDK_VERSION_PLACEHOLDER", SDK_VERSION).replace("POLICY_PRESETS_PLACEHOLDER", repr(POLICY_PRESETS)), encoding="utf-8")
        print(run([str(python), "-I", str(check)], cwd=work, env=env).strip())
        print(json.dumps({"wheel_and_sdist_scope": "verified", "clean_environment": "passed"}))


if __name__ == "__main__":
    main()
