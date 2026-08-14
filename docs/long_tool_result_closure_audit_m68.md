# 长工具结果闭环审计（Phase 0）

> 只读审计报告。对应执行单《Akane 长工具结果闭环改造》Phase 0，作为后续 Phase 1–5 的实施依据。
> 日期：2026-08-14。基线 commit：a71c6e3。

## 0. 结论摘要

当前工具轮存在三类真实硬截断点：

1. **全局 8000 字保险截断**（`tool_orchestration_engine.shape_tool_followup`）：所有未声明
   producer-bounded 的工具结果，超过 `MAX_TOOL_FOLLOWUP_CHARS=8000` 时被按行破坏性截断，
   只留"请缩小范围、加过滤条件或分页再调用"提示，没有 cursor、没有 continuation。
2. **handler 内静默截断**：`web_search`（结果摘要每条 420 字、整页 6000 字、extract 5000 字）、
   `browser_page`（快照/正文 5000 字硬切、elements 40 条硬切）都在 handler 内 `[:limit]` 破坏性切掉，
   不带 complete / cursor / 剩余量。
3. **服务层截断无续读**：`read_workspace` 超过 `max_chars`（默认 1M）时返回
   `truncated=True + [内容达到单次读取上限，已截断。]`，没有 continuation；`list_workspace` 50000 条
   渲染成文本后同样被 8000 出口二次截断。

基线复现（确定性 fake 数据，脚本见 `maintenance/_phase0_*` 临时脚本）：

| 复现场景 | 结果 |
|---|---|
| 100891 字工作区正文经 `shape_tool_followup` | 只留 7991 字 + "已截断…请缩小范围"（无 cursor） |
| 100891 字工作区文件 `read_items(max_chars=1M)` | 服务返回完整 102891 字，但 handler 文本会在出口被砍到 8000 |
| 10 条 × 500 字摘要的搜索结果 | 每条摘要被砍到 420 字并出现 `...[truncated]`，条目被从中间切断 |
| producer-bounded envelope | 完整透传，无二次截断（已正确） |
| complete=False 且无 continuation | 出口正确追加"没有提供可执行 continuation"警告（已正确） |

## 1. 长结果矩阵（逐工具）

图例：S=handler 自身上限；T=是否截断；C=返回 complete；Cu=是否有 cursor；PB=producer-bounded；
G=是否再经统一 8000 出口；M=MemCore 当前轮存什么；R=settled 后可回看；A=真实权威数据源；P=适合分页。

### 已合格（只做回归，不重写）

| 工具 | 现状 | 判定 |
|---|---|---|
| `exec_run` / `exec_status` | `execution_run.py` 有版本化 cursor（`c1.<tag>.<offset_hex>`）、owner 绑定、输出页字节/行上限、`next_cursor`；handler 用 producer_bounded envelope（`execution.py:_mapped_result`） | 已合格 |
| `open_memory` / `read_memory_timeline` / `browse_memory` | 包原生 cursor 续读，handler producer_bounded + complete + continuation（`memory.py`） | 已合格 |
| `retrieve_memory` | `retrieval_engine.py:556` producer_bounded + continuation | 已合格 |
| `load_skill` | `skills.py:_result` producer_bounded complete=True；SKILL.md 本身按设计有界 | 已合格（complete_bounded） |

### 需迁移（paged_source）

| 工具 | S | T | C | Cu | PB | G | M | R | A | P |
|---|---|---|---|---|---|---|---|---|---|---|
| `read_workspace` | 服务层 max_chars≤4M；`truncated` 标志 | 是（>4M 服务截断；否则被 8000 出口砍） | 否 | 否 | 否 | 是 | 截断后的 followup | 是（存了被砍版本） | 工作区原文件 | 是 |
| `list_workspace` | 50000 条扫描上限 | 是（8000 出口二次截断） | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | 实时文件系统 | 是（按完整 entry） |
| `register_workspace_items` | max_files≤5000 | 是（条目清单可能 300k+ 字，8000 出口砍） | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | 实时文件系统+附件服务 | 否（完成回执即可） |

> Repair pass 修正：`register_workspace_items` 不再隐藏成功 handle（`list_workspace` 无法恢复 handle），
> 而是按完整 handle 回执分页；每页实际登记本页文件，后续 cursor 绑定 owner 与解析后文件集指纹。
> `focus_workspace` 当前轮使用与 `read_workspace` 相同的首页预算，超出部分直接返回
> `read_workspace` continuation，不再以 producer-bounded 绕过保险后一次灌入多文件全文。
> `load_character_context`（包内容有界）保持 producer-bounded complete=True。`AdapterCapabilityToolHandler` 保持 6000 字
> 上限，作为“未迁移第三方结果”的最后保险分类。
| `web_search.search/batch_search` | 每条摘要 420 字、整页 6000 字 | 是（条目中间切断） | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | AnySearch 返回（已归一） | 是（按完整结果条目） |
| `web_search.extract` | 5000 字 | 是（正文硬切） | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | AnySearch 提取正文 | 是（重提取+fingerprint） |
| `browser_page` snapshot/read_text/current | 5000 字硬切 | 是 | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | 浏览器页面（动态） | 是（需不可变快照缓存） |
| `browser_page.elements` | 40 条硬切 | 是 | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | 浏览器页面（动态） | 是（按完整元素记录） |
| `read_attachment_section` | 抽取默认 12000 字 | 部分（section 语义即分页，但出口 8000 仍会砍） | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | 附件原文件/已解析文本 | 是（溢出保护） |
| `inspect_generated_file` (content/file_list) | 40000 字 | 是（8000 出口砍） | 否 | 否 | 否 | 是 | 截断后的 followup | 是 | 生成文件本体 | 是 |

### 保持短结果（short_inline / complete_bounded，不动）

- 播放器控制、`send_file`/`send_audio`/`send_sticker`/`send_music_card`、提醒、库存、
  图片生成状态、capability ACK、`gen_*` 登记、审批结果、`focus_workspace`、
  `manage_task_workspace`、`open_browser`/`open_music_search`、媒体工作台各工具——
  结果天然有界或只是状态回执，不强制卡片化、不加 cursor。
- `web_search.get_sub_domains`：当前 `_clip(...,3000)` 是 handler 内上限；
  本次一并升级为 producer-bounded complete（把 3000 上限提为完整归一输出），不再被二次砍。

## 2. 截断点定位（文件:行）

- 全局保险：`tool_orchestration_engine.py:253`（`DEFAULT_MAX_TOOL_FOLLOWUP_CHARS = 8000`）、
  `shape_tool_followup` 308–368。
- 引擎出口唯一调用点：`engine.py:6638` `_record_tool_round_result` → 每轮工具结果在此 shape，
  同时该结果进入 `tool_followups`（下轮模型可见）与 MemCore 工具批（`_record_memcore_tool_batch`，engine.py:6964，
  `manager.record_tool_batch` memcore_integration/manager.py:766）。
- MemCore 当前轮保存：每轮 shaped followup 全文作为 OBSERVATION 记录；
  assistant final（complete_turn）后才按 `memcore/settlement.py` 确定性分类压成
  `inline_full` / `compact_reloadable` / `explicit_empty`；`open_memory` 可回读原始正文。
  → 满足"final 后才压卡、settled 可回看"的要求，MemCore 侧无需改动。
- `read_workspace` 服务：`workspace_files.py:668 _read_file`（`content[:max_chars]`+truncated 标志）。
- `web_search`：`tool_handlers/web_browser.py:1480-1508`（`_format_search_followup` 每条 420/整页 6000）、
  `1510-1537`（`_format_extract_followup` 5000）、`MAX_FOLLOWUP_CHARS=6000`/`MAX_EXTRACT_CHARS=5000`（865–866）。
- `browser_page`：`browser_page_runtime.py:239-284`（`_safe_body_text`/`_safe_page_snapshot`/`_safe_aria_snapshot`
  硬 `[:limit]`，limit≤5000）、`511-548`（`_safe_element_summary` 40 条）；handler `MAX_TEXT_CHARS=5000`、
  `_clip`（web_browser.py:842-846）。
- `read_attachment_section`：`attachment_inbox.py:519 read_section` → `_extract_original_file_section`
  （12000 默认上限，section 语义分页）。
- `inspect_generated_file`：`generated_files_delivery.inspect_generated_file`（max_chars≤40000）。

## 3. 决策门逐工具裁决

| 分类 | 工具 |
|---|---|
| `short_inline` | 播放器控制、交付、提醒、库存、审批、capability ACK、gen_* 登记、open_browser、open_music_search、manage_task_workspace、媒体工作台状态 |
| `complete_bounded` | load_skill、retrieve_memory、open_memory 等已合格者；`web_search.get_sub_domains`、load_character_context |
| `paged_source` | read_workspace、list_workspace、register_workspace_items、focus_workspace（跨工具续到 read_workspace）、read_attachment_section、web_search.search/batch_search/extract、browser_page 快照/elements、inspect_generated_file（content 续读） |
| `reloadable_external` | browser 快照（实例内不可变缓存）；MemCore compact_reloadable 卡片承载历史 |
| `unsafe_unbounded` | AnySearch 原始 JSON：只保存安全归一后的证据字段，不存原始大 JSON；AdapterCapabilityToolHandler 保持 6000 字最后保险 |

## 4. 设计要点（进入 Phase 1 的硬约束）

1. 保留 8000 字统一保险，只作为未迁移/异常工具的兜底；长结果工具一律
   `ToolFollowupEnvelope(producer_bounded=True, complete, continuation, diagnostics)` 绕过。
2. `complete=False` 必须带可执行 continuation；`complete=True` 时 continuation 必须为空。
3. 分页以完整逻辑单元为边界：文件按行、列表按完整条目、搜索按完整结果、快照按行；
   不拆 UTF-8 字符、不拆条目、不拆结构化对象。
4. cursor 版本化（`p1.`）、opaque、绑定工具+owner（profile/session）+资源指纹（size/mtime_ns 或内容 hash）+偏移；
   跨会话或 cursor 损坏统一返回 `cursor_invalid`（不泄露 owner 是否存在）；文件变化/页面变化 → 结构化 `stale_cursor` /
   `source_missing` / `snapshot_expired` / `page_closed`，绝不静默从头重读、绝不新旧拼接。
5. 模型可见反馈明确：本页范围、剩余事实、够用即可回答、只有需要时才用 cursor 续读；
   不输出 schema 说明书，不强迫翻页。
6. 不新增永久 ID/数据库：文件续读回原文件、网页续读重提取同 URL 校验指纹、
   浏览器续读实例内不可变快照缓存（TTL/条数/字节上限，进程重启失效）。
7. cursor/snapshot_id 只出现在工具轨迹尾部，不进稳定 prompt cache key；不改 `_final_prompt_cache_key`。
8. 每个工具只做一次稳定 schema 变更；native schema 与 legacy prompt 描述同一套参数。

## 5. 计划实施顺序（对应执行单 Phase 1–5）

1. 共享分页契约：`companion_v01/paged_reading.py`（cursor 编解码 + 行边界分页器 + 结构化失败）+ 契约测试。
2. Phase 2 工作区：read_workspace / list_workspace / register_workspace_items / read_attachment_section / inspect_generated_file。
3. Phase 3 Web：search/batch_search（完整条目分页）、extract（重提取+fingerprint）、get_sub_domains（complete_bounded）。
4. Phase 4 Browser：不可变快照缓存 + snapshot_id/next_cursor + elements 条目分页。
5. Phase 5 收口：复核其余工具，回归测试。

## 6. 回归测试清单（不跑全量）

`tests/test_tool_runtime.py`、`tests/test_native_tool_schema.py`、`tests/test_native_web_search_tooling.py`、
`tests/test_execution_kernel.py`、`tests/test_execution_wiring.py`、`tests/test_execution_resources.py`、
`tests/test_memcore_integration.py`、`tests/test_workspace_files.py`、`tests/test_workspace_management.py`、
`tests/test_generated_files.py`、`tests/test_attachment_inbox.py` + 新增契约测试。

## 7. 云端部署记录（2026-08-14）

- 云端不可变 release `5f2b975-long-tool-paging` 已上线统一 Host；上一版
  `bfac18c-group-resource-scope` 保留为直接回滚点（`/opt/akane/ops/90-release.conf.bak-fc49d38-20260814T1132Z`）。
- 部署前按惯例对 personal/finance 的 memcore 与 akane_memory 共 6 个 SQLite 库做 online backup +
  `PRAGMA quick_check`（全部 ok），备份目录 `/opt/akane/backups/fc49d38-predeploy-20260814T113159Z`。
- 切换前在云端新 release 内运行 516 项聚焦回归全部通过。该轮在服务器上发现并修复一个真实缺陷：
  文件指纹原用 `size:mtime_ns`，在云主机文件系统上两次内容不同的写可能落入同一时间戳，
  导致内容变化漏检；已改为提取正文 sha256 内容指纹（commit `5f2b975`，含回归测试）。
- 切换后 `/health` 为 `ok` 且 root binding 有效，`akane-host.service` active 且 `NRestarts=0`，
  进程 cwd 指向新 release；personal/finance 两个 Bot 均为 `online / active`，
  两条 QQ self-check 均为 `connected`（account_online=true）。
- 启动窗口没有 traceback、import error 或 MemCore compaction failure；vision 的
  JSONDecodeError 警告在切换前 2 小时已有 115 次同类记录，属于既有现象，非本 release 引入。
- 未修改 Bot 账号、NapCat/OneBot、模型密钥、host.env、bots.toml、QQ profile 或两份数据根；
  `.packages` 由上一 release 原样复制（本轮未改动任何抽包依赖）。
- 部署前 schema 变化说明：read_workspace / list_workspace / web_search / browser_page /
  inspect_generated_file 各发生一次稳定 schema 变更（新增 cursor，删除 max_chars），
  对应 provider 前缀会冷建一次；之后 schema 保持稳定，不影响 MemCore 压卡规则。
