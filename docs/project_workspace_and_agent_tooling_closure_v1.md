# Project Workspace 与 Agent 工具链可靠性收口执行单 V1

> 状态：待执行，先审查后实施
> 日期：2026-08-19
> 范围：Akane 宿主、MemCore 通用接入面、Web Search 执行链路
> 部署：本执行单完成并通过 repair pass 前禁止部署

## 1. 目标

本执行单不是修补 2026-08-19 两次失败任务的表面报错，而是收口一类长期问题：模型已经知道如何完成任务，但宿主没有提供稳定、清晰、可验证的执行环境，导致路径猜测、长命令拒绝、工具结果丢失、上下文投影失败和搜索假不可用。

最终目标是建立一套对聊天模型、编程 Agent 和未来其他宿主都成立的通用契约：

```text
Prompt-led, contract-backed

提示词负责告诉模型如何工作；
工具契约保证提示词描述的能力真实存在；
工具结果向模型提供足够证据；
MemCore 在回合完成后统一管理历史体积和可召回性。
```

完成后，模型应当能够：

1. 明确知道当前项目是什么、在哪里工作、是否为临时项目；
2. 用专用写入和补丁原语修改文件，不再把大段源码塞进 Shell；
3. 在执行前看到真实平台、Shell 和工具链能力；
4. 收到结构化、可行动的失败原因，不再只看到 `execution_unknown`；
5. 在当前工具轮读取完整逻辑页，需要时通过 cursor 续读；
6. 由 MemCore 看到按真实 turn 归属构建的权威上下文，回合完成后再结算成可重载卡片；
7. 在 Web Search 健康探针抖动时仍可真实尝试执行，并读取规范化、可分页的结果。

## 2. 已确认的故障证据

### 2.1 群聊编程任务（12:12-12:20）

- 会话：`qq_group_shared_872732158`。
- 总耗时约 `480600 ms`，约 14 个工具轮。
- 主写入命令长 `14592` 字符，超过 `EXEC_COMMAND_MAX_CHARS=8192`。
- 内部真实原因是 `execution_request_invalid`，模型只收到笼统的 `execution_unknown`。
- 当前没有 `apply_patch` 等通用文件修改原语，模型被迫通过 Shell 传输大段源码。
- `output_globs` 在空 `cwd` 下切入隔离的 `.akane_exec_runs/...`，此前可见的 `minecraft_clone/three.min.js` 因而不可见；模型误判为文件状态发生变化。
- 共享执行目录混有历史项目和残留文件，没有明确项目所有权。
- 执行 PATH 中没有 Node；模型随后从旧项目内找到 vendored Node 18。这不是可靠的工具链发现方式。
- 模型编写的复合命令中，后续命令成功掩盖了前序 `curl 404`，最终 exit code 为 0。

### 2.2 私聊编程任务（12:47-12:54）

- 会话：`master`。
- 总耗时约 `386604 ms`，约 25 个工具轮。
- 模型发现并修改了 `/opt/akane/AkaneCompanionLab`。
- 该目录是无 Git 元数据的陈旧可写副本，不是当前运行 release。
- 当前服务实际运行 `/opt/akane/releases/cf1629e-memcore-summary-luna`。
- 陈旧代码却加载当前共享 venv/MemCore 包，出现 `raw_trigger_count`、`keywords`、`record_tool_exchange(turn_id=...)` 等真实版本不兼容。
- 模型把“旧宿主代码 + 新依赖”的错误泛化成生产 MemCore 故障；修改既不影响线上服务，又污染了陈旧副本。

这不是模型不会编程，而是宿主没有给出明确的项目边界、运行版本和写入权限边界。

### 2.3 MemCore

压缩本身正在工作：

- 群聊约 `60k -> 29k tokens`；
- 私聊约 `62k -> 31k tokens`。

因此本轮禁止用调阈值、加更激进摘要或提高压缩频率来“修复”问题。

真实缺陷是至少出现 7 次的：

```text
projection_source_turn_mismatch
```

当前请求历史可能同时包含：

- 活跃用户 turn 的用户消息和工具轨迹；
- 由附件、材料引用等产生的独立 synthetic turn；
- turn ID 为空或属于其他 turn 的来源消息。

`response_builder._build_memcore_provider_history()` 将这些内容一并放入 `current_turn_messages`，请求 observer 又尝试把所有 source 冻结到活跃 turn。MemCore 拒绝跨 turn 归属是正确行为，不能放宽校验。

### 2.4 Web Search

已确认两类故障：

1. readiness 探针越权控制执行。`checking` 不被 `ServerLocalOfferIndex` 视为 offered，导致 schema 仍可见但本轮执行被冻结为 unavailable；随后 AnySearch 即使返回 HTTP 200，也无法挽救已经冻结的工具轮。
2. AnySearch 经常返回 Markdown 搜索列表而非结构化数组。当前解析器将真实结果判成“没有拿到可用搜索结果”，raw fallback 又只保留约 1200 字符，进一步丢失证据。

## 3. 非目标与禁止项

本轮明确不做：

- 不把 Akane 改造成完整 IDE，也不新增项目专属的“写 Minecraft”工具；
- 不简单提高 Shell 命令长度上限来承载源码；
- 不让模型扫描服务器全部目录以猜测项目；
- 不让云端执行器写宿主源码、active release、旧 release 或共享 venv；
- 不降低 MemCore 的 turn、namespace、source ownership 校验；
- 不新增 legacy bypass 或对某个宿主特判的 MemCore 投影格式；
- 不在开放工具轮提前压缩参数和结果；
- 不用 MemCore 卡片为当前轮硬截断辩护；
- 不让健康探针决定一个已配置工具是否允许真实执行；
- 不把 provider、解析器或系统失败伪装成模型成功；
- 不触碰 `gargantua/`、`minecraft_clone/` 及其他用户 WIP；
- 不在 repair pass 审查通过前部署。

## 4. 硬性设计原则

### 4.1 单一项目权威

代码任务必须绑定一个稳定 Project Workspace。`cwd`、文件写入、补丁、构建、产物登记都引用同一个 workspace ID/alias，不再各自猜路径。

### 4.2 Shell 是执行通道，不是源码传输协议

Shell 用于 build、run、test、install 和诊断。文件创建与局部修改由结构化工具完成。Shell 仍保留合理的长度上限，但拒绝必须告诉模型确切原因和下一步。

### 4.3 当前轮证据优先

模型当前正在工作的回合中，工具参数、结果、错误和分页内容必须完整可用。较长结果由 producer 分成完整逻辑页，不能在任意字符位置硬切。

### 4.4 MemCore 是历史上下文权威

assistant final 之前保留开放轮；final 之后由 MemCore settlement 决定 full、compact_reloadable 或其他既有状态。宿主不得另建第二套压缩和工具专属卡片。

### 4.5 失败是模型可见 API

所有失败至少包含稳定 `status`、`reason` 和可行动字段。日志用于运维，不能替代给模型的真实反馈。

### 4.6 Prompt-led, contract-backed

Skill 可以要求模型“选择项目、增量修改、验证后交付”，但只有对应工具和状态真实存在时才能写入提示词。future-only 能力只进文档，不进运行时 prompt。

## 5. 目标架构

```text
User / conversation / group actor
                |
                v
       Project Workspace authority
       - workspace_id
       - owner / group / actor
       - stable alias:project
       - lifecycle / permissions
                |
       +--------+---------+
       |                  |
       v                  v
workspace_write/patch   exec_run(cwd="alias:project")
       |                  |
       +--------+---------+
                v
       output/resource registration
                |
                v
       open tool-turn evidence
                |
       assistant final closes turn
                |
                v
       MemCore settlement + reload card
```

上下文请求链路：

```text
host source messages
       |
       v
turn-aware request binding helper
       |
       +-- group by real source turn identity
       +-- preserve synthetic/attachment ownership
       +-- reject ambiguous attribution
       v
MemCore request projection
       |
       v
provider-visible messages
```

## 6. Phase 0：冻结复现与安全基线

### 目标

在动生产逻辑前，把四类故障变成可重复测试，避免修复只对历史日志成立。

### 工作项

1. 为 `14592 > 8192` 的命令建立回归夹具，锁定内部拒绝原因不能丢失。
2. 复现空 `cwd + output_globs` 导致工作目录切换，记录当前行为和目标行为。
3. 建立“旧宿主目录 + 当前共享依赖”隔离测试，确认 release/source 树不可被项目工具选择。
4. 用多 source turn（活跃 turn + synthetic attachment turn）复现 `projection_source_turn_mismatch`。
5. 复现 readiness=`checking` 时 schema 可见但执行被冻结。
6. 保存 AnySearch Markdown、JSON、MCP content block 三种真实响应样本，去除密钥和内部 URL 参数后纳入 fixture。
7. 审计所有剩余 `1200/6000/8000` 类字符截断点，逐项标记：producer-bounded、第三方失控保险或待迁移。

### 决策门

- 每项必须有失败测试或可审计 fixture；只有日志描述不得进入下一 Phase。
- 确认 MemCore 压缩阈值保持不变。
- 确认 release、宿主源码和共享 venv 不会被后续测试写入。

## 7. Phase 1：Project Workspace 权威与所有权

### 目标

建立用户可选择、跨回合稳定、权限明确的项目工作区抽象。

### 最小数据契约

```text
workspace_id        稳定、不可猜路径的 ID
display_name        用户可见名称
owner_kind          private / group / desktop
owner_id            脱敏稳定主体 ID
actor_scope         群内创建者或允许协作者范围
state               active / archived / missing
root_ref            宿主内部引用，不进入模型上下文
created_at/updated_at
```

模型只看到 `workspace_id`、名称、状态和稳定别名 `alias:project`，不需要看到宿主物理根路径。执行需要的项目相对路径可以原样出现。

### 用户入口

- 桌面端：目录选择器绑定现有目录，或创建新项目。
- QQ 私聊：list/create/select/archive 项目命令或等价工具。
- QQ 群聊：项目所有权至少绑定 group + actor，不允许不同群或不同创建者无意共享。
- 新对话默认继承“当前明确选中的项目”，没有选择时必须提示选择或显式创建 scratch。
- scratch 是显式模式，具有清晰生命周期，不伪装成持久项目。

### 工具契约

- `list_project_workspaces`
- `create_project_workspace`
- `select_project_workspace`
- `archive_project_workspace`
- 或复用已有 workspace 工具并扩展为同等语义；不得长期保留两套权威入口。

### 安全边界

- root 必须位于专用项目根或用户明确选择的桌面路径。
- 云端项目根与 `/opt/akane/releases`、宿主 checkout、共享 venv、runtime DB/cache 完全隔离。
- 所有路径解析经过 safe child/path containment；禁止 `..`、符号链接逃逸和隐式绝对路径。
- 不扫描全服务器寻找“可能的项目”。

### 可能涉及文件

- `companion_v01/workspace_files.py`
- `companion_v01/tool_handlers/workspace.py`
- `companion_v01/capability_registry.py`
- `companion_v01/engine.py`
- QQ/桌面现有命令或设置接入层

### 验收

- 私聊跨会话选择同一项目后可继续修改同一文件。
- 未选择项目时不会落入历史共享目录。
- 群 A/成员 A 无法访问群 B 或成员 B 的项目。
- archive 后不可写，重新选择给出结构化状态。
- 模型永远不会把 active release 当成项目工作区。

### 删除/收口

- 删除或变成薄适配器的全局共享默认项目根逻辑。
- 删除通过当前进程目录、历史 `cwd` 或 output glob 猜项目的路径。
- 旧 workspace 字段只能进入 documented migration window，不得无限兼容。

## 8. Phase 2：通用文件写入与补丁原语

### 目标

让模型以结构化方式创建和增量修改项目文件，避免长 Shell 命令、转义错误和失败掩盖。

### `workspace_write`

建议输入：

```json
{
  "workspace_id": "...",
  "path": "src/main.js",
  "content": "...",
  "expected_sha256": "optional",
  "mode": "create_or_replace"
}
```

要求：

- 只接受项目相对路径；
- 临时文件写入、flush 后原子 rename；
- 支持 expected hash/base version，避免覆盖并发变化；
- 超过单次内容预算时返回 continuation/chunk contract，不静默截断；
- 返回 bytes、sha256、created/replaced 和项目相对路径；
- 不把宿主物理路径放入模型结果。

### `workspace_patch`

建议输入：

```json
{
  "workspace_id": "...",
  "patch": "unified diff",
  "expected_files": {
    "src/main.js": "sha256"
  }
}
```

要求：

- 支持标准 unified diff；
- 先完整校验所有目标和 hunks，再原子提交；
- 文件级/hunk 级结构化失败；
- 默认禁止补丁越出项目根；
- 失败不留下半应用状态；
- 大补丁使用明确续传协议或拆分为多次独立补丁。

### 失败语义

至少包括：

```text
workspace_not_selected
workspace_missing
workspace_archived
path_outside_workspace
path_conflict
base_hash_mismatch
patch_parse_failed
hunk_not_applicable
content_too_large
write_failed
```

每项带 `recommended_action`，但不得把失败包装成成功。

### 可能涉及文件

- `companion_v01/workspace_files.py`
- `companion_v01/tool_handlers/workspace.py`
- `companion_v01/capability_registry.py`
- native/legacy tool schema 测试

### 验收

- 新建多文件小项目不通过 Shell 传源码。
- 基于 hash 的局部修改成功；陈旧 hash 明确拒绝。
- patch 第二个 hunk 失败时，第一个 hunk 不得残留。
- 50 KiB 以上文件按明确协议续写，最终 hash 一致。
- Windows/Linux 路径语义一致，中文和空格项目名通过。

### 决策门

若现有 workspace 写入 API 可满足全部契约，应扩展并删掉重复路径；只有无法保持兼容时才新增工具名。

## 9. Phase 3：执行环境、Shell 与结果契约

### 9.1 工具链 manifest

模型在编码 Skill 或第一次执行前应能看到或查询：

```text
platform
shell + version
python + version
node + version
npm/pnpm
git
rg
workspace_write/workspace_patch availability
project workspace state
```

manifest 必须来自真实运行环境探测并有短 TTL；不能把旧项目中的 vendored binary 当成全局能力。云端用受控 PATH 或 wrapper，部署时明确安装/不安装哪些工具。

### 9.2 Shell 命令上限

保留有界命令长度。超过限制时模型应看到：

```json
{
  "status": "rejected",
  "reason": "command_too_long",
  "max_chars": 8192,
  "actual_chars": 14592,
  "recommended_action": "workspace_write_or_patch"
}
```

禁止再折叠成 `execution_unknown`。

### 9.3 cwd 与 output registration

- `exec_run(cwd="alias:project")` 始终解析到当前项目。
- 空 `cwd` 的含义必须唯一且写入 schema：显式 scratch 或明确拒绝，不得因 `output_globs` 偷换执行目录。
- output globs 相对于本次执行 cwd 解析；资源登记只登记允许目录内的实际产物。
- run/status/cancel 保持真实状态，不把“已发送信号”描述成“进程已停止”。

### 9.4 复合命令失败

- Coding Skill 明确 POSIX 使用 `set -e`/等价安全写法，PowerShell 使用 `$ErrorActionPreference = 'Stop'`。
- 工具结果保留每个真实 stdout/stderr/exit code；宿主不猜测模型命令的业务成功。
- 对明显的 shell wrapper 可提供 `fail_fast` 选项，但不偷偷改写用户命令。

### 9.5 长结果

- 当前轮结果由 producer 返回完整逻辑页。
- 每页包含 `complete`、展示范围、总量（可知时）和 opaque cursor。
- 不拆搜索条目、错误对象、JSON record 或源码行。
- producer 已迁移后，不再经过全局 8000 字符破坏性截断。
- 仅保留一个对第三方失控 adapter 的最终保险上限，触发时返回 `result_limit_exceeded` 和原始大小，不伪装完整。

### 可能涉及文件

- `companion_v01/execution_specs.py`
- `companion_v01/execution_run.py`
- `companion_v01/execution_local.py`
- `companion_v01/execution_resources.py`
- `companion_v01/tool_handlers/execution.py`
- `companion_v01/paged_reading.py`
- `skills/coding-project/SKILL.md`

## 10. Phase 4：MemCore 按真实 turn 的请求绑定

### 目标

让宿主把 source-attributed messages 按真实来源 turn 提交给 MemCore，避免宿主手工拼接造成跨 turn 归属。

### 设计要求

在 MemCore 提供或使用一个通用 request-binding helper：

```text
input:
  ordered host messages
  source_id / source_turn_id / role / payload
  active turn identity

output:
  frozen closed-turn groups
  active/open-turn messages
  standalone synthetic-source groups
  structured ambiguity errors
```

规则：

1. 已有 source turn ID 必须保持原值，不能被 active turn 覆盖。
2. 附件/材料 synthetic turn 可作为独立 source group 进入投影。
3. turn ID 为空时只有契约明确允许的 source type 可归入 active turn；其余返回歧义错误。
4. 开放工具轮完整保留，不提前 settlement。
5. assistant final 后仍走 MemCore 统一 settlement；不新增 Harness/Akane 专属卡片。
6. provider 实际 messages 必须能够在测试中重放并逐项核对。

### 故障分层

必须区分：

- `context_authority_failed`：无法构建可信 provider 上下文，本轮不能假装正常。
- `audit_persistence_failed`：provider 上下文已正确构建，但额外审计落盘失败；记录告警，不应无条件杀死有效模型回复。
- `settlement_failed`：历史压缩失败，回退到可信 full projection，不丢历史、不假结算。

具体是否允许继续必须由错误类别决定，不能用一个 broad exception 覆盖。

### 可能涉及文件/仓库

- `companion_v01/engine_services/response_builder.py`
- `companion_v01/engine.py` 的 request observer
- `companion_v01/memcore_integration/manager.py`
- `F:/Akane/MemCore` 的通用 context surface/request projection API

### 验收矩阵

| 场景 | 预期 |
|---|---|
| 单一普通 turn | provider 历史与现状等价 |
| 活跃 turn + attachment synthetic turn | 各自保持真实归属，无 mismatch |
| 多工具开放轮 | arguments/results 完整，不结算 |
| final 后下一请求 | 符合收益门槛的结果变 compact_reloadable |
| 卡片回读 | `open_memory` 恢复模型此前真实看过的结果 |
| source turn 缺失且不可推断 | 结构化拒绝，不错误归入 active turn |
| settlement 失败 | full projection 可用，回复链不假成功 |
| audit persistence 失败 | 已正确生成的回复按策略继续交付并告警 |

### 明确禁止

- 不修改 settlement 的 `256/0.5` 等既有统一收益规则来掩盖绑定错误。
- 不增加 `legacy=true`、`ignore_turn_mismatch` 等旁路。
- 不在 Akane 内复制 MemCore request grouping 算法。

## 11. Phase 5：Web Search 执行与结果规范化

### 11.1 readiness 只做诊断

权威规则：

- 管理员明确关闭：工具隐藏或结构化 disabled。
- 配置存在：schema 稳定可见。
- readiness=`checking`：允许真实执行，不冻结本轮。
- 执行顺序按当前能力设计尝试 MCP，再在允许时尝试 REST fallback。
- readiness=`unhealthy` 也不能替代本次真实执行结果；探针只提供诊断和路由参考。

### 11.2 统一结果模型

JSON、Markdown 和 MCP content blocks 都归一为同一 canonical contract：

```text
query
items[]:
  title
  url
  snippet/body
  source
  published_at (有则保留)
page:
  complete
  shown_items
  total_items (可知时)
  cursor
diagnostics:
  providers_attempted
  fallback_used
```

Markdown parser 只负责结构化真实条目，不得用 1200 字 raw clipping 代替解析。无法结构化时返回可分页的 raw document，明确 `format=markdown_raw`，仍保留完整逻辑页。

### 11.3 失败语义

```text
search_disabled
provider_unconfigured
provider_timeout
provider_http_error
provider_invalid_response
no_results
cursor_invalid
content_changed
```

`no_results` 只在 provider 确实返回空结果时使用，不能把解析失败写成无结果。

### 可能涉及文件

- `companion_v01/capability_registry.py`
- `companion_v01/tool_handlers/web_browser.py`
- `companion_v01/anysearch_rest_client.py`
- `tests/test_tool_readiness.py`
- `tests/test_tool_runtime.py`
- `tests/test_web_search_paged_results.py`
- `tests/test_native_web_search_tooling.py`

### 验收矩阵

| readiness / 响应 | 预期 |
|---|---|
| checking + MCP 成功 | 本轮成功，不报 unavailable |
| checking + MCP 失败 + REST 200 | fallback 成功，标记来源 |
| unhealthy + 本次 MCP 成功 | 返回真实成功结果 |
| Markdown 14 条 | 14 条均可通过分页读取，无条目中切断 |
| JSON 结果 | 与 Markdown 归一成同 schema |
| 两 provider 均失败 | 返回结构化失败，回合继续由模型解释 |
| 续页时内容变化 | `content_changed`，不拼接新旧结果 |

## 12. Phase 6：提示词、Skill 与旧逻辑清理

### Prompt/Skill 应表达

- 先确认或选择 Project Workspace；
- 优先 `workspace_write/workspace_patch`，Shell 用于执行验证；
- 读取 toolchain manifest，不从旧项目猜二进制；
- 长结果够用即可回答，需要更多证据时使用 cursor；
- 工具失败后依据 `reason/recommended_action` 调整，不重复盲试；
- 只有真实运行/测试过的内容才能宣称通过；
- 产物进入发送队列不等于用户已收到。

### Prompt/Skill 不应表达

- 不声称尚未上线的工具存在；
- 不要求模型通过 base64、here-doc 或超长 `-Command` 写源码；
- 不要求模型记忆宿主物理路径；
- 不把 readiness 说成执行权威；
- 不向模型解释内部 MemCore 数据库或缓存位置。

### 清理要求

每增加一条权威路径，必须列出被删除、变薄或进入短期迁移窗口的旧路径：

- 旧共享 workspace 猜测逻辑；
- output_globs 偷换 cwd 的歧义行为；
- 把所有 execution rejection 折叠成 unknown 的映射；
- 已迁移 producer 之后的全局字符硬截断；
- readiness 冻结 execution authority 的分支；
- AnySearch 1200 字 raw fallback；
- Akane 宿主内手工重建 source turn grouping 的逻辑。

不得只新增兼容层而长期保留两套行为。

## 13. Phase 7：验证与真实 smoke

### 聚焦自动化

至少覆盖：

- workspace ownership、选择、跨会话复用、archive、路径逃逸；
- write/patch 原子性、hash 冲突、hunk 失败、中文/空格路径；
- Shell Windows/Linux 参数、command_too_long、cwd、output globs；
- toolchain manifest 的真实探测和缓存失效；
- producer paging 与 cursor owner/content fingerprint；
- MemCore 多 turn source binding、开放轮、settlement、open_memory；
- search readiness、MCP/REST fallback、Markdown/JSON normalization；
- native schema 与 legacy prompt 参数一致、schema 两次构建字节稳定；
- `git diff --check`、Python compile 和相关全套回归。

### 云端真实 smoke

在隔离测试 project root 中执行，不使用生产宿主 checkout：

1. 私聊创建项目 A，写入小型多文件网页，运行语法/HTTP 验证，换会话后选择 A 并继续修改。
2. 群聊由成员 A 创建项目 B；成员 B 和其他群验证不可越权读取。
3. 触发超过 8192 字符的 Shell 命令，确认模型看到 `command_too_long` 并改用 workspace 工具完成，而不是未交付。
4. 生成长测试输出，确认当前轮分页完整、final 后卡片化、`open_memory` 可恢复。
5. 带附件的任务触发 synthetic turn，确认 provider 请求没有 `projection_source_turn_mismatch`。
6. readiness 保持 checking 时真实搜索，确认可成功或得到真实 provider 错误。
7. AnySearch Markdown 多条结果全部可读，模型能引用尾部结果。

### 桌面真实 smoke

- 目录选择器绑定含中文和空格的现有项目；
- Windows PowerShell、Node、Python、Git manifest 与实际执行一致；
- 项目外文件不可写；
- 慢任务、取消和失败状态在 UI/气泡中不假成功。

### 用户表现验收

用户应当感觉到：

- 换会话后可以明确回到之前的项目，不靠模型猜目录；
- “帮我继续改上次那个项目”在选定项目后可直接成立；
- 大文件修改不会因为 Shell 过长突然未交付；
- 工具不可用时 Akane 能说清楚缺什么、接下来能做什么；
- 搜索健康检查抖动不再表现为莫名其妙的永久不可用；
- 长结果不丢尾部，历史又不会无限膨胀；
- 模型不会再把旧 release 的问题当成当前生产问题。

## 14. 部署门槛、顺序与回滚

### 推荐切片顺序

1. Phase 0 复现测试；
2. Project Workspace 数据契约和权限边界；
3. workspace write/patch；
4. execution/toolchain/error contract；
5. MemCore request-binding helper 与 Akane 薄适配；
6. Web Search readiness/normalization；
7. Prompt/Skill 收紧和旧逻辑删除；
8. repair pass；
9. staging/云端真实 smoke；
10. 独立部署切片。

### 上线门槛

- 所有权和路径逃逸测试全绿；
- 生产 release/source/venv 只读边界实测成立；
- provider 实际 messages 证明 MemCore source turn 归属正确；
- 当前轮长结果无破坏性截断；
- 搜索 checking 状态实测可执行；
- 全部失败路径不导致静默未交付；
- 无新增第二套上下文 authority；
- 工作区无数据库、日志、缓存、构建产物或用户项目被提交。

### 回滚单位

- Project Workspace schema/选择入口；
- workspace write/patch capability offer；
- execution error/paging adapter；
- MemCore request-binding adapter；
- search readiness/normalizer。

每个单位必须可单独关闭，但关闭后应返回结构化 unavailable，不回到旧的歧义逻辑。数据迁移必须向前兼容读取，回滚不得删除用户项目。

## 15. 执行模型最终汇报清单

最终报告必须逐项回答，缺项不得标记完成：

1. 修改文件、生产代码净增减行和聚焦 commit ID；
2. Phase 0 每个故障的真实复现证据；
3. Project Workspace 的唯一权威数据源、owner/actor 隔离规则；
4. 私聊、群聊、桌面分别如何创建/选择/复用/archive 项目；
5. 云端 release/source/venv 如何被阻止成为可写项目；
6. `workspace_write`/`workspace_patch` 的最终 schema、原子性和并发保护；
7. Shell 长命令时模型实际看到的完整结构化反馈；
8. toolchain manifest 的真实样本和 PATH 来源；
9. cwd、scratch、output globs 的唯一语义；
10. 哪些硬截断被删除，哪些最终保险仍保留及理由；
11. 当前轮分页预算、cursor 绑定、失效和内容变化语义；
12. MemCore request-binding helper 的宿主无关 API；
13. attachment/synthetic turn 的 provider 实际 messages 样本；
14. context authority、audit persistence、settlement 三类失败分别如何表现；
15. settlement 阈值是否保持不变，开放轮是否逐字保留参数和结果；
16. Web Search checking/MCP/REST/Markdown 的真实执行结果；
17. 删除、变薄、迁移窗口中的旧逻辑逐项清单；
18. 自动测试数量、全量/聚焦结果、未运行项；
19. QQ 私聊、群聊、桌面、云端 smoke 的真实用户表现；
20. schema/cache 是否变化及一次性迁移影响；
21. 未触碰的 WIP 和未提交产物核验；
22. 剩余风险、回滚方式、是否部署。

## 16. 裁决标准

本执行单只有同时满足以下条件才算完成：

```text
模型知道自己在哪个项目工作
+ 文件修改不依赖超长 Shell
+ 执行环境真实可见
+ 当前工具轮证据完整可续读
+ MemCore 按真实 turn 接管上下文并在 final 后结算
+ 搜索探针不再越权阻断真实执行
+ 错误能够驱动模型继续工作而不是让系统未交付
+ 旧的歧义路径被实际删除或变成薄适配器
```

任何“字段已经存在但模型看不到”、“数据库已有记录但 provider 请求没使用”、“后端返回成功但用户表现未成立”都不算完成。
