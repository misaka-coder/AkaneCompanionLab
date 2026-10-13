# 工具运行轨迹三项修复与验证报告

日期：2026-10-11。

任务来源：`docs/tool_runtime_trace_review_v1.md` 第 1～3 项。本文记录本次修复，保留原审计文档不覆盖。

## 交付状态

- 三项代码修复及隔离验证已完成，变更留在当前工作树。
- 未 stage、未创建提交，未重启或更新任何生产后端/桌宠进程。
- 没有修改生产数据库，没有清空旧任务，也没有改变 MemCore 检索、压缩规则或角色回复字段。
- 原先已有的 5 个已修改文件在任务开始与交付前 SHA-256 完全一致；既有其他材料及未跟踪文件保留。
- 桌宠前端构建成功，但未打包、替换运行中的 EXE。本报告不是上线验收。

## 1. 重启后已读结果重复完成唤醒

### 根因

原生工具历史可以持久恢复，但待确认的结果消费关系只保存在本轮工作对象中。注册表重建后，历史中已经进入恢复请求的终态结果没有重新关联到待确认消费记录，旧任务仍可能进入空闲完成通知。

另外，部分查询或调度失败路径曾在返回结果时确认消费/投递；返回工具结果不等于模型已经收到它，失败或取消的后续请求不应该提前确认。

### 修复

- 在已有请求观察器外接入结果消费关系恢复，不替换冻结投影的原权威。
- 以供应商实际请求内容为边界，核对来源投影、调用标识、结果内容及用户/会话、角色、通道范围。
- 支持原生结果、终态状态查询、完成边界事件及历史中的结果；识别结果呈现与真正终态的区别。
- 每个请求尝试替换待确认集合，而不是一直累加：前一个失败请求包含的结果不能被后一个省略它的成功请求误确认。
- 只有有效模型回应后才持久确认消费；失败、取消、未实际投递、仍运行中、不同来源/范围和已撤销的结果不提前确认。
- 未读任务仍保留正常完成通知，不能用清空旧任务来消除重复唤醒。

### 核心文件

- `companion_v01/host_completion_work.py`
- `companion_v01/host_tool_job_runtime/runtime.py`
- `companion_v01/host_tool_job_runtime/request_receipts.py`
- `companion_v01/engine_services/turn_completions.py`
- `companion_v01/engine_services/tool_history_projection.py`
- `tests/test_tool_completion_restart.py`
- `tests/test_tool_completion_request_guards.py`

验证覆盖：持久存储及注册表重建、有效同步/流式回应、未读通知保留、运行中查询、供应商失败/重试/取消、实际请求省略结果、来源不足、内容不一致、终态分页、不同协议/名称映射、跨范围与撤销、真实隔离 Shell 结果恢复。

## 2. 桌宠后台与恢复任务缺少中途反馈

### 根因

排队/恢复及非队列插件路径原先调用非流式处理，只在最后投递完整 frame，没有持续传递工具轮文字和执行状态。前端旧插件队列还会等待聊天/TTS 空闲；若直接把同一后台流的中途事件塞入此队列，最终事件可能被自己的处理中状态挡住。

### 修复

- 生产后台/恢复和非队列插件路径复用已有引擎事件流及桌宠投递通路。
- 复用现有开始、等待心跳、完整文字/配音单元、工具工作状态及阶段事件，不新增模型回复字段。
- 前端对同一后台流维持一次聊天流消费，使用现有文字/配音分阶段去重与最终缺失补齐逻辑，不逐帧重建聊天状态。
- 以会话、用户、角色、运行范围和事件顺序隔离旧任务；重复、迟到、切换范围后的事件不能覆盖新场景。
- 排队任务等待原前台任务结束，但同一后台流的进度不能等待它自己的聊天/TTS 空闲。
- 失败、停止、超时与销毁释放展示状态；保留中途内容，但不把它当作任务最终成功。
- 单条通知的研究过程文字仍不单独展示；工具文件动作保留既有最终交付与独立撤销范围，不重复执行。
- 完成批次在核对仍有效的输入之前不发送初始进度，已撤回的通知不会先冒出恢复气泡。
- 没有事件通路的旧内嵌调用保留最终 frame 兼容路径。

### 核心文件

- `companion_v01/routes/think.py`
- `companion_v01/routes/desktop_turn_events.py`
- `companion_v01/bot_http_routes.py`
- `desktop_pet_next/src/pet/background-turn-stream.js`
- `desktop_pet_next/src/pet/chat.js`
- `desktop_pet_next/src/pet/plugins.js`
- `tests/test_desktop_background_progress.py`
- `desktop_pet_next/scripts/background-turn-stream.test.mjs`
- `desktop_pet_next/scripts/pet-runtime-browser-smoke.mjs`

验证覆盖：中途文字先于下一工具步骤、慢步骤心跳不重复推进、重建收件队列后的继续处理、最终交付后才确认收件、空/失败终态、进度投递失败、已撤销结果、独立文件撤销、不同会话/角色、阶段重放、配音单元去重、停止/销毁、前台忙碌与连续后台任务。

真实浏览器检查使用真实页面/DOM/controller 及隔离设备边界，确认恢复气泡、中途文字、工具状态、最终完成和迟到帧拒绝；没有向生产会话派发测试任务。

## 3. projection_immutable_conflict

### 根因证据与边界

只读核对保留账本与请求审计：完成通知轮已于 13:41:01 关闭，13:41:03 的后续请求位置与该轮的用户及最终助手投影指纹吻合。用户投影已冻结，最终助手投影已完成；终态重放再次要求合并请求冻结状态，试图改变最终投影，被不可变约束拒绝。

生产同形的隔离调用链能在原方法下复现该冲突，不能只因工具调用/结果配对完整就认定警告无害。原警告行本身未在所查保留日志中找到，其时间来自源审计文档；定位依靠匹配的请求指纹、终态账本及隔离复现。所核对的投影版本是现存账本版本，不冒充当时未单独保存的描述符版本。

### 修复

- 只读核对终态、来源、索引、版本、状态、声明载荷与实际请求载荷。
- 只有经过核对、完全一致的已关闭/中止轮重放才跳过重复冻结；不覆盖、重写或提升终态投影。
- 停止/完成恰好赢过原子提交的竞态也必须再次严格核对，才能归类为同一重放。
- 真正的内容、来源、版本、状态或索引差异仍保留原失败链。
- 跳过回执明确保留“未记录”，不虚构新的冻结审计。

### 核心文件

- `companion_v01/memcore_integration/request_replay.py`
- `companion_v01/memcore_integration/projections.py`
- `companion_v01/engine_services/memcore_turn_pipeline.py`
- `tests/test_memcore_request_projection_replay.py`

验证覆盖：首次冻结、同观察器重试、新管理器恢复、新工具后缀、完成/停止后的重放、多请求/多轮、提交竞态，以及真实内容/来源/版本/状态/索引和跨范围冲突。

## 统一验证结果

所有数字按真实运行结果记录；专项是联合回归的子集，不能重复相加。

| 检查 | 结果 |
| --- | --- |
| 四个后端专项模块联合运行 | 58 项全部通过 |
| 选定 20 个后端模块联合回归 | 267 项全部通过，包含上述专项 |
| 后台事件前端测试 | 11 项全部通过 |
| 既有 pet runtime smoke | 通过 |
| pet runtime 静态/边界检查 | 44 个模块检查通过 |
| 真实浏览器 smoke（含新增后台事件边界） | 通过，0 page errors |
| 桌宠前端 npm run build | 通过，1245 个模块构建成功 |
| Python 语法编译与 git diff --check | 通过 |
| 原先 5 个已修改文件 SHA-256 | 与开始时完全一致 |
| 暂存区 | 空 |

构建存在 npm 的 `store-dir` 环境配置提示，不影响本次构建成功。环境没有 Ruff，本次没有声称通过 Ruff 检查，也未为此安装依赖。

### 可复跑命令

使用项目已有兼容 Python 环境：

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_tool_completion_restart tests.test_tool_completion_request_guards tests.test_memcore_request_projection_replay tests.test_desktop_background_progress
```

选定联合回归：

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_tool_completion_restart tests.test_tool_completion_request_guards tests.test_memcore_request_projection_replay tests.test_memcore_runtime_contract_guard tests.test_memcore_manager_modules tests.test_desktop_background_progress tests.test_tool_batch tests.test_tool_foreground_execution tests.test_tool_completion_boundary tests.test_host_completion_batch tests.test_desktop_chat_waits tests.test_plugin_agent_events tests.test_multi_bot_desktop_routes tests.test_desktop_completion_file_scope tests.test_plugin_completion_revocation tests.test_completion_model_cancellation tests.test_completion_satellite_cancellation tests.test_llm_runtime_stream tests.test_prompt_builder tests.test_turn_mainline_contract
```

在 `desktop_pet_next` 下：

```powershell
node --test scripts/background-turn-stream.test.mjs
node scripts/pet-runtime-smoke.mjs
node scripts/pet-runtime-check.mjs
node scripts/pet-runtime-browser-smoke.mjs
npm run build
```

失败注入测试中出现的不可变冲突、错误索引、失败投递等警告是负例断言的一部分；运行结果以上表的实际测试终态为准。

## 扩大检查中保留的基线失败

没有为了得到全绿结果而修改这些测试、配置或无关生产逻辑。选定 267 项通过不代表仓库全部测试通过。

1. 初始六模块基线 146 项中，`test_backend_route_modules` 的 `test_qq_router_poke_notice_runs_llm_as_normal_user_message` 失败：旧断言要求没有 `actor_stable_id`，当前实际载荷保留经过核验的发送者身份。该失败发生在本任务改代码之前。
2. 扩大到包含 `test_session_completion_batches` 的 212 项检查中，`test_retry_waiting_head_cannot_be_overtaken_including_steer` 失败：期望 `deferred`，实际 `claimed`。相关收件存储与测试文件没有本轮差异，单独复跑也失败，未在本任务顺手改队列调度。
3. 包含 `test_operation_projection_policy` 的 67 项检查出现 1 个失败、6 个错误（包含子测试错误）：旧夹具未初始化 `_retrieval_mode`，另有默认投影策略断言与当前默认值不一致。以 HEAD 的原投影方法只在内存替换后，单独运行该模块 20 项仍出现同样失败/错误；相关配置/生命周期/测试文件未改。
4. 投影子任务扩展检查 243 项中有 3 条既有失败：历史提示附加文案、供应商不可达重试次数、恢复进度事件断言。该子任务以 HEAD 原修改方法在内存对照，同样失败；记录为辅助扩大检查，不混入主任务最终 267 项通过数字。

## 未完成的现场验收与后续边界

- 未重启生产实例，所以不声称新代码已被正在运行的后端/桌宠加载。
- 未做更新后的真实长任务中断/重启现场演练，也未验证物理音频设备或外部供应商实际响应。
- 浏览器/队列/存储重建夹具证明对应边界，不等于断电、网络波动或所有历史压缩状态都已现场验证。
- 已核对的终态投影冲突得到窄修复，不声称其他历史冲突都无害。
- 后续在另行允许更新后，建议现场观察：恢复任务中途反馈、已读结果不重复唤醒、未读结果仍通知、停止/切换场景后无旧回复覆盖，以及真实冲突仍保留失败原因。

本轮涉及 20 个实现/测试文件，另附本报告；源执行单、原先的提示词/解析修改及其他已有工作均保留。

## 交付复核（2026-10-11）

### 复核中补上的遗漏

恢复消费关系原先只识别直接工具名。实际执行会把 `capability_invoke` 路由到其 `capability_id`，而持久原生历史保留包装名和外层参数；按包装名查任务会漏掉已经进入模型请求的结果。这次主任务确实使用了 46 次包装调用，不是仅为假设场景添加支持。

隔离复现中，模型已经有效回应，但任务仍为 `model_consumed_by=""`、`completion_status="pending"`。新增的四个正向回归在修复前全部失败。复核已补上：先核对原始来源、调用和结果配对，再解出真实目标及业务参数，沿用原来的任务查找和终态判断，不执行工具、不改写历史。

新增回归覆盖 OpenAI/Anthropic 包装调用、包装入口别名、终态分页查询，以及运行中查询和无效包装参数。分页结果确认消费后，后续页面仍可正常读取。

复核验证：选定的 20 个后端模块 **273 项全部通过**（原 267 项加 6 项）；前端后台流 **11 项通过**；真实页面浏览器 smoke 通过、0 page errors；前端 build 通过、1245 个模块。原先五个提示词/解析相关已修改文件的 SHA-256 仍与任务基准一致。未提交、未重启、未修改生产数据库。

### 完整任务轨迹核对

- 主任务最终正常关闭；132 次原生调用与 132 条结果配对。
- 73 个原生调用批次中，30 个包含 2～4 个调用；其中 22 个批次的任务起止时间确实重叠，能证明宿主实际并发执行。不是只看调用数组作推断。
- 5 个助手工具消息包含可见进度正文；中途文字和并行机制在本轮均有实际使用。频率仍由模型决定。
- 四个子任务中，一个成功，两个连接错误，一个请求超时。失败发生前有两个子任务写入了代码；主任务接手整合并完成统一验证，不能把这称为四个子任务都成功交付。
- 四个子任务的终态均已返回并由主任务确认消费。父任务下剩余 30 条未标记消费的任务均为 `silent/current_turn` Shell 记录，不能直接认定为 30 个待唤醒通知。

## 待处理：长任务后的响应延迟与子代理接续

用户要求先记录这些问题。以下时间为本机时间，生产存储仅以只读方式核对；没有为取证向聊天模型发送测试消息。

### 已确认的延迟证据

| 时间/阶段 | 观察 |
| --- | --- |
| 17:14:06 | 长任务关闭 |
| 17:14:11 → 17:18:08 | 后续聊天轮已打开，约 237 秒后才出现第一次聊天请求构建审计；不能把这段全算作聊天供应商响应时间 |
| 17:18:35 | 第一次压缩成功；上下文估算 78598 → 59642 token，原始记录 67259 → 47450 token |
| 17:19:23 → 17:23:21 | 队列中的一条被动图片消息处理约 238 秒；批次日志耗时 235898.5 ms，其余步骤另计 |
| 17:20:27 → 17:23:50 | 新的正常聊天等待队列约 203 秒，再开始处理；此时前方有被动消息积压 |
| 17:23:33 | 第二次压缩成功；上下文估算 61720 → 15086 token，原始记录 49528 → 2078 token；该批包含一个长任务轮的 291 条来源记录 |

因此，压缩确实工作了，且长任务记录需要两批才被收起；本次不能认定为“压缩失败导致上下文一直不降”。同时存在请求构建前长等待和被动消息队列阻塞，不能归结为同一种上游慢。第一次后续聊天还有两次模型请求及工具步骤，模型本身的耗时需与上述等待分别计算。

`qq_session_queue.py::_handle_admitted` 在处理一个被动批次时，依次准备附件/转发、取得会话执行权、入库，再推进后面的队列项。图片附件注册本身按设计只创建 pending 记录并提交后台处理；**现有汇总日志不足以证明这 236 秒具体花在下载、注册、会话锁、入库还是维护调度**，不能仅凭图片消息的类型就断言是同步下载。另一个图片批次也记录了约 123 秒处理时间，值得单独定位。

下一步优先补齐现有调用边界的轻量耗时：被动内容准备、附件注册、会话执行权等待、记忆入库/调度、上下文读取与投影、请求预算处理。只记录阶段和数值，不记录正文、定位凭据或密钥；用一次真实长任务后的轨迹确认具体等待点，再做小范围修复。聊天预算路径仍存在同步压缩入口，但没有证据证明本次 237 秒就是走了该入口。

### 子代理需要继续处理的地方

1. 连接错误、请求超时会结束子任务；此次主任务通过重新派发或接手来继续，没有自动沿原子会话恢复执行的证据。应区分短暂传输失败、主动停止和业务失败，研究最小重试/接续路径，避免每次丢掉已建立的任务上下文。
2. 失败前已经写入的代码仍留在共享工作树；失败报告应尽量附已完成步骤、修改文件和验证状态，让主任务明确哪些工作可以接续。不能把留下代码当成已验证交付，也不应在失败时清掉它。
3. 本次错误记录只有连接错误/请求超时，无法据此判断是 PinAI、网络代理还是本地取消。需要在现有诊断中保留可区分的传输阶段与错误类别，避免盲目调大超时或无限重试。

这些延迟和子代理改进尚未实施；包装调用的消费恢复修复已完成。现有三项交付的验证通过不代表上述延迟问题已经解决。
