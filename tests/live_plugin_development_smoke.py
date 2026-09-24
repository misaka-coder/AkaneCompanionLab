"""Opt-in real configured-model acceptance, in a fresh isolated host instance.

Run explicitly: python -m tests.live_plugin_development_smoke --work-dir <new dir>
Uses the currently configured chat connection; never prints or copies credentials.
The model creates every plugin source/test file with existing workspace tools.
No mocked provider response, prebuilt plugin, direct publish, or plugin handler
injection is used. This is an acceptance driver, not a second installation API.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import traceback
from pathlib import Path
from unittest.mock import patch

import config
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from tests.tool_exposure_development_harness import DevelopmentHarness

REQUEST = """请从零开发并安装一个 Akane 插件，统计整数列表的 count、sum 和 squares（平方和）。
插件 ID 使用 example.live-statistics，函数名 summarize，参数名 values，返回上述三个整数键。
请先阅读现有 plugin-development Skill 和当前公开 SDK 样例文档，用现有工作区工具在新建独立项目里编写源码与普通 unittest。
测试至少覆盖 [3,4]、空列表和负数；经正式插件管理工具检查、暂存、按返回的完整权限安装并确认 active。
安装后在当前会话读取新能力通知，直接加载该精确工具 ID 的契约，经固定调用入口真正计算 [3,4] 并报告实际结果。
新插件应即时可用，不重启、不另开会话、不改宿主配置或内部文件、不使用命令行自行安装。无需联网和第三方依赖。
源码和测试文件请直接用 workspace_write/workspace_patch 创建，工作区工具会创建父目录；检查用 test_source，无需调用 Shell 创建目录或安装依赖。
本次只在验收临时工作区开发，不修改已有项目。若产生精确审批，我会随后批准并发送续接消息；依据真实工具失败修正，不模拟成功。"""


async def run(h,settings,max_rounds):
    await h.start_development()
    report = {"kind":"real_model","model":settings.chat_model_name,"status":"incomplete","rounds":0,
        "controlled_provider_responses":False,"preinstalled_plugin":False,"manual_plugin_handler_injection":False}
    try:
        h.configure_live(settings)
        h.begin(1,REQUEST)
        baseline = None
        user_turn = 1
        report["owner_continuations"] = 0
        report["model_final_replies"] = []
        cap = "example.live-statistics.summarize"
        installed_wire_index = None
        load_wire_index = None
        async def loop(until_native=False):
            nonlocal baseline,user_turn,installed_wire_index,load_wire_index
            for _ in range(max_rounds):
                prepared,response = await asyncio.to_thread(h.live_request)
                report["rounds"] += 1
                if baseline is None:
                    baseline = prepared
                if not until_native:
                    h.assertEqual(prepared["tool_exposure_lifecycle"]["compaction_generation"],
                                  baseline["tool_exposure_lifecycle"]["compaction_generation"])
                    h.assertEqual(h.requests[0]["tools"],h.requests[-1]["tools"])
                    if installed_wire_index is not None:
                        h.compare("development-live","official-install-notice-tail",
                            h.requests[installed_wire_index],h.requests[-1],history_prefix=True)
                        installed_wire_index = None
                outcomes = await asyncio.to_thread(h.execute_model_response)
                for row in h.trace[-len(outcomes):] if outcomes else []:
                    if row["tool"]=="manage_extension" and row["arguments"].get("action")=="install" and '"status":"active"' in row["result"]:
                        installed_wire_index = len(h.requests)-1
                    if row["tool"]=="capability_load" and cap in row["arguments"].get("capability_ids",[]):
                        load_wire_index = len(h.requests)-1
                h.write_trace(h.root/"model-trace.json")
                print(json.dumps({"round":report["rounds"],"tools":[row["tool"] for row in h.trace[-len(outcomes):]] if outcomes else [],
                    "statuses":[[{"type":event.get("type"),"status":event.get("status"),"reason":event.get("reason")} for event in result.stream_events] for result in outcomes]},ensure_ascii=False),flush=True)
                if h.approve_pending_test_requests():
                    h.approval_notice()
                if not outcomes:
                    report["model_final_replies"].append(response.parsed.get("speech", ""))
                    expected = {"count":2,"sum":3,"squares":29} if until_native else {"count":2,"sum":7,"squares":25}
                    complete = any(row["tool"]==cap and row["value"]==expected and
                        ((row["model_name"]!="capability_invoke") if until_native else row["model_name"]=="capability_invoke") for row in h.trace)
                    if complete:
                        return
                    if report["owner_continuations"] >= 3:
                        raise AssertionError("model_stopped_without_required_execution")
                    # A real test-user continuation, with no code, target args,
                    # or fabricated tool feedback. Keep the same conversation.
                    h.finish_model()
                    user_turn += 1
                    report["owner_continuations"] += 1
                    h.begin(user_turn,"请继续完成刚才的插件开发、检查、正式安装和真实调用任务；若尚未完成，请实际使用工具推进，依据真实结果修正，直到取得要求的计算结果。")
            raise AssertionError("model_round_limit_reached_without_final_reply")
        with h.engine.plugin_capability_source.turn_scope():
            await loop()
            tools = [row["tool"] for row in h.trace]
            h.assertIn("load_skill",tools)
            h.assertTrue(any(row["tool"]=="project_inspect" and row["arguments"].get("cwd")=="alias:akane-sdk" for row in h.trace))
            h.assertIn("workspace_write",tools)
            stages = [row for row in h.trace if row["tool"]=="manage_extension"]
            for action in ("test_source","stage_source","install"):
                h.assertTrue(any(row["arguments"].get("action")==action and '"ok":true' in row["result"] for row in stages),action)
            h.assertTrue(any(row["arguments"].get("action")=="install" and '"status":"active"' in row["result"] for row in stages))
            h.assertIn(cap,h.runtime.capability_ids)
            invokes = [row for row in h.trace if row["tool"]==cap and row["model_name"]=="capability_invoke"]
            h.assertTrue(invokes,"Model never used the fixed invocation entry")
            h.assertTrue(any(row["value"]=={"count":2,"sum":7,"squares":25} for row in invokes))
            h.assertTrue(any(row["tool"]=="capability_load" and cap in row["arguments"].get("capability_ids",[]) for row in h.trace))
            h.assertTrue(any(cap in repr(request["messages"]) for request in h.requests))
            h.compare("development-live","same-session-created-installed-used",h.requests[0],h.requests[-1])
            h.evidence[-1]["expected_change"] = "workspace_selection_changed_environment_context"
            h.assertIsNotNone(load_wire_index)
            h.compare("development-live","load-invoke-tail",h.requests[load_wire_index],h.requests[-1],history_prefix=True)
            report["development_prefix_scope"] = "tools/system stable; workspace selection is an independent environment change"
            report["development_install_load_invoke"] = "passed"
            report["initial_generation"] = baseline["tool_exposure_lifecycle"]["compaction_generation"]
            h.finish_model()
            preference = h.select_resident(cap)
            row = next(row for row in preference["state"]["tools"] if row["id"]==cap)
            h.assertTrue(row["pending"],row)
            before = h.requests[-1]
            user_turn += 1
            h.begin(user_turn,"我已在控制中心把新工具选为常驻。请直接调用本轮原生 summarize 工具计算 [5,-2]，报告实际结果。")
            count = len(h.trace)
            await loop(until_native=True)
            final = h.last_prepared
            h.assertEqual(final["tool_exposure_lifecycle"]["compaction_generation"],report["initial_generation"])
            h.assertIn(cap,final[TOOL_CAPABILITY_SELECTION_FIELD].schema_tool_names)
            native = [row for row in h.trace[count:] if row["tool"]==cap and row["model_name"]!="capability_invoke"]
            h.assertTrue(native,"Model did not call the new native declaration")
            h.assertTrue(any(row["value"]=={"count":2,"sum":3,"squares":29} for row in native))
            h.compare("development-live","resident-next-request",before,h.requests[-1],tools_equal=False)
            report["resident_next_request_native"] = "passed"
            report["final_generation"] = final["tool_exposure_lifecycle"]["compaction_generation"]
            report["compaction_performed"] = False
            report["status"] = "passed"
            h.finish_model()
        return report
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        # Detailed generated-only traces stay in the local acceptance directory.
        # Do not include connection URLs, keys or unsanitized provider exceptions.
        report["reason"] = "acceptance_assertion_failed" if isinstance(exc,AssertionError) else "acceptance_runtime_failed"
        last=traceback.extract_tb(exc.__traceback__)[-1]
        report["failure_location"] = {"file":Path(last.filename).name,"line":last.lineno}
        raise
    finally:
        report["provider_reported_metrics"] = h.engine.llm.snapshot_metrics()
        for comparison in h.evidence:
            comparison["online_cache_metrics"] = "provider_reported_aggregate_separately"
        report["wire_comparisons"] = h.evidence
        h.write_trace(h.root/"model-trace.json")
        (h.root/"wire-requests.json").write_text(json.dumps(h.requests,ensure_ascii=False,indent=2),encoding="utf-8")
        (h.root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        await h.stop_development()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir",required=True,help="New directory; must not already exist")
    parser.add_argument("--max-rounds",type=int,default=48)
    args=parser.parse_args()
    settings=BotSettingsView.from_config(config)
    if not settings.chat_api_key or not settings.chat_base_url or not settings.chat_model_name:
        print('{"status":"not_run","reason":"configured_chat_model_unavailable"}')
        return 2
    h=DevelopmentHarness()
    h.work_root=Path(args.work_dir)
    h.setUp()
    try:
        # Prevent incidental token-pressure compaction while the new tool is
        # being installed; completed history is compacted explicitly once below.
        with patch.object(config,"LLM_AUTO_COMPACT_TOKEN_LIMIT",0):
            report=asyncio.run(run(h,settings,args.max_rounds))
        print(json.dumps(report,ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status":"incomplete","error_type":type(exc).__name__}))
        return 1
    finally:
        h.doCleanups()


if __name__ == "__main__":
    raise SystemExit(main())
