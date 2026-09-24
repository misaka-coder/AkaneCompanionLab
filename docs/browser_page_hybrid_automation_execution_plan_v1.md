# Akane `browser_page` 混合浏览器自动化升级执行单 v1

原生桌面能力现单独维护于 [Computer Use 设计与执行单 v1](computer_use_execution_plan_v1.md)。本单保留浏览器范围及 QQ 实践来源记录；新的桌面契约、增量说明、设备执行与验收以独立执行单为准。

> 2026-09-20 审计修订：方案可行，先落实本单中的观察绑定、媒体传输和动作结果契约，再进入实现。本次仅修订执行单，尚未修改浏览器代码。公开搜索页实测属于原执行单的记录；本次审计核对了本地实现和 Playwright 文档，未重跑该实测。

## 目标

把 Akane 的浏览器自动化从“DOM/ARIA 读取 + 显式控制”升级为可观察的混合自动化：

```text
可访问性树 / 页面文字优先
        ↓
交互动作后自动重新观察
        ↓
需要视觉判断时附带截图
        ↓
DOM 无法定位时再使用视觉坐标兜底
```

主要体验目标：

- Akane 能看到交互前后的真实页面状态，而不是只根据点击返回的文本猜测结果。
- `click / fill / press / scroll` 后自动返回最新的可访问性树、页面文字和截图。
- 读取长文时仍以文本/ARIA 为主，不为每个续读片段重复生成截图。
- 浏览器触发下载时能捕获、保存、登记并返回文件句柄，而不是只执行一次点击。
- 保留现有 Akane 托管浏览器、会话隔离、审批和安全路径边界，默认不连接用户手动打开的标签页；用户指定的个人 Chrome 会话接入另按 Computer Use 执行单第 10 节实施。

## 已确认的现状和实测结论

### Akane 当前实现

- `browser_page` 已支持 `navigate`、`read_text`、`current`、`snapshot`、`screenshot`、`scroll`、`elements`、`click`、`fill`、`press`。
- 页面读取使用 Playwright 的 `aria_snapshot()`，失败时回退到正文文本，因此已经具备网页级可访问性树能力。
- 元素读取可生成 `ref` 和 `candidate_index`，控制动作可以按 selector、ref 或候选索引执行。
- `click / fill / press` 执行后会重新捕获页面文本快照，但当前不会自动捕获并返回截图。
- 当前 `screenshot` 是单独动作，并通过生成文件服务登记为 `gen_*` 文件；不适合直接作为每次交互后的临时观察结果。
- 现有显式截图已通过 `ToolExecutionResult.model_image_inputs` 向模型传图；本轮复用这条通道，新增临时观察的生命周期，不另建一套视觉模型接口。
- 当前浏览器是 Akane 自己管理的 Playwright 窗口，不接管用户手动打开的 Edge/Chrome 标签页。
- 当前已有 `page_revision` 和不可变文本快照缓存，但控制参数没有观察版本绑定；候选索引点击时会重新枚举当前页面。仅把版本号打印进结果，还不能防止点到变化后的另一项。
- 本次审计环境为 Playwright Python 1.60.0，`aria_snapshot` 支持 `mode` 和 `boxes`。部署时需探测实际能力；退化为普通 ARIA/正文时，不能声称仍有可操作 ref 或坐标框。

### 对照实践结论

本轮使用电脑浏览器自动化在公开 Google 搜索页完成了：

```text
打开搜索页
→ 读取可访问性树
→ 点击 Playwright 文档结果
→ 点击后再次读取可访问性树
→ 需要视觉确认时主动请求截图
→ 使用 AX + screenshot 组合观察
```

确认的真实行为是：

- 浏览器标签页自动化的 `getAXState()` 默认只返回可访问性树/界面文字。
- `getScreenshot()` 需要主动调用。
- `getAXStateAndScreenshot()` 才会同时返回可访问性树和截图。
- 上述实测证明 AX 与截图可组合使用，并不能单独证明每次交互截图都是最优策略。本轮将“可看图模型下，交互后默认 AX + screenshot”作为可调整的产品默认值；保留 text 模式，并在验收中记录单次观察耗时与实际送入模型的图片数。

## 必须先统一的契约

### 文本快照与操作证据分别绑定

- `snapshot_id + cursor` 指向不可变的历史文本快照。当前页面变化后，旧 cursor 在 TTL 内仍可续读原文；结果注明这是旧快照，不允许用其中旧 ref 操作新页面。会话不匹配、缓存过期或被清理时明确失败。
- `observation_id` 指向一次观察，绑定会话、浏览器实例代号、page_id、文档代号、交互版本与观察时间。浏览器重建后旧证据必须失效，不能只靠会从 r0 重启的计数器区分。
- `ref / candidate_index / screenshot_id` 都绑定 observation_id。候选表在观察时冻结；执行时核验目标身份、可见性和可操作性，不重新枚举后按同一个序号猜测。
- 页面导航、标签页切换、滚动和尺寸变化使相应操作证据失效。动态 DOM 可在没有工具调用时改变；普通 locator 也要重新核验目标，视觉操作还需有界有效期和布局变化检测。无法确定时先重新观察。
- 不承诺对不断变化的网页提供绝对原子快照。AX 与截图采集前后校验文档/视口标识；发现变化时做一次有界重采集，仍不一致则返回 `unstable`，不发布可点击坐标。

### 动作执行与后续观察分别报结果

结果至少区分 `action_state=not_started/executed/unknown`、`observation_state=complete/partial/failed/unstable`、`page_changed=yes/no/unknown`。正文是否还有下一页继续由 `complete / next_cursor` 表示，不能与观察成功混用。

点击已执行，但后续截图、读文本或下载等待失败时，返回已执行事实和观察失败原因；下一步是 current/snapshot 或 download_status，不重放 click/fill/press。浏览器在提交瞬间断开、无法确认是否执行时标记 unknown，也不能自动重试有副作用的动作。页面没变化不等于动作没执行，页面变化也不等于业务目标完成。

审批等待期间页面可能变化。批准后仍校验证据与目标，失效则重新观察；不把对旧目标的批准当作对新目标的批准。默认一会话串行执行动作与观察，沿用现有 runner 专用线程，不在其它线程直接调用 Playwright 对象。

## 执行顺序

### 第 0 步：锁定现有行为契约

涉及文件：

- `companion_v01/browser_page_runtime.py`
- `companion_v01/tool_handlers/web_browser.py`
- `companion_v01/capability_registry.py`（工具输入 schema、描述及能力暴露）
- `companion_v01/tool_handlers/core.py` 与既有工具图片续轮链路（复用，不增加业务分支到大入口）
- `tests/test_tool_runtime.py`
- `tests/test_browser_page_paged_snapshots.py`
- `docs/tool_interface.md`

执行内容：

- 记录当前 `browser_page` 的动作集合、结果字段、快照 cursor、会话绑定和审批行为。
- 保留已有动作名称和参数含义；新增 `observation_mode`、`observation_id` 等明确字段，并同步 normalize_call、schema、审批目标摘要和工具文档。旧文本读取和 cursor 语义不变；缺少有效绑定的候选/坐标操作明确要求先观察，不用兼容分支猜目标。
- 明确自动观察结果属于本次工具结果，不自动登记成用户可发送的 `gen_*` 文件。

完成标准：

- 现有文本读取与分页续读测试继续通过；ref 操作测试补上观察绑定，验证正常操作仍可用、过期证据被拒绝，不保留无绑定操作的兼容绕过。
- 当前工作区已有改动不被覆盖或混入本任务。

### 第 1 步：增加统一的混合观察模型

建议将观察结构与缓存放入独立模块，由 `browser_page_runtime.py` 装配调用，至少包含：

- `page_revision`
- `observation_id / page_id / browser_generation / document_generation`
- `url`
- `title`
- `aria_snapshot`
- `visible_text`
- `element_candidates`
- `screenshot` 或临时截图引用
- `screenshot_id`
- `captured_at`
- `complete / next_cursor`
- `action_state / observation_state / page_changed`
- `viewport / scroll_position / screenshot_dimensions / coordinate_transform`
- `visual_status`（图片已随模型请求发送、仅已捕获、不可用或未请求）

观察模式建议：

- `text`: 页面正文/ARIA，不截图。
- `hybrid`: 页面正文/ARIA + 当前视口截图。
- `visual`: 截图优先，可附带简短 ARIA 摘要。

默认策略：

- `navigate`：默认 `hybrid`，用于确认页面确实打开并让模型看到初始界面。
- `click / fill / press / scroll`：默认 `hybrid`，动作完成后自动重新观察。
- `read_text / cursor`：默认 `text`，避免长文续读重复生成截图。
- `snapshot / elements`：默认 `text`；模型明确需要视觉判断时切换 `hybrid`。
- `current`：默认 `text`，用于状态确认和失败后的安全恢复，可显式请求 hybrid。
- 显式 `screenshot`：继续支持，并保留生成文件句柄能力。

临时截图必须接通实际模型输入：runner 返回受限图片载荷或会话内短期引用 → handler 构造 `model_image_inputs` → 既有多模态工具续轮。只返回截图 ID 或本地路径，不算模型已经看见图片。没有可用视觉模型时保留文本观察，报告 visual_unavailable，禁止继续坐标猜测。

自动截图不进入 GeneratedFileStore、附件工作台或用户文件交付队列。使用独立的有界临时缓存，明确 TTL、总字节数和清理时机；关闭会话/重建浏览器时清理。显式导出截图才走已有文件登记路径。

每次自动观察至多附一张视口图。连续操作时优先当前观察，只有明确比较需求才保留前后两张；需要在现有临时媒体组装边界落实这一策略，不能仅限制本次工具输出。MemCore 省略持久历史图片，不等于同一开放回合的图片不会累积。保留文本事实及必要引用，不破坏原始调用/结果配对、冻结投影和 provider continuation；不能安全缩减时明确返回预算限制，不静默丢掉最新图或伪造视觉可用。

采集像素上限、编码字节上限和超时集中配置。缩放或裁剪必须携带坐标映射，不用截图展示尺寸直接推断点击坐标。文本已经去敏不能代表截图也已去敏；自动观察至少遮罩可识别的密码输入框，并如实标记无法可靠处理的敏感区域，不宣称所有敏感内容都能自动识别。

完成标准：

- 观察结果能同时承载文本证据和临时视觉证据。
- 经过一次真实工具续轮，模型请求内确实含当前截图；持久投影只留安全标记/引用，不含像素、base64 或内部路径。这里可用请求拦截验证，不要求付费模型反复测试。
- 自动观察截图不污染 `GeneratedFileStore`，也不会凭空出现可发送文件。
- 仍然能通过 cursor 续读同一份文本快照，不会因截图重新操作页面。

### 第 2 步：接入“观察 → 单步动作 → 重新观察”闭环

涉及文件：

- `companion_v01/browser_page_runtime.py`
- `companion_v01/tool_handlers/web_browser.py`

执行内容：

- 控制动作完成后有界等待页面状态：结合 actionability、导航/弹窗事件和适当的渲染等待；不把固定 sleep 或全站 networkidle 当作“页面已稳定”的证明。超时可返回 partial/unstable，不因此重放动作。
- 每次动作只使用最近一次观察产生的 `ref`、候选索引或截图坐标。
- 动作结果中明确区分：
  - 动作是否执行成功；
  - 页面是否发生变化；
  - 新观察是否完整；
  - 是否还有文本 cursor；
  - 是否包含视觉观察。
- 导航和 popup 监听在动作前安装，捕获到的新页面分配 page_id；不能仅等待 200ms 后把 context.pages 最后一项当目标。只跟随与本次动作关联且通过 URL 策略检查的页面；来源不明的新增标签列出而不自动切换。
- 区分 DOM 弹层、JS dialog、网页权限与系统窗口。JS dialog 使用 Playwright 的独立处理策略：监听后必须 accept/dismiss，否则可能阻塞动作；不默认接受 confirm/prompt。首版没有预先确定的处理策略时 dismiss 并报告类型和必要说明，不把它写成业务成功。
- URL 边界检查覆盖显式导航、页面点击跳转、重定向和 popup；允许的资源请求范围按既有部署策略核对。本地实现当前主要检查入口 URL 和候选 URL，不能把“已有公共 URL 校验”直接写成已覆盖全部导航边界。
- 如果动作后的页面没有可用内容，返回真实状态和建议下一步，不生成“已完成”的假成功。

完成标准：

- 点击搜索结果后，工具结果同时能看到新页面正文/ARIA 和当前页面截图。
- 页面没有变化时也明确返回当前状态，而不是假设点击成功产生了结果。
- 旧 `ref`、旧候选索引和旧截图标识不会跨页面版本继续使用。
- MVP 就进行一次真实 Playwright 浏览器闭环 smoke，不等到 Windows 原生 UI 阶段才确认 ARIA、截图和新页面是否能同时工作。

### 第 3 步：增加视觉兜底操作

这一步在混合观察闭环稳定后执行。

建议增加受控的内部能力：

- 使用最近一次 `screenshot_id` 绑定坐标操作。
- 输入坐标统一为当前视口 CSS 像素，原点在视口左上角。截图尺寸、DPR、滚动、裁剪偏移与缩放映射由宿主保存；不混用桌面物理像素和图片显示像素。点落在视口外或遮罩区时拒绝执行。
- 支持视口坐标点击，必要时支持拖拽。
- 坐标操作后强制重新获取 `hybrid` 观察。
- 截图过期、页面版本变化、窗口尺寸变化时拒绝复用旧坐标，并要求重新观察。
- 本阶段同步更新工具输入 schema、参数归一化与审批摘要，明确坐标动作名称、坐标字段及 screenshot_id/observation_id；不能只新增 runner 内部方法而让模型无法调用。

使用顺序：

```text
可见语义目标 / ref
→ 已观察到的 selector
→ candidate_index
→ 最新截图坐标
→ 返回真实失败
```

不要在这一阶段引入无证据的“猜测点击”或自动遍历页面；视觉坐标必须绑定到最近一次截图。

完成标准：

- Canvas、非标准按钮或 ARIA 树缺失时，可以基于最新截图完成一次受控点击。
- 页面变化后旧坐标不能继续执行。
- 坐标操作和 DOM 操作使用相同的审批、结果和失败反馈契约。
- 坐标点击优先落地；拖拽单独作为扩展，不为了拖拽延后主流程验收。

### 第 4 步：补齐浏览器下载接管

涉及文件：

- `companion_v01/browser_page_runtime.py`
- `companion_v01/tool_handlers/web_browser.py`
- 现有 Attachment Inbox / Generated File Store 相关服务
- `docs/tool_interface.md`

执行内容：

- 在托管页面建立持久 download 监听，并在可能触发下载的动作前记录 action_id；覆盖 click、press、导航和 popup，不要求模型先准确猜出哪一步会下载。监听回调必须立即关联记录，不能只保留到工具返回前。
- 捕获下载事件、建议文件名、下载完成状态和失败原因。
- 下载记录拥有独立 download_id，状态为 pending/completed/failed/cancelled，支持 `download_status` 查询；一次动作可关联多个下载。短等待超时仅表示仍在下载或结果未确认，不能自动再次点击。
- 将 `download_status` 和 download_id 接入公开工具 schema、参数归一化和处理器分发，查询仍校验所属会话；工具返回后的下载进展由同一 Playwright 线程继续处理，不能只注册监听却停止事件处理。
- 保存到 Akane 受管工作区，使用现有安全路径校验和临时文件替换流程。
- 登记路径必须明确，不能在实现时随意“附件或生成文件”：以用户下载材料进入 Attachment Inbox 为首选，复用来源元数据、解析和安全路径能力；若现有 send_file 需要另一种句柄，通过统一解析入口衔接。同一下载只登记一次。下载文件记录真实来源，不假装由模型创作。
- 建议文件名作为不可信输入处理，净化路径成分、避免覆盖同名文件、使用受管唯一文件名和原子发布；先完成 save_as/错误检查，再注册句柄。登记失败时保留可恢复状态或清理临时文件，不声称已完成交付。
- 自动下载观察结果不直接发送给用户；只有用户明确要求交付时，才继续走 `send_file`。
- 下载失败、取消、文件过大、保存失败必须返回下载层结构化状态，不能只返回点击成功。合法零字节文件与下载失败分开判断；业务预期非空时标记异常。文件体积限制注明实施位置：下载完成后的校验不等于传输中硬限额；首版至少限制并发、受管发布大小和临时目录占用，超过预算终止或清理。
- 保存完成前不能关闭所属 context；待处理下载在关闭时取消并更新记录。永久保存并登记后才承诺关闭浏览器不会丢失。回调、等待与取消遵守 Playwright 所属线程。

完成标准：

- 点击公开下载按钮后，Akane 能确认下载是否完成。
- 下载完成后可以通过句柄继续 `inspect_attachment`、转换或 `send_file`。
- 上述完成标准用同一真实下载句柄走通，不能分别 mock 成三个“成功”。只接受登记记录中的文件类型能力，不把任意 MIME/扩展名当作已支持转换。
- 浏览器上下文关闭后，受管工作区中的已登记文件不会丢失。
- 不把内部绝对路径、Cookie 或下载临时目录写入稳定提示。

### 第 5 步：必要时接 Windows 原生 UI 兜底

仅在 Playwright 无法处理以下界面时执行：

- 浏览器外壳权限提示；
- 系统保存/另存为对话框；
- 非网页原生窗口；
- 浏览器 UI 自身的下载管理界面。

原则：

- 优先使用 Playwright 的下载事件和受管路径，避免依赖系统对话框。
- 只有确实需要时才接 Windows UI 自动化。
- 原生窗口观察同样采用“截图 + UI 可访问性信息”的组合。
- 本轮托管模式不接管用户个人 profile 或手动标签页；后续个人 Chrome 模式通过明确连接授权接入，不复用托管实例的关闭/清理逻辑。

### 第 6 步：测试、文档和验收

新增或扩展测试：

- 混合观察结果包含 ARIA、文字和临时截图，不生成多余用户文件。
- `read_text` 长文不自动截图，cursor 续读行为不变。
- `click / fill / press / scroll` 后会重新观察并返回新页面版本。
- 页面切换、弹窗、新窗口和页面关闭能够恢复或给出真实失败。
- 旧 ref 和 screenshot id 无法操作新页面；旧 cursor 在 TTL 内仍读取原不可变文本，且不改变当前可操作观察。
- 点击成功而截图失败时不重复点击；候选顺序在观察后变化时不点击另一个目标。
- 连续几步工具续轮仍发送最新截图，且真实图片数量受观察预算约束。
- 下载事件成功、超时、保存失败、文件过大和上下文关闭后的文件保留。
- 视觉坐标点击只接受最新截图绑定的坐标。
- 现有审批、敏感字段处理和公开/私网 URL 边界不被削弱。

建议验证命令：

```powershell
git diff --check
python -m unittest tests.test_tool_runtime
python -m unittest tests.test_browser_page_paged_snapshots
```

优先新增范围清晰的 observation/download 行为测试，不把所有场景继续塞入大型 test_tool_runtime。只有改到后端路由才追加 test_backend_route_modules。无需为了形式启动后端、TTS 或桌宠进程。

真实浏览器 smoke 首选可控的小测试页覆盖长文、异步按钮、popup 和下载，再做一次公开网站验证。测试页仅在隔离测试配置中放行回环地址，不修改生产公开/私网策略。少量真实闭环用于识别模拟测试看不到的浏览器行为，不进行大规模站点巡检。

代码组织：现有 runner 已超过千行，web_browser handler 也较大。新增观察缓存/绑定、下载记录等各归独立模块，原文件保留装配和委托；不为本轮顺手拆全仓，也不继续将所有新职责堆进同一个文件。

## 交付分层

### MVP

- 统一观察模型；
- `navigate / click / fill / press / scroll` 后自动返回 AX + 截图；
- 长文读取保持 text-only；
- 页面版本、过期 ref 和失败状态清楚可见；
- 图片实际进入模型请求、临时媒体预算与降级可见；
- 一次真实托管浏览器 smoke；
- 相关单元测试和工具文档完成。

### 第二阶段

- 视觉坐标点击兜底，拖拽按实际需求后加；
- 浏览器下载捕获、工作区登记和文件句柄交付；
- 下载与现有附件工作台打通；上传作为后续独立能力，不隐含在下载任务中。

### 第三阶段

- Windows 原生窗口/浏览器外壳兜底；
- 更细的视觉观察策略、局部截图和增强敏感区域保护；
- 真实 Windows 原生窗口 smoke，补充前两阶段已有的网页浏览器验收。

## 可选扩展及优先级

1. **托管标签页列表与显式切换（第二阶段优先）**：为 popup、返回原页和多页面任务提供 page_id、来源及关闭状态。只操作 Akane 托管 context，不读取用户个人浏览器标签。
2. **iframe 定位（第二阶段按需要）**：观察标注 frame_id，定位和坐标绑定相应文档；先覆盖实际目标网站，不声称 body 的 ARIA 文本已经覆盖全部嵌套页面。
3. **局部截图与前后对比（第二阶段之后）**：提高小字/Canvas 细节的可见性，只在明确目标区域采集，携带裁剪映射；比较是显式需求，日常操作不用重复附旧图。
4. **按范围等待（第二阶段之后）**：支持等待指定元素出现、下载完成或 URL 改变，复用已观察目标并设置短超时；不要让模型靠反复截图实现轮询。
5. **上传（独立切片）**：只能选择现有受管文件句柄，明确目标站点、用户授权和真实选中文件，不能接受任意本地路径。不要因下载成功就宣称上传已打通。

这些扩展不阻塞 MVP。Windows 原生 UI 不是网页弹窗的默认解法；先证明目标场景无法通过网页 API 完成，再启用原生切片。

## 明确不做

- 不把 Akane 改成持续录屏或每次调用都强制截图。
- 不用截图替代已有的 DOM/ARIA 定位能力。
- 不自动接管未经用户选择和授权的 Edge/Chrome 标签页；用户主动选择的 Chrome 会话按独立执行单实施。
- 不通过验证码、付费墙、DRM 或浏览器安全拦截。
- 不把下载成功、点击成功或页面变化写成未经验证的假成功。
- 不在本切片顺手重构无关的浏览器、附件或桌宠模块。

## 最终验收场景

1. 打开一个公开搜索页：结果中同时有页面文字、可访问性树和截图。
2. 点击一个搜索结果：点击后自动返回新 URL、新标题、新 ARIA/文字和新截图。
3. 阅读长文：首次和 cursor 续读只返回文本/ARIA，不重复生成截图。
4. 操作一个 ARIA 不完整的可视按钮：先返回截图，再基于最新截图完成一次坐标点击并重新观察。
5. 点击公开下载按钮：Akane 返回下载完成状态和受管文件句柄，可继续检查或发送文件。
6. 关闭浏览器或让页面失效：工具返回结构化失败和可恢复动作，不声称任务已经完成。
7. 点击后截图失败：仍明确标记动作已执行，只重试观察；旧文本 cursor 继续读原快照，但旧 ref 不得操作新页面。

## 审计依据

- 当前仓库：`browser_page_runtime.py` 的快照缓存、候选解析、popup 与控制动作；`tool_handlers/web_browser.py` 的审批、截图登记和 model_image_inputs；`capability_registry.py` 的实际输入 schema；`tests/test_browser_page_paged_snapshots.py` 的不可变分页契约。
- MemCore/模型接入：持久历史中的媒体省略不替代开放回合媒体预算；图片可触发既有视觉执行目标选择，因此需要如实记录视觉可用性和实际媒体送达。
- [Playwright 下载文档](https://playwright.dev/python/docs/downloads)：download 监听、save_as，以及 context 关闭后临时下载文件的生命周期。
- [Playwright 对话框文档](https://playwright.dev/python/docs/dialogs)：默认自动 dismiss；注册监听后必须处理 dialog，避免阻塞页面动作。
- [Playwright 截图接口](https://playwright.dev/python/docs/api/class-page#page-screenshot)：视口/裁剪/比例等采集参数。在线文档可能比本机版本新，具体参数以部署版本探测和真实 smoke 为准，不依赖未经核验的新版本接口。

## 2026-09-20 实现复核与修复

原有浏览器相关 103 项检查通过后，定向复现了以下缺口，并修复实际调用链：

- 页面自行替换按钮后，旧截图仍能点到替换后的目标：现在绑定文档身份、DOM/滚动/视口变化；所有普通控件操作要求 observation_id。坐标操作增加 60 秒有效期及目标附近像素的本地复核，覆盖 Canvas 重绘，核验截图不额外传给模型。历史文本 cursor 仍可续读。
- 点击已经产生副作用，但 popup 处理失败时回报 not_started：执行边界现在区分 not_started / unknown / executed，失败反馈要求先观察，不能自动重放；popup 使用来源 page 的事件监听和 Playwright 的 remove_listener，删除仅为测试替身保留的页面列表回退。
- 下载登记失败仍返回 completed：现在返回 failed 和登记失败原因，保留已保存文件供恢复，不发布不可用句柄；临时文件改在目标磁盘暂存，再原子替换。
- 截图编号只通过 MemCore 图片占位间接可见：工具结果现明确提供 screenshot_id、视口 CSS 尺寸、图片尺寸及坐标说明。无法遮罩密码时降级为文字，不回退发送无遮罩图片。

验证范围：浏览器观察、分页和工具运行时测试；真实 Playwright 页面替换、Canvas 重绘、被拒绝的 popup；真实本地 HTTP 下载接入 AttachmentInbox，按同一 file_* 句柄检查材料，关闭浏览器后文件保留。未调用付费模型，也未接管个人浏览器或操作真实桌面应用。此记录不代表第三阶段 Windows 原生控制已经完成。

### 下一步 computer-use 的衔接建议

继续在现有项目内迭代。`desktop_pet_next/src-tauri/src/desktop_capture.rs` 已有设备侧截图，main.rs 的 Satellite 调用分发已有设备执行入口；新增独立桌面自动化模块，经现有能力目录、审批和 Satellite 路由接入，不将 Windows 操作堆入 engine.py，也不把屏幕观察开关等同于操作授权。

首个切片限定一个用户选定窗口：窗口/UI Automation 观察与截图 → 单步点击、输入、按键、滚动 → 自动重观察。证据绑定设备、窗口、坐标原点、DPI 和观察编号；操作前检查前台窗口，用户切窗/接管即暂停，提供可见运行状态和立即停止入口。网页仍优先使用 browser_page；桌面控件优先 UI Automation，缺少控件信息时使用截图坐标。

先以记事本完成“输入一段文字并保存到指定工作区”验收，再扩展多窗口、多屏和拖拽。当前截图实现采集主屏，不能直接当作任意窗口/多屏的坐标依据；原生输入也受 Windows 完整性级别限制，不把管理员窗口或安全桌面列为首版支持项。参考 [Windows UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32) 与 [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)。

#### 2026-09-20 原生 Computer Use 实践补充

> 以下实践由另一会话记录，本轮仅核对公开 API 与设计含义，未重新操作 QQ。它反映一次特定窗口的观察结果，不代表所有 QQ 版本的无障碍能力。发送前再次确认的描述来自该次 Codex Windows skill 策略；Akane 的授权设计参见独立执行单第 6 节。

本机通过公开 `computer-use` skill 的 `@oai/sky` 接口，对 QQ 原生窗口做了一次真实操作，目标窗口为“Akane测试交流群（128）”。本次只完成了输入验证，没有点击发送，因此不能记作“QQ 外发已验收”。

实际闭环如下：

1. 初始化 `sky`，调用 `list_apps()` 获取应用及窗口对象；不猜 PID、句柄或窗口索引。
2. 用返回的窗口对象调用 `get_window()`、`activate_window()`，再获取最新窗口状态。
3. `get_window_state()` 默认提供截图；每次截图都有只对当前观察有效的 screenshot id。
4. QQ 的 WebView 无障碍观察只暴露为 `RootWebArea`，没有提供可直接定位的消息编辑框或焦点控件，说明 AX 树在原生 WebView 场景下可能不足。
5. 在最新截图中确认编辑区位置后，使用窗口相对坐标点击，并立即重新观察。
6. 截图确认编辑区出现光标后，调用 `type_text()` 输入“大家好，我是codex”；再次观察确认草稿可见且发送按钮已启用。
7. 按该次使用的 Codex Windows skill 策略，最终发送前要求动作时确认；本次在这一步停止。证据应记录为“输入成功、未执行发送、未验证送达”。这不是 UI 输入 API 自带的约束。

这次实践对实现契约的补充结论：

- 截图和 AX 是互补证据，不应假设两者内容完全重复或始终一致。截图适合判断光标、视觉焦点和 WebView 内部状态；AX 适合语义定位，但可能退化为单个 `RootWebArea`。
- 截图坐标、screenshot id、AX 索引都只属于最近一次观察。任何动作后都必须重新观察，不能复用旧坐标或旧索引。
- AX 没有暴露焦点控件时，应标记为“观察能力不足”，不能据此猜测控件；只有在截图中目标和坐标都清楚、窗口仍在前台且坐标原点/DPI 映射已确认时，才允许使用截图坐标回退。
- 原生应用可能有多个窗口，窗口选择必须基于 `list_apps()` 返回的应用和窗口对象，并保留标题、窗口身份和当前前台状态作为证据。
- `type_text()` 返回成功不等于用户界面已经正确接收文字。输入后要用新的截图或 AX 观察确认；验收状态区分 `observed`、`typed`、`submitted`、`submission_observed`、`delivery_confirmed`。本地气泡最多支持界面侧提交观察；只有应用提供可归属的送达回执时，才记录 delivery_confirmed。
- “开启视觉理解”只表示允许或支持观察，不等同于桌面操作授权；观察、输入和代表用户发送应保持分离的状态机。
- 网页继续优先走 `browser_page`；QQ、桌面设置、系统文件对话框等非网页表面才由 Computer Use 接管。两条路径共享证据绑定和停止机制，但不应把原生坐标操作硬塞进网页自动化执行器。

公开 API 的调用形态如下；这是纠正原简化示例后的说明，不是本轮执行记录。先从 list_apps/list_windows 返回值唯一选择窗口，再观察并检查结果：

```javascript
const state = await sky.get_window_state({
  window: targetWindow, include_screenshot: true, include_text: true
});
// 检查返回画面后，在后续一次调用中使用 state.window 与观察出的 x/y。
const screenshotId = state.screenshots[0]?.id;
// click 之后须再次 get_window_state，确认编辑区焦点后才能 type_text。
// 截图编号不在 state.screenshotId；include_text 缺省为 false。
```

因此，原生桌面首轮验收应区分“看到目标”“完成输入”“执行提交”“界面显示已提交”和“确认送达”。这次 Codex 实践受其动作时确认规则约束；Akane 在实际执行发送前检查自己的具体授权、目标与内容，不从截图或第三方说明中获得授权。未执行发送时，输入框里的文字仍只是草稿。
