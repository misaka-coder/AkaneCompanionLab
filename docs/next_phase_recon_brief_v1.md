# 下一阶段 多维勘探简报 v1（交给探路模型用）

**用途**：开新对话继续设计前，先让探路模型按本简报做**只读勘探**，把现状摸清并输出。Claude（把关方）据此挑"要升级的/要修的"，作者中间把关。这样不靠拍脑袋、不留半成品双轨。

**协作规则**：见 `docs/ai_collaboration_protocol_v1.md`（风险分工 + 简报模板）。

## 0. 背景（一句话）

Akane 工具系统已确认：native 通过 `opencode.ai` 中转 + `NATIVE_TOOL_PROVIDER_ALLOWLIST=...:json` **真跑通、人设稳**（commit 6316a7f / cf5869a）。现在要参考 Claude Code，给 **MCP/skill 铺通道无关的地基**，并顺带把 UX / 架构 / 潜在 bug 几个维度摸清。**通道(native vs tool_call)只是末端优化，不是难点。**

## 参考设计（必读，照着对比 Akane 现状）

- **Claude Code 标注源码** `F:\Akane\galgame\AkaneBrain\claude_code_annotated`：`src/tools/ToolSearchTool`（延迟工具/按需取 schema）、`src/utils/toolResultStorage.ts`（大结果落盘给句柄）、`src/query.ts`（agent loop）、`src/tools/MCPTool|SkillTool`。
- **本项目调研** `docs/claude_code_tool_system_research_v1.md`（附录 A 深挖落盘/SkillTool/MCP/延迟工具）。
- **Sakura** `F:\Temp\sakura-reference/app/agent/runtime.py`（原生工具循环、`structured_response=not bool(tool_defs)`）、`app/agent/tool_registry.py`。
- **承重不变量** `docs/engineering_invariants_v1.md`；**工具设计** `docs/tool_system_decoupling_v1.md`。

## 输出格式（每条都这样，别下结论）

> `文件:行 + 现状一句话 + 潜在问题/与参考设计的差距`
> **不下结论、不给改法——结论留给把关方用完整上下文下。** 每个维度单独成段。

---

## 维度 A —— 工具系统地基（下一步主线，优先）

围绕"Claude Code 式 通道无关地基"，定位 Akane 现状与差距：

1. **注册/分发**：`engine.py` `_build_tool_handlers` / `_resolve_tool_handlers` / `_resolve_capability_selection`、`capability_registry.py`——现在怎么决定每轮发哪些工具？是不是硬编码 per-client + 全量进 prompt？
2. **延迟工具池缺口**：有没有"只给名字、schema 按需取"的机制？(对比 Claude Code `ToolSearchTool`)。工具总数多少、全装会不会爆 prompt？
3. **结果处理**：`tool_orchestration_engine.py` `shape_tool_followup`（已有：空占位+截断）——大结果是"截断"还是"给句柄去读"？离 `toolResultStorage` 落盘句柄差多少？
4. **MCP 现状**：`tool_runtime.py` `AdapterCapabilityToolHandler` / `McpStdioToolCaller`——MCP 工具现在怎么发现/暴露/执行？schema 怎么进 prompt？
5. **可复用 vs 该简化**：`ToolMetadata`（family/operation/risk/round_budget/input_schema）、`native_tool_schema.py`、统一 `tool_invocation` 层（`_native_tool_call` 载体）——哪些通道无关可留、哪些 native 专用。
6. **provider 门**：`llm_runtime.py` `PROVIDER_TOOL_PROFILES` / `_native_tool_profile` / `NATIVE_TOOL_PROVIDER_ALLOWLIST`——硬编码 deepseek + 手填 allowlist 的繁琐点在哪？做"启动能力探针"要动哪几处？

## 维度 B —— 用户体验 / 表现到位（对照 CLAUDE.md §9）

定位"用户实际看到/听到/点到"的表现链在易露馅状态下会不会塌或打架：

1. **表现链**：`prompt_profiles.py`（输出 schema：emotion/speech/speech_segments/persona/scene/activity/state_request）、`final_output_engine.py`、流式 `_TopLevelJSONStreamTap`（llm_runtime.py）——气泡/表情/动作/TTS/音乐/兜底各从哪个字段进真实渲染。
2. **易露馅状态**：空态、慢请求、失败、**兜底("我在认真听你说")**、重复触发、切角色、切会话——哪些状态下表现层会静默降级或互相打架。
3. **工具时表现**：native 边调边说前置话（llm_runtime.py:798，仅流式；非流式 783 不贴）——前置话 / 工具进行中 / 工具结果 三段表现是否连贯、有没有假完成。
4. **兜底 UX**：掉兜底时用户看到什么、能不能更优雅（与"无 max_tokens 截断""网络抖动"挂钩）。

## 维度 C —— 产品架构 / 双轨残留（对照 [[completion-discipline]]）

定位"做了一半、活死不分"的中间态：

1. **双轨/死代码**：默认关的开关后面有没有从未上线的分支；`ENABLE_NATIVE_TOOL_DECISION` / 两个 allowlist 的关系是否清晰。
2. **stale/专精**：`config.py` `TEXT_MODEL_NAME` 默认仍是弃用的 `deepseek-chat`；provider profile 只硬编码 deepseek——还有哪些"专精 deepseek / 配置繁琐"的点。
3. **配置面**：`.env`/`config.py` 里工具/native/模型相关项是否冗余、易配错、缺默认。
4. **边界文档**：哪些"实验性/过渡态"代码没在文档里标清归宿（native 现已可用，该从"待删"改写成"已启用特性+边界"）。

## 维度 D —— 潜在 bug / 静默脆弱点

扫"不报错、只是偶尔坏"的那类（我们已抓到两颗：json_object 只对 ollama 开、无 max_tokens）：

1. **截断**：`llm_runtime.py` 全文无 `max_tokens`——长回复/长工具结果被 provider 默认上限截断的风险点。
2. **流式/非流式不一致**：`_call_json`(783) vs `stream_chat_json`(798) 对 native+speech 处理不一致——还有哪些路径只在一种模式下对。
3. **静默 fallback/吞异常**：哪些地方 parse 失败/异常被吞成兜底而不留样本（建议加兜底原始内容采样日志的落点）。
4. **provider/中转兼容**：response_format / tools / stream 在非 deepseek、非 opencode 中转下会不会静默不支持。

---

## 怎么用

- 可一次只跑一个维度（**A 优先**，是下一步主线），也可全跑。
- 探路模型输出后，作者转交把关方（Claude）→ 挑"要升级/要修"的 → 作者中间确认 → 把关方执行承重切片、机械切片可派给探路模型。
