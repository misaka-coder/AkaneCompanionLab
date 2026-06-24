# 工程承重约束与子系统地图 v1

> **这份文档给谁看**：任何（人或 AI）准备改后端核心链路（对话回合、工具调用、提示词、输出归一化、能力门控、workspace）之前，先读这一篇。
>
> 它不替代各功能的详细设计文档（见文末「详细文档索引」），而是把**散落在脑子里、违反就会出事的承重约束**集中写下来。`docs/akane_project_manifesto.md` 讲"为什么做"（哲学），这篇讲"碰哪些地方会塌"（工程不变量）。
>
> 维护规则：发现新的承重约束或踩坑就补进来；写错比不写更糟，**只写你已在代码里验证过的事实**，拿不准的标注「待证」。

---

## 一、最高优先级不变量（违反 = 体验或主流程崩坏）

### INV-1 表达层不可丢（这是真不变量）
- **约束**：桌宠/QQ 的**角色表现**——情绪 `emotion`、说话 `speech/speech_segments`、音乐控制 `activity`、人设 `persona`、好感度信号 `state_request`、场景立绘 `scene/character`——必须以结构化形式产出并进入真实渲染/播放链路（见 `companion_v01/final_output_engine.py::normalize_final_output`，字段契约见 `persona_profiles.toml`）。
- **为什么承重**：这层是产品的灵魂。任何改动只要让"表情/音乐/人设/好感度"退化成纯文本或默认值，就是砍掉了陪伴体验。
- **❌ 真正的禁止项**：**不准通过"关掉最终表现 JSON"来实现 native 工具调用**。曾在 `feature/monogatari-web` 上踩坑：一上 native tools 就把 json_mode 关了，导致模型只吐纯文本 speech，表情/音乐/人设/好感度全丢，已整体回退。
- **✅ 允许且更优**：把"动作决策（工具）"与"角色表现"**解耦成两层协议**——工具走独立通道（native 或内部 ToolInvocation），表现 JSON 留在工具结果回来后的最终回复轮产出。"工具拿出去，表达留下"。**这不违反 INV-1，反而能同时提升工具稳定性和表现质量。** 设计与迁移见 `docs/tool_system_decoupling_v1.md`。
- 注意区分两个命题：①"别弄丢表达层"=真不变量；②"工具必须永远当 JSON 里的一个字段"≠不变量（那只是当前实现，正在按解耦方案改）。

### INV-2 工具调用走"提出→校验→权限→执行→喂回"统一管线，一轮一个，多步靠多轮
- **约束**：一轮最多一个工具；多步靠多轮循环，预算 `MAX_TOOL_ROUNDS`（默认 3，clamp 1–5；web_research 8、browser 10，见 `config.py`）。
- **当前实现**：legacy provider/model 仍可用最终 JSON 的 `tool_call` 字段提出工具调用；已验证 native `web_search` 则走内部 `_native_tool_call` 载体，engine 消费后移除，公开 payload 不泄漏 `_tool_*` metadata。`tool_call` 仍是过渡兼容入口，正按 `docs/tool_system_decoupling_v1.md` 解耦为独立工具通道。
- **不变的是管线契约**：无论工具调用来自旧 JSON 还是 native，都要归一成内部 `ToolInvocation` → schema 校验 → 能力/权限检查 → `execute` → `ToolResult` 喂回模型。**改这条管线改共享辅助，别只改一条循环。**
- **入口**：`engine.py::process_turn`（非流式）与 `process_turn_stream`（流式）。两者的回合体已抽成共享辅助：`_prepare_tool_round_decision`（promote→normalize→分类拒绝）、`_record_tool_call_rejection`、工具执行。**改回合逻辑时改共享辅助，不要只改一条循环**（历史上这两条是复制粘贴，极易改出分歧）。

### INV-3 工具失败必须**结构化反馈**，不准静默丢
- **约束**：模型调了不存在/参数不合法的工具时，不能 `return None` 然后悄悄当作"没调工具"。要把"为什么没成 + 本轮可用工具是哪些"喂回模型，预算内让它重试。
- **实现参考**：`tool_orchestration_engine.py::classify_tool_call_rejection` + `engine.py::_record_tool_call_rejection`，并 `logger.warning("tool_call_rejected ...")`。这是全库处理"失败"的范式，呼应 CLAUDE.md §4。
- **延伸**：库里其他"出错没人知道"的地方应按同样范式改造。

### INV-4 缓存块只放**稳定内容**；易变状态进动态 user_prompt
- **约束**：`prompt_builder.py` 里 `system_extra_blocks`（= 会被 prompt 缓存的块）只放稳定内容：视觉资源、语义记忆、阶段摘要。
- **易变状态**（附件焦点、任务工作区、未总结原始消息、当前用户消息）一律进 **`user_prompt`（每轮重建，不缓存）**。
- **为什么承重**：把易变状态放进缓存块，会出现"清理了/改了但 TTL 内还看得见"的幽灵 bug。已验证当前附件焦点走的是动态 user_prompt（`prompt_builder.py` 里以 `user.extra_context` 落在 user_prompt），清理下一轮即生效。

### INV-5 能力门控**每轮**决定本轮可用工具
- **约束**：本轮有哪些工具，由 `engine.py::_resolve_tool_handlers` → `CapabilityRegistry.select` 按 client 模式/profile/session 动态决定。一个"接上了"的工具，若本轮没被选进 handler 集合，**模型就调不到**。
- **MCP 工具**：`promptExposed` 控制是否进提示词，**默认 False（opt-in）**（`local_capability_config.py`）。不要随手把默认翻成 True——会让所有 MCP 工具灌满提示词。
- 详见 `docs/capability_registry_v1.md`、`docs/client_mode_capability_boundaries_v1.md`。

### INV-6 路径/密钥/绝对路径不进 snapshot / 日志 / prompt
- 见 CLAUDE.md §3。角色包读出的相对路径必须走 `safe_child_path`；Rust 写角色包用临时文件再 rename。

### INV-7 层级边界
- UI 不直连后端/Tauri；后端不执行桌面端动作；桌面动作由前端执行（CLAUDE.md §3）。
- 设置窗口唯一实现是 `desktop_pet_next` 的 `control-center-lab.html`；`settings.html` 只是兼容跳转页。

---

## 二、子系统地图（一行一个 + 关键文件 + 详细文档）

| 子系统 | 职责 | 关键文件 | 详细文档 |
|--------|------|----------|----------|
| 对话回合编排 | 单轮对话的检索→生成→工具循环→收尾 | `engine.py`（`process_turn` / `process_turn_stream` + 共享回合辅助） | `character_engine_blueprint_v1.md` |
| LLM 运行时 | 流式/非流式 JSON 生成、缓存观测、JSON 容错 | `llm_runtime.py`；Anthropic 兼容垫片 `services/llm_client.py` | — |
| 工具运行时 | 40 个工具 handler（`build_prompt_instruction`/`normalize_call`/`execute` 契约） | `tool_runtime.py` | — |
| 工具编排 | 归一化、拒绝分类、轮次预算、QQ 媒体委派 | `tool_orchestration_engine.py` | — |
| 输出归一化 | 把模型 JSON 落成 speech/emotion/activity/persona/… | `final_output_engine.py` | `akane_multimode_output_profiles_v1.md` |
| 提示词构建 | 拼 system/user prompt、缓存分层、审计分段 | `prompt_builder.py` | — |
| 能力系统 | 按模式/profile 选工具；MCP 适配器 | `local_capability_config.py`、`capability_*` | `capability_registry_v1.md`、`capability_adapter_v1.md` |
| 客户端协议 | 模式（DESKTOP_PET / QQ_TEXT）与能力（AUDIO_PLAYBACK…） | `client_protocol.py` | `client-mode-architecture.md` |
| 存储 | SQLite 落地：消息时间线、附件、任务、生成文件 | `store.py` | — |

### 四套"工作区/文件"概念——别搞混（这是公认的易混区）
| 概念 | 是什么 | 模块 | 状态/要点 |
|------|--------|------|-----------|
| 附件收件箱 attachment_inbox | **用户发来的**材料（图片/文件），异步观察成摘要 | `attachment_inbox.py` | `ready/pending_observation/failed/cleared`；`focus_rank>0` 才进提示词；`clear` 置 `cleared` |
| 任务工作区 task_workspace | 多步任务**白板**（步骤/产物登记/后台交接） | `task_workspace.py`、`task_workspace_engine.py` | `running/waiting_user/queued` 才进提示词；cleanup 置 `cleaned`（不删） |
| 生成文件 generated_files | 桌宠/工具**生成的产物**（compose 等，gen_001…），可交付 | `generated_files*.py` | 有交付状态 delivery |
| 工作区文件 workspace_files | 工作目录里**真实磁盘文件**的读取/浏览层（仅 DESKTOP_PET） | `workspace_files.py` | 路径必须经安全校验 |

> 这四套是"长得像、各管一摊"，不是冗余。改任意一套的清理/可见逻辑前，先确认**它注入提示词的状态过滤**（参 INV-4）和**前端展示的来源**是否一致。

---

## 三、"动这块之前先知道这件事"速查

- 改**工具调用** → 先读 INV-1/2/3；改回合逻辑改**共享辅助**别只改一条循环。
- 改**提示词/上下文** → 先读 INV-4；问自己"这是稳定的还是易变的"，决定进缓存块还是 user_prompt。
- 加**新工具** → 实现 `BaseToolHandler` 三件套；确认它会被 `_resolve_tool_handlers` 在目标模式选中（INV-5）。
- 改**输出字段** → 同时改 `persona_profiles.toml` 的字段契约 + `final_output_engine.py` 的归一化，否则字段会被丢。
- 改**清理/可见性** → 确认后端状态过滤与前端展示来源一致；"还看得见"常是对话延续或前端没刷新，不一定是后端不同步（已验证 attachment/task 的清理在后端是一致的）。

---

## 四、已知的结构压力（是"已知"，不要顺手乱治）

这些是公认的成长痛，正在分阶段处理，**别在不理解全局时擅自重构**：

1. **`engine.py` 是上帝类**（数千行，什么都管）。方向是逐步把"回合编排"等抽出去。流式/非流式两条循环的回合体已抽成共享辅助（第一步已做）。
2. **大 JSON 把表达层全焊在一起**（INV-1）。是有代价的设计，但在找到"表情/动作能与工具调用共存"的方案前，不要拆。
3. **静默漏斗**：工具拒绝已结构化，其他地方待逐个排查（INV-3）。
4. **control-center-lab 是 mock 骨架 + 真实数据逐行打补丁的原型**。`control-center/data-sources.js` 的 backend 快照仍以 `...mockData` 为基底，`data-adapter.js::patchRowsByLabel` 按行标签把真实值覆盖上去；**后端没提供的行会保留 mock**，即便 `sourceKind=backend / fallbackReason=null`。这是渐进接入中的有意脚手架，别当 bug 修（别直接删 `...mockData`，面板依赖这套骨架结构）。真正收口 = 逐面板接真数据，或在 UI 标出仍是 mock 的行。

---

## 详细文档索引（按主题）
- 哲学/自述：`akane_project_manifesto.md`
- 角色引擎：`character_engine_blueprint_v1.md`
- 能力系统：`capability_registry_v1.md`、`capability_adapter_v1.md`、`client_mode_capability_boundaries_v1.md`
- 输出模式：`akane_multimode_output_profiles_v1.md`、`client-mode-architecture.md`
- 附件/产物：`attachment_focus_inbox_v1.md`、`artifact_container_system_v1.md`
- 工具系统解耦（进行中）：`tool_system_decoupling_v1.md`
- 桌宠角色工坊：`desktop_pet_character_workshop_v1/README.md`
