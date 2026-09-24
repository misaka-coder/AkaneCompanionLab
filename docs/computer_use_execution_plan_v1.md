# Akane Computer Use 设计与执行单 v1

> 2026-09-21 后续易用性改进见 [执行单 v2](computer_use_refinement_execution_plan_v2.md)：优先契约一致性、引用管理和事实呈现，避免模糊判定引入新的硬限制。本单继续保留原设计与实现背景。

> 2026-09-20。状态：已进入实现和验收；本单保留设计目标，实际代码覆盖和实测结果见 [实现与验收记录](computer_use_acceptance_v1.md)。原生输入已在专用真实窗口验证，个人 Chrome 已连接并读取 AX；尚未验收的步骤不视为完成。未进行 QQ 外发。

关联：[browser_page 混合自动化执行单](browser_page_hybrid_automation_execution_plan_v1.md)。网页执行器继续由原执行单维护；原生桌面能力以本单为准。原 QQ 记录保留在原文，作为另一会话报告的案例，不冒充本轮复测结果。

## 1. 先确定的方向

在现有项目新增一个模型可调用工具 `computer_use`，以 `action` 区分操作。Windows 执行放在现有 Tauri/Satellite 设备侧；宿主负责契约、授权、模型输入和任务结果。复用 CapCore、MemCore、能力发现和图片通道，不另建 agent 主循环。

- 网页优先用 `browser_page`；桌面应用、原生对话框用 `computer_use`。同一任务可以交接，但两边的窗口/观察/截图标识互不通用。
- 用户只想看屏幕时，保留现有观察能力；允许观察不自动开启桌面输入。
- 先做普通权限、单目标窗口的闭环；包括目标窗口自己打开的保存对话框。后续再做跨应用任务、多屏和拖拽。
- 不依赖用户安装 Codex 或 `@oai/sky`。学习其公开交互契约，用现有 Windows 技术栈实现 Akane 的执行器。
- 首版使用结构化参数，不增加任意 Python/JS 脚本执行入口。公开 OpenAI 文档同时介绍代码执行、computer tool 和自定义 UI 工具；我们选择自定义工具以贴合现有授权和多模型接入，不宣称官方仅推荐这一种做法。[来源 S2]

## 2. “增量提示词”到底借鉴什么

### 已核实的机制

Codex 的技能发现采用 progressive disclosure：先暴露技能名和用途，使用时才读完整 SKILL.md，相关参考文件按需读取。[来源 S1] 本机 computer-use 包的 SKILL.md 很短，另分 guidance、API、confirmations 三份参考文档；可访问的包内没有完整原生执行器源码。

当前会话的统一 CUA 接口也明确要求：首次选择表面时返回接口文档与初始状态，读完后再行动。这是接口自描述的证据，不能据此推断其内部缓存算法。它与另一个入口 `@oai/sky` 的 Windows API 是不同接口，不能混用字段或把一个入口的能力开关推断为另一个入口可用。

因此至少区分三层：**按需加载使用说明、追加当前观察、设备保留执行会话**。没有证据表明 Codex 通过每轮改写系统提示词实现桌面控制，也没有证据表明返回的 UI 树始终是增量 diff。

### Akane 的具体设计

| 层级 | 内容 | 加载/更新规则 |
| --- | --- | --- |
| 能力目录 | 名称、用途、在线状态、契约版本 | 复用 capability_search/load 与现有常驻/按需配置 |
| 使用说明 | 窗口选择、单步操作、失败恢复、字段语义 | 按 `contract_version + guide_version` 加载；当前可见投影已有同版说明时不重复追加 |
| 应用提示 | QQ 的发送键、编辑区焦点等已验证经验 | 需要时才加载，记录适用版本；不保存绝对坐标，不替代最新观察 |
| 当前观察 | 目标窗口、截图、控件、焦点、动作结果 | 每次操作后追加到工具结果，不改稳定系统提示或角色提示 |
| 执行状态 | 窗口映射、观察缓存、控制租约、调用状态 | 保留在宿主/设备端；模型仅持有有范围限制的标识 |

“说明已加载”“工具可执行”“用户已授权”是三个独立状态。文本教程不能让离线设备变在线，也不能充当授权。

沿用现有 `capability_discovery.py` / `capability_contracts.py` 的契约加载与调用路径，不建立第二套能力目录。当前 `capability_exposure_service.py` 仍含冻结契约与 compaction pending 状态：实现时必须实际验证新工具在按需模式下可经已存在的加载/调用入口执行，不能只追加教程后就宣称立即可调用，也不能要求用户等压缩才能开始桌面任务。

说明的去重以**当前模型可见投影**为准；会话压缩后说明若已消失，应在下一次使用时重新展开，不能用全局“曾加载过”标志一直跳过。说明内容来自受控宿主资源，网页、聊天消息和 UI 树不能注册教程或覆盖宿主规则。

### 图片、缓存与成本

- 工具 schema 与基础说明不包含当前窗口标题、设备路径、截图编号或时间戳；这些属于本轮后部观察。
- `model_image_inputs` 必须真实送达当前执行模型；仅有 screenshot_id 不代表已看图。模型无视觉能力时，只允许有明确控件证据的 text 操作，不能继续坐标操作。
- 自动截图采用会话内临时媒体，不能直接复用会登记 `gen_*` 的 `register_desktop_screenshot`。显式导出截图时才登记文件。
- 默认每步传一张最新图，需要对比时显式允许两张。设备复核用的额外截图不发送给模型。不能把现有“单次最多五张”当成完整回合预算已落实。
- 保留调用/结果配对和 provider continuation；持久 MemCore 保存动作事实、文本观察与媒体省略标记，不保存原始像素或凭据。
- 实现验收检查连续三步的最终模型请求，记录实际图片数、文本量、耗时和新增说明次数。少量请求拦截即可，不靠反复付费模型试跑。
- 不承诺固定缓存命中率；说明追加、模型切换和供应商缓存策略仍会影响命中。

## 3. 从公开 Computer Use 接口学到的边界

| 本机公开接口事实 | Akane 的采用方式 |
| --- | --- |
| `sky` 通过 list_apps/list_windows 返回窗口对象，再选择目标 | 返回不透明 window_id；不让模型猜 HWND/PID，也不凭标题唯一性认定同一窗口 |
| get_window_state 默认截图，默认 accessibility 为 null；文字需 include_text | 原生工具默认 `visual`，UIA 可用的任务按需用 `text/hybrid`；不照搬网页每次强制 AX+图 |
| 截图数组为 screenshots，单项含 id、尺寸和可能的区域原点 | 为主窗口、菜单、弹窗各保留截图区域身份，不把 screenshots[0] 永远当整屏 |
| UIA 提供 tree/focused_element/selection/document_text 等 | 对焦点、选择和可编辑状态建结构化证据；RootWebArea 退化标记 `ax_status=limited` |
| 文档要求观察后再决策，每次输入后刷新 | 首版一个工具调用只做一个语义操作，再自动观察；不允许模型跨未知状态预排一串点击 |
| WGC 截图可观察被遮挡窗口，但输入要验证实际目标 | “看得见”与“现在可点击”分开；遮挡、最小化、前台不符须恢复或返回阻塞 |
| type_text 是文字，press_key 是控制键 | 换行/Enter 的提交语义不混进输入操作；优先 UIA ValuePattern，缺失时使用受控输入 |

QQ 仅暴露 RootWebArea 的记录说明该版本/该观察路径能力有限；不能推断所有 QQ 版本都没有 UIA，也不能推断根节点下存在可用焦点控件。AX 缺失时，可依据清楚的新截图确认编辑区域与视觉焦点；无法确认则返回 `focus_unverified`，不因标题里有群名就直接打字。

## 4. 工具契约与状态

建议首版动作：

| action | 用途与主要约束 |
| --- | --- |
| list_windows | 按应用/标题查询当前可选择窗口，分页；不默认枚举整个安装软件清单 |
| launch_app | 打开已识别的安装应用或用户明确指定的程序；随后枚举实际窗口，不把进程启动当成界面已就绪 |
| select_window | 选择已枚举目标并建立控制会话；必要时激活，之后返回观察 |
| observe | visual/text/hybrid；读取当前窗口、焦点及证据 |
| click | element_id 或 screenshot_id + 坐标，二选一，必须绑定 observation_id |
| type_text | 绑定当前焦点证据；返回输入效果验证，不能隐含提交 |
| press_key | 受限按键或组合键；Enter 等按当前应用/目标动作分类审批 |
| scroll | 必须绑定具体滚动区域或截图内位置；随后重观察 |
| status | 查询 operation_id/control_session_id，不执行输入，用于超时恢复 |
| stop | 取消待执行输入并释放租约；本地停止按钮不依赖模型调用这个动作 |

窗口标题不是稳定身份；本地绑定 `device_epoch + HWND + process_start_identity + window_generation`，对模型只公开受会话约束的引用。关闭、重建、设备重连时使旧引用失效。

观察至少返回：

```text
control_session_id, observation_id, window_id, device_epoch, captured_at
foreground_matches, window_bounds, client_bounds, dpi, coordinate_space
ax_status, focus_evidence, text_complete, text_cursor, elements
screenshots[{screenshot_id, region_id, width, height, origin, scale}]
action_state, observation_state, reason, next_action
```

- `action_state=not_started/executed/unknown`；`observation_state=complete/partial/unstable/failed`。输入 API 返回成功只代表动作已执行，业务结果独立验证。
- observation_id 同时绑定窗口、设备、会话及布局版本。任何输入/激活/切窗后都消耗旧操作证据；只读分页不消耗证据，但分页中的旧索引不能复活失效控件。
- 图片坐标定义为所选截图区域内的逻辑像素。宿主保存截图区域→窗口/屏幕物理像素转换，明确是否包含标题栏、DPI、负坐标显示器和裁剪缩放；不套用 browser_page 的 CSS 像素约定。
- 坐标证据有独立的短 TTL，执行前验证窗口几何、命中窗口/控件和目标区域画面；TTL 不是新鲜性的唯一判据。截图前后状态不一致时只做一次有界重采集，仍不稳定就报告 unstable。
- `focus_evidence` 区分 UIA、视觉确认和 unknown；当前窗口在前台不等于编辑框聚焦。文字输入后重新核对草稿，失败/超时不能整段盲目重输。
- screenshot_id、element_id 与审批 ID 都由宿主产生并校验，模型不能在参数中自称 approved/verified。

## 5. Windows 执行层及并发

沿用 Rust/Tauri 与 windows crate。优先实现 WGC 窗口捕获、UI Automation 控件读取/操作、SendInput 坐标和按键回退；不把仅限主屏的现有 GDI 截图直接升级宣称为窗口捕获。[来源 S3–S6]

- WGC 初始化、窗口关闭/缩放、设备丢失、空帧与内容尺寸变化须有明确状态；被遮挡可捕获不等于最小化/受保护窗口始终可捕获。
- UIA 使用独立、长驻的 MTA 工作线程，不占 Tauri UI 线程；注册与移除事件在所属线程完成。对不响应的目标使用有界查询；如阻塞调用无法取消，按需要隔离该查询，不能让停止按钮等待 UIA 返回。
- SendInput 不能跨越 Windows 完整性级别限制；输入事件数与界面效果分别核验。不对管理员窗口/UAC/锁屏作首版成功承诺。
- 同一交互桌面只允许一个控制租约。聊天会话隔离不能阻止物理鼠标键盘互相干扰；QQ、桌宠和其它任务同时申请时排队或 busy，不允许交叉输入。
- 用户真实输入、切换前台或点击停止时暂停/撤销租约；区分自身注入输入与用户输入，不能因自己的 click 触发自我取消。不要在用户接管后自动抢回焦点。
- 停止通道独立于动作队列；先拒绝新输入，再取消等待和观察，最后释放自己按下的键/鼠标键。已经发送的消息无法靠 stop 撤回，必须报告已发生事实。
- 设备断线/宿主超时延续现有 Satellite 的 execution_unknown 语义，通过 status 和新观察恢复，禁止重放发送。
- 复用 invocation_id，但加强幂等边界：输入前先写入 executing 和参数摘要；重复/并发请求返回同一状态；结束后记录终态。当前终态 ledger 不能直接视为执行中的防重放已完成。设备重启后的旧 epoch 请求拒绝，不重放无法确认的旧输入。

## 6. 审批、草稿与真实完成

现有 QQ 实践里的“即使预先说过，也必须发送前再确认”来自所用 Codex Windows skill 的 confirmations 文档，是该运行环境的策略，不是 SendInput 或 Computer Use 技术本身的要求。Akane 由现有 CapCore 和用户配置决定有效策略，不能把第三方 skill 整套策略当作新的宿主权限来源。

2026-09-21 按用户明确要求修正：所有桌面动作统一服从现有能力权限设置，按真实发起人读取具体能力覆盖或 `ops` 家族模式。完全访问/直接允许无需额外的发送审批，UIA 业务识别不能另行构成权限门槛；请求审批模式才展示目标会话、完整内容/附件、动作及观察并请求具体批准。已批准的同一具体操作校验授权后执行，内容或目标变化使审批失效。单步、连续流程后续步骤和恢复执行必须一致，不另建独立桌面权限开关。

审批绑定 `device/window/session + action + target + payload_digest`。等待审批期间可能失焦或切换群聊，批准后重观察并核对；无需把每次新截图本身当作重新审批理由，但不能把对旧群聊的批准用于新群聊。

输入文本也可能触发联网联想、自动保存或其它提交动作，不能承诺所有应用的“未按 Enter”都没有外部影响。模型提供动作意图，宿主结合目标状态和许可范围判定；敏感发送步骤不能只靠模型把 action 标成“普通点击”来绕过检查。首版先限定已验收应用/工作流；无法可靠区分提交点的场景返回需接管或需明确授权。

消息场景的结果分层：

| 阶段 | 需要的证据 |
| --- | --- |
| observed | 目标会话、编辑区已观察；仅看到 QQ 窗口标题不够 |
| typed | 新观察中存在预期草稿，焦点/内容合理；输入 API 成功不够 |
| submitted | 已执行发送动作，记录对应 operation_id；尚不宣称送达 |
| submission_observed | 刷新后看到匹配内容/目标的已发送界面，且没有失败/待发送提示；不等于收件人收到 |
| delivery_confirmed | 应用提供可归属到这条消息的明确送达回执；没有回执则 unavailable，不能强凑终态 |

这些是任务效果证据，不能与通用 action_state 混成一个布尔值。当前 QQ 记录只支持 typed；本地出现气泡也不能直接升级为 delivery_confirmed。发送结果不明时查询/观察，不能通过再发一次“确认”。保存文件则使用独立的目标路径与文件存在/内容验证，不套用消息送达状态。

## 7. 代码落点与模块边界

以下新增路径是建议模块，不是现有文件：

| 位置 | 职责 |
| --- | --- |
| `companion_v01/computer_use/contracts.py` | action schema、结果状态、观察摘要与 guide 版本 |
| `companion_v01/computer_use/session.py` | 宿主会话、范围绑定、操作状态与租约请求 |
| `companion_v01/computer_use/media.py` | 临时截图校验、图片输入与投影预算；不登记自动 gen_* |
| `companion_v01/tool_handlers/computer_use.py` | 参数归一化、审批摘要、工具反馈，经现有 broker 分发 |
| `desktop_pet_next/src-tauri/src/computer_use/` | 分拆 windows、capture、accessibility、input、session、protocol 模块 |
| 控制中心对应独立组件 | 选择设备/目标、授权范围、运行/暂停/停止状态，不在聊天页堆调试字段 |

接入点须核对：`desktop_satellite_specs.py`、`tool_handlers/catalog.py`、`desktop_satellite.py`、`capability_discovery.py`、`capability_approval.py`，以及 `tool_orchestration_engine.py` 的设备结果转工具结果/媒体通道。当前 DesktopSatelliteToolHandler 有显式工具分支，不能只注册一个名称就假定 normalize_call 已支持新工具。

`main.rs` 只增加模块声明和路由委托；engine.py 只复用必要装配，不新增桌面操作分支。保持既有 desktop_screenshot 的显式文件输出语义，自动观察走新的临时媒体路径。

## 8. 实施顺序与验收

### P0：契约和可调用链

交付 computer_use ToolSpec、动作状态、按需说明、设备在线/版本协商、宿主到 Satellite 的真实调用。未实现动作明确 unavailable。对能力加载后立即调用做一次检查，保证不依赖上下文压缩；检验说明去重和版本不匹配反馈。

### P1：只读窗口观察

交付窗口枚举/选择、WGC 截图、按需 UIA、坐标映射、临时媒体通道。选择普通测试窗口，核验截图/控件属于同一窗口，移动/缩放与遮挡有明确结果；不注入鼠标键盘。到此可验收“看得到”，不能宣称“能控制”。

### P2：单窗口操作闭环

交付控制租约、聚焦、点击、输入、按键、滚动、自动观察和本地停止。记事本输入指定文字并通过所属保存对话框保存到测试工作区；确认文件内容。增加切窗/用户接管、重复 invocation、失效坐标及动作后观察失败的少量行为检查。此阶段先达到可用成品。

### P3：QQ 与应用扩展

仅选用户指定的测试会话验证 RootWebArea 降级、编辑区聚焦、草稿核对、发送审批和消息状态。没有这次具体外发授权时停在草稿，不为了验收给群聊发消息。获准发送后能证明什么就报告什么；无送达回执不算实现失败，也不写成 delivery_confirmed。随后按需要扩展 owned popup、多窗口和拖拽。

### P4：工具交接与个人 Chrome（可独立做连接验证）

按第 10 节完成 shell/桌面/网页三类执行器交接，以及用户指定 Chrome 会话接入。先验证连接与只读状态，再接入输入；不以复制 Cookie 或修改默认 profile 启动参数模拟已完成集成。若已有 MCP 接入路径可复用，连接验证可与 P1 并行安排，不要求先完成所有桌面动作。

### 最小验证集合

- 小型可控窗口覆盖窗口身份、坐标映射、未知执行状态、焦点改变与停止，不做大量陌生软件巡检。
- 至少一次真实 Windows 窗口验收；模拟 UIA 返回值不能替代真实 QQ/记事本观察。
- 真实工具续轮拦截：图片正确送达、旧像素按策略退出、说明不重复；不新增付费模型压力测试。
- `git diff --check`；按改动跑 satellite/approval/native schema 相关测试，以及 Tauri Rust 检查。只在改到前端组件时追加对应 build 和交互检查。
- 每阶段报告：实现了什么、实测哪些应用/窗口、未验证什么。当前结果见 [实现与验收记录](computer_use_acceptance_v1.md)，包含真实记事本与 QQ 草稿验收及个人 Chrome 的未完成验证项。

## 9. 来源与适用范围

- **L1 本机公开 skill**：`~/.codex/plugins/cache/openai-bundled/computer-use/26.915.31945/skills/computer-use/SKILL.md`，及同包 `docs/guidance.md`、`api.md`、`confirmations.md`。用于确认所提供 API 与操作规范，不作为 Akane 的代码依赖。
- **L2 当前仓库**：上述 Satellite/能力发现/审批/媒体文件；`desktop_capture.rs` 当前为主屏 GDI 截图，`desktop_screenshot.py` 会登记生成文件，二者不能直接当作窗口级临时观察已实现。
- **S1** [Build skills](https://learn.chatgpt.com/docs/build-skills)：按需展开技能说明；原 developers.openai.com/codex/skills 已重定向到此页。
- **S2** [OpenAI Computer use](https://developers.openai.com/api/docs/guides/tools-computer-use)：环境由应用提供、执行和返回观察；支持不同工具接入方式。这里采纳的是闭环与接口分层，不承诺所有模型具有同等桌面能力。
- **S3** [Windows UI Automation threading](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)：UIA 工作线程及 COM 生命周期。
- **S4** [Windows screen capture](https://learn.microsoft.com/en-us/windows/uwp/audio-video-camera/screen-capture)：WGC 捕获与帧尺寸/设备变化处理。
- **S5** [CreateForWindow](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow)：桌面窗口 HWND 与捕获对象的衔接。
- **S6** [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)：输入注入及 UIPI 限制。

检索工具本轮不可用，以上官方页面通过 HTTPS 直接获取并核对；不把搜索摘要当作接口依据。文档与库版本变更时，在对应切片开始前复核受影响契约即可，不做无关平台迁移。

## 10. 能力目标、工具联动与个人 Chrome

### 能力目标与可见限制

目标是具备同类桌面 agent 的功能覆盖：观察、定位、输入、应用启动、窗口/标签页切换和跨工具完成任务。agent 循环与工具调用已具备只是基础；视觉定位、规划纠错、上下文长度和工具使用能力仍受所选模型影响。不能承诺接上相同工具后，每个模型都达到 Codex 的成功率，也不能仅凭“未设置限制”承诺任何软件都可控制。

未实现能力、OS 拒绝、设备离线、模型无视觉能力、授权不足、次数/时间/媒体预算分别返回明确原因和恢复办法。配置与控制中心展示有效能力和限制；阶段性不支持不是永久写死的软件黑名单，也不允许静默停止或伪装任务完成。用户能看到待处理步骤，预算耗尽时保留可续接状态。

本机 Codex Windows skill 明确不允许通过 UI 操作终端，这属于当前工具运行策略，不是 Windows 输入技术做不到。Akane 不复制该工具的全部限制；需要命令时优先调用已有 shell 执行器，GUI 操作则进入 computer_use。工具切换不能用来绕过同一任务的授权边界。

### 工具可以接力，交接对象必须真实

典型路径：

```text
shell / launch_app 启动目标应用
→ 返回执行设备与启动结果
→ computer_use 枚举并选中真实窗口
→ 观察、点击、输入

browser_page 操作已连接的标签页
→ 出现原生文件对话框
→ computer_use 选中属于该浏览器的对话框并完成操作
→ browser_page 在原标签页重新观察结果

browser_page 下载材料
→ 受管文件句柄
→ shell/文档工具处理
→ computer_use 在对应软件查看或编辑
```

- 三类工具共用目标设备和任务授权；不能把云端 shell 启动的进程当作用户电脑窗口。跨设备文件使用现有传输/句柄机制，不直接传一个另一台设备无法访问的路径。
- 交接记录 `task_id / device_id / device_epoch / control_session_id / backend_id / window_id / tab_id / file_handle` 中实际需要的字段；由宿主产生映射，不能让模型凭相同标题拼接窗口和标签页。
- 浏览器会话与原生窗口无法可靠关联时明确要求重新选择，不能从“默认 Chrome”猜标签页、账号或 profile。
- 桌面键鼠操作与浏览器中会改变同一用户交互状态的动作共享仲裁；交接时暂停原控制者、使旧操作证据失效，接收方重新观察。状态可以继承，旧坐标/索引不能继承。
- 原生对话框已有明确网页 API（如设置文件输入）时优先该 API；确实进入 Windows 对话框再切换执行器，减少脆弱的点击链。
- shell 有退出码、stdout/stderr，GUI 有观察和动作状态，两者分别验证；进程已启动不代表软件已经打开目标文件，更不代表业务操作完成。

### “使用 Google Chrome”与“复用我的登录会话”是两个选择

当前 runner 的 `browser_channel` 只决定启动哪一种浏览器程序；现有 launch/new_context 不代表已经接管用户正在使用的 profile。产品中应分别选择浏览器种类和会话来源，不能只提供一个“Chrome”按钮就承诺账号复用。

| 会话方式 | 登录状态与能力 | 设计定位 |
| --- | --- | --- |
| Akane 独立临时浏览器 | 不继承个人登录；已有 Playwright 托管能力 | 保留当前默认路径 |
| Akane 专用持久 profile | 用户在该 profile 中登录后可持续复用，网站仍可能要求重新验证 | 需要稳定长期会话时可选，不复用默认用户目录 |
| 用户当前 Chrome + 官方授权连接 | 可访问所连接会话已有登录态，并通过协议读取/操作页面 | 优先验证的个人 Chrome 方案 |
| computer_use 操作已打开的 Chrome 窗口 | 使用该窗口现有登录态，按桌面截图/UIA 操作；不因此获得 Playwright DOM 接口 | 无浏览器连接时的显式 GUI 路径 |

Chrome 官方已发布当前会话连接流程：Chrome 144 及以上版本，可由用户在 `chrome://inspect/#remote-debugging` 开启对应能力，Chrome DevTools MCP 使用 `--autoConnect` 请求连接；Chrome 展示连接确认与自动化提示。该方式面向正在使用的浏览器会话，包括已有登录态。[S7]

本轮只读查询本机 Chrome 注册表版本为 `153.0.8010.50`，满足上述文档的版本门槛；未检查或修改调试开关，未连接浏览器，不能据此声称具体 profile/企业策略已经允许连接。

这与旧式命令行调试端口不同：Chrome 136 起，`--remote-debugging-port` / `--remote-debugging-pipe` 不再对默认用户数据目录按旧方式生效，调试需要非默认目录。[S8] 不应因此笼统认定“个人 Chrome 不能接入”，也不能让用户复制默认 profile 或导出 Cookie 来假装自动复用了账号。

优先通过现有 MCP 适配能力验证官方 Chrome DevTools MCP 的会话连接，再评估统一进 browser_page 的 backend adapter。不能假定 MCP 连接、扩展 debugger 与 Playwright `connect_over_cdp` 能直接互换；Playwright 官方也说明 CDP 连接与完整 Playwright 协议存在能力差异，截图、弹窗、下载和定位应分别探测。[S9]

Chrome 扩展的 `chrome.debugger` 是另一种需明确安装和授权的接入方式，可对标签页使用允许的 CDP 域；它不开放所有协议域，因此仅作为候选后端，不在已有官方会话连接可用前同时自建第二套桥接。[S10]

连接契约必须包括：用户选择设备和 Chrome 会话/窗口/标签页；显示 connected/disconnected/awaiting_user；由用户操作浏览器的连接许可界面；明确结束时仅解除连接，不关闭个人浏览器或清空登录态。不要把 browser_page 当前托管窗口的 shutdown/close 逻辑原样用于个人浏览器。截图/工具结果不包含 Cookie 或认证材料；网页登录态留在浏览器中使用。

普通群成员不能因为角色有 computer_use 工具就控制主人 Chrome。调用者身份、绑定设备与个人会话授权都由宿主校验；页面文字、群聊请求和模型判断不能自行扩大范围。用户正在使用 Chrome 时接管输入的暂停规则仍有效。

### 坐标准确性与辅助定位

不设置未经实测的统一准确率。模型、图像清晰度、小目标、相似图标、遮挡、滚动、动画以及 DPI 映射都会影响结果。按以下顺序提供辅助，模型选择目标，宿主完成确定性的坐标换算和执行核验：

1. **DOM / UIA 语义定位**：有稳定角色、名称、控件区域时优先用 element_id 与控件操作，不让模型重新估算像素。
2. **截图 + 可选目标编号**：把观察到的可交互区域标号，模型返回编号，宿主从当前观察表取真实边界；编号仅对本次观察有效。SoM 研究支持这类标注作为视觉定位辅助手段，但不保证对所有模型/应用都有相同收益。[S11]
3. **文字/OCR 与局部放大**：小字或密集工具栏先取局部清晰图；OCR 只给候选文字/位置，不能把识别结果当作可点击控件证明。保存裁剪、缩放和 DPI 变换，避免用放大图坐标直接点击原窗口。
4. **执行前核验与执行后观察**：检查窗口、焦点、命中目标与局部画面；操作后核实目标效果，低置信或状态变化就重观察，不盲点多个近似位置。

首版落实第 1、4 项和局部放大接口；目标编号/OCR 仅在实测发现定位瓶颈时启用，不新增一个必须运行的重型识别服务。标注图与原图不会默认同时占两个模型图片名额。

## 11. 连续流程与交互增强（2026-09-20 开始，2026-09-21 完成本轮实现与验收）

当前已实现 E1 和 E2 的有界接口；E3 保留真实交接与附件核验边界，未实现的跨应用文件投放明确返回不可用。完整实测结果与限制见 [增强验收记录](computer_use_acceptance_v1.md#2026-09-21-连续流程与交互增强)。

边界按“下一步可预测且前置/后置条件能验证”划分，不按同一页面划分。一次模型调用可提交有界步骤，设备按序执行、等待并验证，覆盖菜单、页面变化、所属对话框和明确选择的窗口。新目标使用当前 UIA 语义匹配，不能预填未来 observation_id 或沿用旧坐标。无法唯一定位时暂停。

### E1：有状态的连续流程

- 新增 run_steps / resume_steps，最多 12 步，有总耗时和单步等待预算；不接受任意代码、无限循环或嵌套流程。
- 每步声明动作、目标及可机读的预期条件；提供 wait_for 和明确的窗口切换。已经满足目标的设置操作可跳过。
- 每步复用现有目标范围、控制租约、用户接管、具体操作审批和输入前核验。未来步骤的发送/提交不能借批量许可跳过实际内容核对。
- 返回 workflow_id、逐步状态、已完成数量、中断原因、下一步和最后现场。中间截图留在设备内，仅最终或中断现场进入模型。
- 重复 invocation 返回同一结果；恢复先检查现场。已执行但尚未核实的步骤只复核后置条件，不重新输入；未知执行结果禁止自动重放。停止与用户接管仍须本地恢复。

### E2：动作及纠错

- 扩展左右/中键、双击、受控拖拽；拖拽中断释放本动作按下的键鼠，验证起终点、窗口身份、遮挡和用户接管。
- 增加明确的 set_value / set_checked，核验原值与控件能力，目标已满足则不重复修改。不把任意 UIA Edit 或 Toggle 的存在当作提交无风险证明。
- 补充 QQ 编辑区有证据时的撤销、重做、删除与选择操作；取消/返回通过明确按键及当前应用策略执行。不存在统一业务回滚；发送、文件覆盖等效果分别核验。

### E3：附件与跨工具边界

文件投放必须区分鼠标拖动、附件准备、提交、送达。QQ 富文本可输入不证明可安全投放附件；未核实来源文件、目标会话及投放语义时明确停止。跨工具步骤仍走真实 handoff，新工具重新观察；本阶段不把浏览器标签与 HWND 按标题自动拼接。真实 QQ 文件外发不属于验收授权。

### 验证与交付

覆盖点击失败阻止后续输入、可预测窗口变化、等待超时、歧义目标、失败后只续接未完成步骤、未知结果禁止重放、审批内容改变失效、停止与用户接管、勾选幂等、鼠标释放及最终媒体数量。更新宿主/设备契约哈希和增量说明版本，跑相关 Python/Rust 测试并构建；真实键鼠验收只在明确的专用测试窗口或用户让出键鼠后执行。实际完成范围在验收记录单列，不以本节计划代替验收。

新增来源（2026-09-20 直接获取官方/论文页面核对）：

- **S7** [Chrome DevTools MCP 连接当前浏览器会话](https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session)。
- **S8** [Chrome 136 远程调试参数变化](https://developer.chrome.com/blog/remote-debugging-port)。
- **S9** [Playwright connect_over_cdp](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)。
- **S10** [Chrome debugger 扩展 API](https://developer.chrome.com/docs/extensions/reference/api/debugger)。
- **S11** [Set-of-Mark Prompting](https://arxiv.org/abs/2310.11441)。
