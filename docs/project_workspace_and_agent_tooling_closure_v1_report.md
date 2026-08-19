# Project Workspace 与 Agent 工具链可靠性收口 V1 - 封板报告

> 日期：2026-08-20
> 状态：核心链路完成，未部署
> 范围：Akane、MemCore 公共 request-binding API、Web Search、Coding Skill

## 1. 修改、净行数与提交

Akane 运行时代码与 Skills 合计 `+1979/-299`；测试和执行文档另计。MemCore 生产代码
`+147/-5`，测试 `+154/-0`。

- `217998a`：Shell/toolchain 与结构化拒绝。
- `3b46105`：持久 Project Workspace、write/patch 与 `alias:project`。
- `add3116`：Akane 按 source turn 绑定 provider request。
- `0e13da9`：Web Search 真实执行、fallback、规范化与分页。
- `873a6a6`：Skill 依赖、Coding Skill、cwd 描述和长结果旧逻辑清理。
- MemCore `7327996`：宿主无关 request-binding API。
- 执行文档：`71136df`。

## 2. Phase 0 复现证据

- 14592 字命令超过 8192 上限，旧链只给模型 `execution_unknown`；现锁定为
  `rejected/command_too_long`。
- 空 `cwd + output_globs` 会进入 per-run 受管目录，不能假定仍在此前项目目录。
- 旧 checkout 与 active release 混用当前共享依赖可产生版本错配；新项目根只能位于
  execution workspace 的 `Projects/`。
- active user turn 加 standalone material turn 曾触发 `projection_source_turn_mismatch`；现请求级测试
  保留两条 provider message，只冻结 active 索引。
- readiness=`checking` 曾使 schema 可见但执行冻结；现 `offered=true` 并允许真实执行。
- AnySearch Markdown 结果曾被当成空结果，raw fallback 只剩 1200 字；14 条 fixture 和长 Markdown
  fixture 现均无损读取。
- 旧共享 8000 字 followup 截断和 Adapter 的 1200/4000/6000 多层切断均已复现并移除。

MemCore settlement 的 `256 bytes / 0.5 savings ratio` 未改变。

## 3. Project Workspace 权威与隔离

唯一数据源是 `project_workspaces` 与 `project_workspace_selections`；物理根由
`ProjectWorkspaceService` 解析，模型只看到 `proj_*`、名称、状态和 `alias:project`。

- 私聊：`owner_kind=private`，按 profile 共享选择，因此换 session 可继续同一项目。
- 群聊：`owner_kind=group`，`owner_id=group session`，`actor_scope=actor_stable_id`；不同群或成员
  不能 list/select 对方项目。
- 桌面：`owner_kind=desktop`，按 profile 跨桌面会话复用。

## 4. 创建、选择、复用与归档

三端均由同一模型工具 `manage_project_workspace` 完成 `current/list/create/select/archive`。create 自动
选中；select 使用稳定 workspace ID；archive 清除选择但不删除文件。

当前桌宠尚无“操作系统目录选择器绑定任意已有目录”入口。桌面模型可创建和选择受管项目，但已有
外部目录不能原地绑定。这是独立 UI + 授权桥切片，不能通过接受模型提供绝对路径来伪装完成。

## 5. release/source/venv 写入边界

项目记录只存 `Projects/proj_*` 相对引用，根必须落在 execution workspace 的专用 `Projects/` 下；
`..`、绝对路径、盘符、符号链接/重解析逃逸均拒绝。Project Workspace 不扫描宿主，也不能选择
release、source checkout、共享 venv、数据库或缓存目录。

## 6. write/patch 最终契约

- `workspace_write(workspace_id?, path, content, expected_sha256?, mode?)`：UTF-8，单次最多
  256 Ki chars，临时文件 + flush/fsync + replace 原子提交；支持 create/replace/create_or_replace。
- `workspace_patch(workspace_id?, patch, expected_files?)`：标准 unified diff，单次最多 256 Ki chars；
  V1 只修改既有 UTF-8 文件，不 create/delete/rename。所有文件、hash 和 hunks 先验证，再逐文件
  原子替换；失败会回滚已替换目标。
- 并发保护返回 `base_hash_mismatch` 和实际 SHA-256；路径、解析、hunk、大小和写入失败均为稳定 reason。

## 7. Shell 长命令反馈

模型实际收到：

```json
{"status":"rejected","reason":"command_too_long","max_chars":8192,"actual_chars":14592,"recommended_action":"workspace_write_or_patch"}
```

空命令为 `rejected/command_required`。参数错误先于审批判断，不再折叠成 unknown。

## 8. toolchain manifest 实测

本机 smoke：`platform=windows`，`command_shell=cmd.exe`，`preferred_script_shell=pwsh`；Python
3.11.4、Node v24.18.0、Git 2.52.0、rg 15.2.0 可用，npm 在当前受控 PATH 中不可用。探测不返回
可执行文件物理路径，也不会把旧项目 vendored binary 算作全局工具。

## 9. cwd、scratch 与 output globs

- 编程项目：固定 `cwd="alias:project"`，解析为当前选择项目。
- 无 input/output 资源且省略 cwd：受信任 execution workspace 根。
- 有 `input_resources`，或省略 cwd 但声明 `output_globs`：独立 per-run 受管目录。
- 显式 cwd 加 `output_globs`：相对该 cwd 登记本次新增/变化产物，不拥有也不清理项目目录。

schema、handler prompt 与 Coding Skill 使用同一表述。

## 10. 硬截断清理

已删除：AnySearch 1200 raw fallback、共享 `shape_tool_followup` 的 8000 字截断、Adapter content
block 的 1200/4000 切断、Adapter/Plugin feedback 的 6000 切断。

保留：未分页第三方 Adapter 的唯一 64 KiB 最终保险。触发时不返回残缺成功正文，而是
`result_limit_exceeded`，带 `actual_chars/max_chars/recommended_action`。preview、通知、语音等非模型
证据显示限制不在本轮范围。

## 11. 分页契约

正文首页 50,000 chars/2,000 lines，续页 32,000 chars/1,000 lines。cursor 为 opaque `p1.*`，绑定
tool、profile+session owner、资源标识/指纹和 offset。跨会话为 `cursor_owner_mismatch`，资源改变为
`stale_cursor` 或 `content_changed`，缺失为 `source_missing/snapshot_expired`；不静默从头读、不拼接
新旧内容。

本机 70,000 字 Shell 输出 smoke 共 2 页，`BEGIN`/`END` 均可见，70,000 个正文字符完整恢复。

## 12. MemCore 公共 API

`bind_request_projection_messages(messages, active_turn_id=...) -> RequestBindingResult` 位于 MemCore
公共包；按原始 request 顺序产出 active/standalone groups 和 request indexes。Akane manager 只做
类型转换，不复制 grouping 算法。

`record_request_projection(..., history_message_indexes=...)` 可冻结非 suffix active message；索引必须
合法、唯一、递增。缺失或冲突 source turn 返回 `request_binding_source_turn_ambiguous` 等结构化错误。

## 13. synthetic turn 的 provider 样本

测试请求按原序包含：

```text
index 0: active turn user payload "结合材料回答"
index 1: standalone material payload "独立材料正文"
```

provider 收到两条；binding groups 为 `active:[0]`、`standalone:[1]`；projection_count=1，只把 index 0
冻结到 active turn。真实 source turn ID 没有被当前 turn 覆盖。

## 14. 三类上下文失败

- `context_authority_failed`：绑定/可信 projection 失败，request observer 拒绝 provider 请求，返回
  可交付的上下文衔接失败说明，不假装正常回答。
- `audit_persistence_failed`：provider messages 已可信生成，仅审计附加落盘失败；告警并继续请求。
- settlement 失败：MemCore 保留可信 full projection，不丢历史、不生成假卡片；状态由既有 settlement
  结果记录。

## 15. settlement 与开放轮

阈值仍为 `operation_settlement_min_utf8_bytes=256`、
`operation_settlement_min_saved_ratio=0.5`。assistant final 前，tool call arguments 与 tool result 按
provider 原始顺序完整保留；final 后才由 MemCore 统一选择 full/inline/compact_reloadable。

## 16. Web Search

- 管理员 disabled：`offered=false`；配置存在的 checking/unhealthy：`offered=true`，不阻止本次调用。
- 每次优先 MCP；异常或 MCP `isError` 时，同一次工具调用执行 REST fallback。
- JSON、MCP content blocks、Markdown 均归一。14 条 Markdown fixture 的第 14 条可见；无法结构化的
  Markdown 用 `format=markdown_raw` producer paging；真正空数组是 `status=no_results`。
- diagnostics 含 `providers_attempted` 与 `fallback_used`；续页重查漂移返回 `content_changed`。

本轮未调用真实付费/公网 provider，结论来自 handler 真实 dispatch fixture，而非 readiness 常量测试。

## 17. 删除、变薄与迁移窗口

- 共享默认目录不再承担编程项目身份；Project Workspace 为唯一项目权威。
- `output_globs` 不再被描述成隐式项目定位；三种 cwd 模式已明确。
- execution 参数拒绝保留原 reason，不再统一映射 unknown。
- 生产者分页结果不再经过全局 8000 字截断。
- readiness 不再冻结 Web Search execution authority。
- AnySearch 1200 raw fallback 已删除。
- Akane 手写 source grouping 已删除，变为 MemCore API 薄适配。
- Coding Skill 旧“泛 workspace + Shell 传源码”路径已删除。
- Skill `metadata.required_tools` 按本轮真实工具集合过滤，关闭执行时不会再提示 Shell 型 Skill。

## 18. 自动验证

- MemCore 全套：548 passed，5 skipped。
- Akane 全套：2516 tests，4 failures + 1 error，2 skipped；五项均为本轮前既有 WIP/契约漂移：
  desktop realtime voice/TTS 两项、settings catalog 一项、Task Workspace 旧 `[local_path]` 一项、
  resource visibility compose-event 一项。本轮修改文件不与这些生产路径重叠。
- 本轮核心矩阵：241 passed，1 platform skip；最终 Project/MemCore/Search/Execution/Paging 矩阵
  175 passed。
- 19 个变更生产 Python 文件 `py_compile` 通过；6 个 bundled Skill quick validator 通过；
  `git diff --check` 通过。

## 19. 真实表现 smoke

本地隔离 smoke 已通过：中文项目名创建；private A 创建后 private B 自动复用；write + hash-guarded
patch 后真实文件为 v1/v2 两行；`exec_run(cwd="alias:project")` 真实完成并看到 v2；70k 输出两页完整。

群成员隔离、archive、路径逃逸、checking 搜索、14 条 Markdown 与 synthetic turn 由真实 handler/provider
request 测试覆盖。未运行真实 QQ 私聊/群聊、桌宠人工 UI、云端或付费 provider smoke；未部署。

## 20. schema/cache 影响

新增三项 native/legacy 同源 schema：`manage_project_workspace`、`workspace_write`、`workspace_patch`；
`exec_run` 仅描述与错误反馈收紧。Web Search schema 参数不增加第二套协议。Skill catalog revision 现在包含
required-tools metadata，首次启用会产生一次目录 revision 变化。工具 schema/hash 会随新增工具一次性变化；
MemCore settlement/cache key 与阈值未改。

## 21. WIP 与产物

`gargantua/`、`minecraft_clone/` 始终未读写、未 stage、未提交。未提交 `.env`、runtime logs、数据库、
cache、wheel、build/dist、node_modules 或 smoke 临时项目；临时目录已自动删除。MemCore 工作区干净，Akane
只剩上述两个用户未跟踪目录。

## 22. 风险、回滚与部署

剩余风险：桌宠缺少任意已有目录的显式 picker/bind 授权桥；云端 PATH 和真实 QQ actor 流尚未 smoke；
第三方未分页 Adapter 超 64 KiB 会诚实拒绝，需 provider 自身增加 paging；Akane 全套仍有五项既有失败。

回滚可按提交独立进行：Shell `217998a`、Project Workspace `3b46105`、MemCore Akane adapter `add3116`
+ MemCore API `7327996`、Search `0e13da9`、Prompt/Skill `873a6a6`。Project schema 回滚不删除项目文件。

**未部署。** 部署应单独执行 wheel 同步、release 构建、数据库前向迁移、服务重启和 QQ/云端 smoke；
失败时关闭对应 capability offer，不能恢复旧歧义路径。
