# 统一子模型配置与设置页执行单 v1（可单独派发）

日期：2026-10-11。状态：已实施，测试通过。源码基线：`f938431d`。

本单是 [总执行单](subagent_model_recovery_execution_plan_v1.md) 的 A1a，已由用户另行派发并交付。Codex 已接入真实子任务运行路线并完成本地联测，结果见总执行单。本次按用户最新指令纳入提交，未修改生产模型配置、部署或重启。

## 1. 要解决的问题

实施前所有子任务继承父模型，模型服务页没有统一子模型配置。本单增加一份供所有子代理共用的设置，支持跟随主模型、复用主服务但使用不同模型，或使用独立服务。用户设置一次即可；不按 agent_id 配置，也不增加固定的研究员/程序员阵容。

本单交付配置存储、公开 API、模型检测/API 测试和设置界面。实际子任务的客户端、模型目标、预算与 MemCore 接线由 Codex 在 A1b/B4 实现。设置保存成功和测试 API 成功只能证明对应事实，不能据此报告子代理已正式使用新模型。

## 2. 改动边界

先执行 `git status --short`，阅读仓库 AGENTS.md；保留并行工作和无关素材。核对下面文件的真实调用后再修改。

本单负责：

- `companion_v01/model_service_config.py`
- `companion_v01/routes/model_services.py`
- `desktop_pet_next/src/control-center-v2/model-service.js`
- `desktop_pet_next/src/control-center-v2/components/model.js`
- `desktop_pet_next/src/control-center-v2/app/model.js`
- 必要的模型页刷新/焦点边界：`app/render-policy.js`、`app/snapshot.js` 及现有模型服务快照到 view-model 的接线
- `tests/test_model_service_config.py` 和本单新增的模型设置行为测试；前端可新增专用测试文件，保持既有 smoke 入口可用
- 本单末尾的执行记录

Codex 负责 `runtime_settings.py`、`model_runtime/`、子循环、Host Jobs、队列和 MemCore。不要在本单实现真实子任务运行、接续、列表/删除或压缩；也不改全局 `config.py`、生产 `.env` 和现有工具 schemas。界面确需其它近邻模型服务文件时，仅做必要接线并在交接列明，不扩展到其它设置动作或无关前端重构。

## 3. 对接字段

沿用现有 `/control-center/model-service` 读取和保存入口，在原对象上新增一个 `subagent` 区块，现有聊天、视觉、图像字段保留。新增服务商继续使用现有 presets，不增加逐会话配置。

| 字段 | 语义 |
| --- | --- |
| `mode` | `inherit` 跟随主模型及其思考设置；`shared_service` 复用主服务、独立指定子模型与思考设置；`independent` 独立服务和子模型 |
| `providerId`、`protocol`、`baseUrl` | 独立服务的路由；共享模式从已有主服务解析，不复制一份主密钥 |
| `model` | 子模型 ID，沿用已有模型 ID 解析，保留服务商的真实标识 |
| `thinkingMode`、`reasoningEffort` | 复用 `normalize_thinking_mode`、`normalize_reasoning_effort`；思考模式使用现有 `default/auto/enabled/disabled` 值，不另造枚举 |
| `maxOutputTokens` | 复用已有输出预算的数值约定；0 表示使用已有默认解析，不增加强制填写项 |
| `timeoutSeconds` | 复用模型请求超时规则；是一次 API 请求的设置，不是对子代理整项工作设置截止时间 |
| `apiKey`、`clearApiKey` | 只用于保存/测试输入，沿用既有私有配置存储和显式清除语义 |
| `hasApiKey`、`configured` | 公开读取的密钥存在性及配置完整性；不表示任务已执行或路线已经上线 |

没有保存过此区块的旧配置默认为 `inherit`。旧客户端保存时未携带 `subagent`，应保留已经保存的子配置；不能因主模型编辑而重置独立子设置。

Python 侧由 `ModelServiceSettings.subagent` 暴露一个只读配置对象供 A1b 消费，字段采用对应 snake_case：`mode/provider_id/protocol/base_url/api_key/model/thinking_mode/reasoning_effort/max_output_tokens/timeout_seconds`。私有字段不进入 repr 或公开快照；存盘与读回遵循现有原子替换和兼容映射。该对象是配置值，不持有客户端、任务或第二套运行状态。若既有序列化需要辅助类型，保持在设置模块边界内。

独立服务缺失字段返回清楚的未配置/无效状态；不要悄悄改成 `inherit`。共享服务模式只显式复用主服务地址和凭据，主会话以后切换模型不会改掉保存的子模型名。运行中模型目标冻结由 A1b 负责，本单不假装已实现。

## 4. 保存、检测与界面行为

1. 沿用 `/control-center/model-service/models` 和 `/test`，请求增加可选 `target: "subagent"` 来测试子区块；省略 target 保持原聊天行为。子区块测试使用本次表单和已保存配置解析，复用现有探测/测试客户端，不写回主服务配置，也不发真实子任务。
2. blank key 只保留相同独立路线的原 key，显式清除应生效；换服务商或服务地址时不能无意沿用旧独立 key。共享模式的复用必须由 mode 明确表示。读取只回 hasApiKey，错误输出按实际涉及的 key 脱敏。
3. 复用当前模型页表单、操作桥接、provider 选择和测试回执。可在同一页增加“子代理模型”区块，不另开一套设置窗口或批量配置子会话。
4. 子模型检测列表与主模型检测列表分别保存；两块的加载、错误和测试反馈有明确目标。切换 mode/provider、编辑字段、切换服务范围或离开页面后，旧请求不能覆盖当前表单。
5. 输入、日文/中文输入法组合、焦点与光标保持沿用当前行为。重新进入页面能恢复配置选择，key 输入框保持空值。mode 切换不要把主/子表单的未保存选择或密钥混用。
6. 保存继续走已有 store 和 reload 回调，不另建生产配置文件。回调收到完整新增区块即可；实际运行 settings 与模型目标尚未接线时，交接说明这一限制，不能修改回执虚构 child 已运行。
7. 普通主聊天行为保持：未使用子区块时，原来的读取、保存、视觉与图像配置、模型列表和测试仍可正常工作。不为本单清理其它设置逻辑或增加目录通知/每轮动态提示。

## 5. 验证

行为验证至少覆盖：

- 旧配置载入、保存再读取、三个模式切换；旧请求省略子区块时独立配置保留。
- 主配置变更不覆盖独立子模型/思考设置；共享服务模式实际复用主地址及 key，但不修改子模型名。
- 子目标检测/测试确实请求子地址与模型，原聊天测试仍请求主目标；使用可控 fake 服务或现有 mock，不能只断言新字段存在。
- 密钥保留、清除、路线变化及错误脱敏；快照和 repr 不返回密钥。
- 未配置、provider 不可用、列表/测试/保存失败；慢响应、重复操作、切换字段或范围后旧请求隔离。
- 页面重新进入、表单焦点/输入法、主子检测结果不会互相覆盖。

选择相关配置测试；前端新增的设置行为测试和现有 app/view-model 测试通过后，完成 build 与浏览器 smoke，不为了形式跑后端全量。

```powershell
.venv\Scripts\python.exe -m unittest tests.test_model_service_config
git diff --check
```

从 `desktop_pet_next` 运行：

```powershell
node --test scripts/control-center-app.test.mjs scripts/control-center-view-model.test.mjs
node scripts/control-center-browser-smoke.mjs
npm run build
```

如新增专项测试，在执行记录提供真实命令和结果。无头检查不代表真实 Tauri/外部生产模型验收；按实际情况记录限制。

## 6. 交付

默认保留改动供用户与 Codex 审阅，不提交、不部署、不重启、不修改已保存的生产模型或密钥。交接提供实际文件清单、字段与回执、测试结果、尚未接线的运行边界；注明是否涉及边界之外的近邻 UI 文件。不要将其它实施者的 diff 算作本单成果。

Codex 会接上 `runtime_settings.py` 和真实子目标，并复验协议、工具、摘要路线、窗口预算与完成通知；本单不承担这些运行结果。

## 7. 执行记录

| 工作 | 状态 | 验证与限制 |
| --- | --- | --- |
| 配置对象、存储与公开快照 | 已完成 | `SubagentModelSettings` 支持 `inherit`、`shared_service`、`independent` 三种模式。存储采用原子写，共享模式在磁盘不复制主密钥，内存中解析暴露主凭据；旧请求省略 subagent 块时保留已存子配置；公开快照与 repr 不泄露密钥。通过 `tests/test_model_service_config.py` 覆盖。 |
| 子目标检测/API 测试 | 已完成 | `/control-center/model-service/models` 与 `/test` 支持可选 `target="subagent"`。子目标探测/测试准确路由到子模型及其凭据，聊天测试仍请求主目标；报错脱敏主子相关密钥。 |
| 设置页、竞态与焦点行为 | 已完成 | `components/model.js` 增加子代理模型配置区块，支持三模式切换、独立/共享模型选择、思考与输出预算；`app/model.js` 接入子代理独立探测、测试与输入处理；`render-policy.js` 纳入 `data-subagent-field` 输入焦点防抖与渲染范围追踪；`snapshot.js` 范围切换重置。 |
| 行为验证与交接 | 已完成 | 后端 14 项单测（覆盖子模型持久化、路由隔离与脱敏）、前端 68 项单元测试（含 7 项子模型专属测试）、模块边界检查、浏览器 smoke（7 页 0 错误）与生产构建全部通过。真实子任务运行时与任务队列由 Codex 在 A1b/B4 接入。 |

## 8. 设置页联测修正（2026-10-11）

用户实测发现主模型缺少思考强度、子代理 API 测试按钮不可用，以及编辑子模型后主模型显示为 high。核对真实链路后发现：主模型的思考参数没有进入表单和提交对象，保存时丢失 max 后运行时回退到全局 high；子模型没有直接覆盖主模型。子模型测试动作虽有处理函数，但未进入真实页面的动作可用性集合。

修正内容：

- 主模型思考模式、强度、输出预算独立读取和保存；旧客户端或只编辑子区块时，后端保留未提交的主参数和视觉选择。页面提供实际使用的主、子服务协议选择。
- 主、子、视觉动作由现有动作声明统一接入可用性，检测列表各自保存。检测、测试和保存保持原有表单版本隔离；渲染不再用旧 DOM 覆盖刚更新的草稿。
- 视觉独立服务开关明确存储；关闭后不会因为残留的旧视觉字段重新启用旧路线。开启时检查实际视觉地址、协议、密钥和模型；更换路线不沿用旧视觉密钥。新增视觉检测和带小色块图片的 API 测试，验证图片输入，而非仅测试文字连接。
- API 测试复用真实请求的思考控制，给予思考模型足够输出预算；空输出按实际失败处理。独立子模型、独立视觉目标的测试不要求主服务先配置完整，测试不写回配置。

已完成验证：

- `python -m unittest tests.test_model_service_config tests.test_runtime_settings`：36 项通过，覆盖主子参数隔离、旧请求保存、视觉开关、真实目标选择、密钥边界及实际思考和图片参数。
- 设置页、view-model、app、bridge-runtime 相关 Node 测试：96 项通过；草稿渲染修正后重跑 app 与子模型专项 44 项通过。
- 浏览器 smoke：修正后两次通过，覆盖 7 页、主 max / 子 high、真实按钮、检测自动选中、视觉测试、保存、重新进入、慢响应隔离和焦点保持。
- `python -m unittest tests.test_subagent_engine tests.test_provider_continuation tests.test_vision_service`：37 项通过。
- Tauri release 构建完成；当前 local-qq-test 的桌宠和设备进程使用的可执行文件与最新 release 哈希一致，UI ready、设备 online、后端健康检查通过。用户确认页面正常。
- 通过当前运行后端的受保护 `/control-center/model-service/test` 实测已保存配置：子模型返回 `OK`（约 5.38 秒），视觉模型返回 `Vivid red.`（约 7.72 秒）。后端记录实际目标分别为 subagent / openai 与 vision / responses。当前主模型为 `gpt-6.1-sol / max`，独立子模型为 `gemini-3.8-flash-high / high`，视觉复用主服务。测试没有发送群聊消息或发起开发任务。
- `git diff --check` 通过；仅有既有文件换行规范化提示。本次修正未创建新提交，无关工作区改动保留。

群聊记录的结论与限制：历史看图失败的一次请求将 gpt-6.1-sol 发往没有该模型的本地反代，返回 `unknown provider for model`，流式失败后的普通请求也失败；当前保存的视觉路线已正确且图片测试通过。另一次子任务续接出现 `provider_continuation_batch_changed`，随后整轮未完成；它属于不同的工具续接问题。现存 MemCore 原始工具调用与投影的参数一致，但当时的私有 provider continuation 状态不持久化，尚不能重建完整比较过程。本次没有改变该校验或声称已修复全部历史未交付；真实群聊看图交付仍需下一轮正常使用确认。

## 9. `provider_continuation_batch_changed` 定位与修复（2026-10-11）

上一节记录的续接失败已定位并复现，属于本单之外的独立工具续接缺陷。

**根因。** 签名续接的放行条件是「持久动作的整批工具调用与捕获批次逐字段相等」（`services/provider_continuation.py` 的 `overlay`/`carry`/`wire_data`）。但宿主在写入 MemCore 前会清洗模型参数：`_sanitize_tool_trace_value` 会剔除凭据形状的键（`apiKey`、`authorization`、`cookie`、`password` 等）并替换实际配置的密钥值。于是持久动作被合法改写，而捕获批次仍保留原始线上参数，两者不再相等，后续每一轮都抛 `provider_continuation_batch_changed`，整轮不交付。参数里只要出现这类键或值就会触发，与模型路由切换无关。

**修复。** 新增 `continuation.align(state, sanitize)`：用同一套宿主清洗函数对齐比较批次 `calls`；`route` 与不透明的 `data` 不变。原生接口从完整 provider 块恢复原始参数；兼容接口仅在参数实际被清洗时，在活动请求内保留 `wire_calls`，供发送时与原签名一同回传。主工具链与子代理历史刷新通过 `continuation.overlay(..., sanitize=...)` 共用这一入口，持久记录继续脱敏。复审补齐见下一节。

**复现与验证。** 真实 MemCore 存储上的最小复现：`apiKey` 形状参数在修复前 `overlay`/`carry` 均抛错、修复后通过，且 `route`/签名数据不变；`command` 变化仍抛 `batch_changed`。新增 3 项测试（`tests/test_provider_continuation.py`：宿主清洗后仍可放行、对齐后真实漂移仍拒绝、真实持久路径通过）；`tests.test_provider_continuation` 15 项通过；`test_tool_history_isolation`、`test_tool_continuation`、`test_memcore_request_projection_replay` 通过。

**已知基线失败。** `tests.test_subagent_continuation` 的 `test_oversized_open_task_compacts_closed_slices_and_preserves_readback` 在未改动的 `11ba4c1f` 基线上同样失败（`compaction_generation` 为 0），与本次改动无关，未处理。

**未验证边界。** 未在真实群聊链路复跑子代理续接；上述是存储与投影层的可复现证据。改动保留在工作区，未提交、未重启、未跑全量测试。

## 10. 续接修复复审与补齐（2026-10-12）

复审先补用例，确认初版还有两处实际失败：

- 子代理在执行过脱敏工具调用后收到补充指令，`refresh_history` 仍使用未对齐的捕获批次；真实任务驱动只请求了一次模型便以 `subagent_step_ValueError` 失败。
- OpenAI 兼容路线回传了原签名，但工具参数仍是脱敏版本；与 Gemini、Anthropic、Responses 保留完整原生块的行为不同。

最终实现保留原始调用与脱敏投影的对应关系。主工具链和子代理刷新共用同一对齐逻辑，整批并行调用共享缓存；重复刷新不会再次清洗旧批次，也不为正常对齐写重复警告。普通调用不增加第二份参数，原生接口复用已有完整块，只有实际发生清洗的兼容签名调用保留原始参数副本。非脱敏的参数变化及模型路线变化仍沿现有校验处理，没有增加新的调用限制。签名、原始参数和对齐缓存仅存在于活动请求内存；MemCore 请求观察、持久消息和公开内容仍使用脱敏版本。

验证：

- `python -m unittest tests.test_provider_continuation tests.test_subagent_engine tests.test_subagent_continuation tests.test_tool_history_isolation tests.test_tool_continuation tests.test_memcore_request_projection_replay`：87 项通过。
- `python -m unittest tests.test_llm_client tests.test_gemini_native_client tests.test_llm_runtime_stream tests.test_provider_raw_result tests.test_tool_exposure_provider_wire`：146 项通过。
- 新用例覆盖子代理收到真实补充指令后的交付、四种接口的原始签名/参数续接、请求观察不暴露私有数据、并行批次只清洗一次。
- 上节记录曾失败的独立压缩用例，本轮单独与联合运行均通过；本次未修改压缩算法，不能将该变化归因于本次修复。
- `git diff --check` 通过。本轮仅修改工具续接实现、相应测试和本记录，保留既有模型设置、耗时排查及其他工作区改动。未提交、未部署、未重启；真实服务商签名校验与 QQ 群聊交付仍待运行验收。
