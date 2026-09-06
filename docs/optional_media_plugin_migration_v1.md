# 可选媒体能力插件迁移 V1

日期：2026-09-06。状态：实施中。第一条完整闭环是媒体格式转换；其余音频、翻唱、训练素材、转写、生图和文档能力后续逐组迁移。

## 目标与边界

让用户通过现有扩展市场选装媒体转换，模型安装后可发现并调用，停用后不再收到对应工具和误导性提示。保留原转换的裁剪、码率、采样率、声道、音量、静音裁切、淡入淡出、变速选项；执行、权限、取消、后台 Job、MemCore、生成文件和渠道交付复用宿主。

这一轮不部署云端、不向真实群聊投递、不改桌宠布局，不迁移实时语音 ASR。不能仅把内置工具换个名字，仍让插件导入宿主私有媒体业务实现。

## 迁移起点已确认的事实

- `tool_handlers/catalog.py` 显式组装内置工具；`GeneratedFileService.convert_media_file` 转发给 `generated_files_media.convert_media_file`，业务、来源解析与登记混在一个调用中。
- 插件能力已经复用工具执行、后台 Job、权限、MemCore 与生成文件事件。
- `plugin_generation_artifacts.py` 已有隔离 worker outbox，父进程消费 token 后由 `GeneratedFileManagedArtifactSink` 登记。复用它，不新建媒体传输服务。
- 当前 `ManagedArtifactDraft` 仅内存字节，格式只有 PNG/MD/PDF/XLSX，16 MiB 上限；宿主和桥接器只处理一个产物。
- `PluginRegistrar` 尚无按当前调用会话访问宿主附件/生成文件的公共资源接口。
- 安装器是隔离 site 目录下的 `pip --no-deps --no-index`，不会安装 FFmpeg 或自动解决重型 Python/GPU 依赖。
- Shell 开启时已有 Skill 处理普通 inspect/convert；迁移后保留通用 Shell 能力，但不得隐藏用户主动安装的转换插件。

## 唯一职责

| 领域 | 权威 |
|---|---|
| 文件/图片/音视频句柄与会话归属 | 宿主资源服务 |
| 转换选项、FFmpeg 参数、业务输出 | 媒体转换插件 |
| 文件交接与生成文件登记 | 现有插件 outbox / 产物 sink |
| 安装、启停、更新、失败保留旧代 | 现有扩展管理与 generation |
| 长任务、取消、完成唤醒、记忆 | 现有宿主执行基座 |
| QQ/桌宠交付与表现 | 现有渠道/客户端链 |

## 实施切片

### A. 通用文件和多产物交接

- 在现有 draft 中支持插件本地产物文件，与小型内存字节二选一；文件分块复制，不能整体读入内存或塞入 JSON。
- 产物 payload 改为统一有序列表，同一接口支持一个或多个产物；同步更新仓库调用者、样例和测试，不保留第二套单产物业务实现。
- 已有输出槽的 `max_bytes` 表示调用产物总字节预算，由插件如实声明，不再被固定 16 MiB 文件上限截断。小型内存字节仍有明确保护边界。
- 格式校验验证安全扩展名与 MIME 形状及已知格式一致性，不再使用四种业务格式白名单。
- 父进程只接受本 generation outbox 中的真实文件；公开反馈仅返回生成文件句柄。多产物失败不得丢掉已产生的真实状态或宣称整批成功。
- 复用生成文件事件，逐件交付，登记不是发送成功。

### B. 当前调用的资源访问

- 增加一个公共插件资源端口，使用真实当前调用绑定的 profile/session；插件不能通过自己填写另一个用户号改变授权范围。
- 按现有宿主资源解析能力处理附件与生成文件句柄，返回插件可使用的工作副本和必要元数据；不暴露数据库/缓存位置，不复制来源解析逻辑。
- 使用现有 generation 回调链；访问绑定到实际在途调用，跨会话、过期调用、缺失材料返回结构化错误。
- 调用结束/取消和 generation 退出清理临时副本；不把工作副本当成新的用户附件或记忆。

### C. 真实插件与依赖

- 独立包发布为现有插件市场条目，使用公开 SDK，不导入 `GeneratedFileService`、Engine 或其他宿主私有实现。
- 首个插件自身采用 Python 标准库；外部运行依赖为 FFmpeg/FFprobe，由插件健康检查准确探测并给出可执行的配置说明。缺失时不宣称可执行。
- 不为本轮新增通用依赖求解器、不静默安装系统软件。Python 插件 wheel、平台二进制/外部服务是不同交付物；后续重型插件必须如实声明并验证自己的交付方式，不能借用宿主偶然存在的包。
- FFmpeg 子进程随现有调用取消终止；长任务由 descriptor 声明，不新增业务任务队列。

### C2. 最小市场（审计后补充范围）

- 代码核查发现原有“市场”只有已安装目录、本地源/wheel 暂存和安装生命周期；M67-G 中的分发市场尚未实现。用户已确认“一并补齐最小市场，完成完整闭环”。
- 补齐静态分发目录、浏览、真实 wheel 获取与 SHA-256 校验，再交给唯一的 `ManagedPluginArtifactStore` 暂存与权限确认；不能把已有本地路径安装改名为市场。
- 首个市场条目必须指向真实构建产物，不提交 wheel/缓存、不放假下载地址。目录来源与系统依赖说明明确，安装失败保持已有运行代不变。

### D. 删除被替代路径

- 删除内置转换 handler、spec、工厂组装、`GeneratedFileService` 转换入口和仅服务于该能力的业务函数。
- 共用的基础格式识别、附件解析、输出路径分配、资源登记和发送保留为公共基础；被其他媒体功能真实使用的 helper 暂不删。
- 删除/改写引导模型调用不存在工具的固定提示，更新相关 Skill、能力诊断、前端标签和测试中的确切调用者。
- 不新增动态系统前缀；插件发现和热更新走已有能力目录生命周期。

## 验收矩阵

1. 新建隔离测试实例，通过真实市场/暂存安装服务安装插件；未安装时宿主无转换业务入口。
2. native 与兼容工具投影来自同一 descriptor；安装可见、停用不可见，普通轮次 schema/hash 不变。
3. 真实 WAV 输入转换为不同格式/规格，使用 FFprobe 核验时长、声道、采样率与输出；覆盖裁剪、变速与音量参数。
4. 附件和生成文件句柄均可输入；跨会话、未知句柄、过期调用拒绝；临时副本清理。
5. 多产物按顺序登记/投影/交付，文件大于 16 MiB 不走整体内存传输；输入文件不被修改。
6. 超声明预算、MIME/扩展名不匹配、outbox 伪引用与路径逃逸、写入失败返回真实错误。
7. 慢转换返回 Job ID，前台不阻塞；取消终止 FFmpeg，不产生伪成功、重复完成或重复交付。
8. 依赖缺失、候选激活失败保留 last-good；卸载/停用后不残留工具、提示、worker 或运行任务。
9. 产物通过现有 QQ/桌宠表现链走到真实本地交付边界；模拟平台传输与真实平台已验收必须分开报告。
10. 对照旧代码移除清单做 `rg` 审计、相关回归与 `git diff --check`，逐个已验证切片聚焦提交。

## 当前迁移窗口

起点：宿主 `7669585`。A/B 阶段旧内置转换仍是唯一已可用业务实现；只增加真实公共能力，不新增第二套转换入口。C/D 完成切换后旧实现必须删除。不得以测试通过的新 helper 代替市场安装和业务交付验收。

## 实施记录

### A — 文件/多产物交接

- 文件输入与小字节输入统一为 draft，运行时仅保留有序 tuple；API v1 的 `artifact=`/原位置参数仅在构造边界归一化，避免已安装 wheel 直接失效。
- 真实隔离 worker 一次交接两个 17 MiB 文件，经宿主登记后 outbox 清空；单进程测试禁止 `Path.read_bytes`，确认文件路径不走整体读取。
- 总预算、路径/格式/MIME、重复与伪造引用、部分失败、后续未消费文件清理均有验证；两个 sink 的慢复制取消测试确认先退出写线程再清理，不发生迟到登记。
- 成功多产物逐件进入原 `generated_file_ready` 事件；部分失败保留真实句柄，不自动宣称发送成功。此处的 QQ 传输验证为本地替身，不是实际群聊发送。
- 回归：`tests.test_plugin_managed_artifacts tests.test_plugin_generation tests.test_plugin_host tests.test_plugin_engine_bridge tests.test_backend_route_modules tests.test_qq_gateway tests.test_generated_files`，340 项通过（追加末轮坏引用清理用例前）。
- B/C/D 尚未完成；本切片不使媒体转换变成已安装插件，也不删除仍在服务的旧转换入口。

### B — 当前调用输入资源

- 公开契约为 manifest 的 `resource.read` 与 `registrar.get_resource_port()`；调用 `await resources.open(target)` 返回结构化 `ok/status/reason` 和仅供插件使用的工作副本。
- 端口没有用户号/会话号参数。隔离 worker 自动绑定实际 request id，父进程在发送调用前登记可信 `InvocationContext`；过期回调和非调用上下文不具有读取范围。
- `GeneratedFileService.resolve_input_resource` 仍是附件/生成文件解析的唯一权威；插件不导入这个服务，输入与输出只复用同一个可取消分块复制原语。
- `result.path` 位于本次调用的临时目录，可在其目录创建本次输出并返回 `ManagedArtifactDraft(path=...)`；不可把这个临时路径返回为普通公开内容或保存为长期引用。宿主完成产物交接后清理整次调用目录。
- 宿主取消等待 worker 的真实结束响应后再删除副本；复制取消先等线程退出。未交接的 worker outbox 文件也随取消清理，已发送响应的产物由父进程接管。
- 本地测试覆盖真实隔离进程读取两种资源、跨用户/会话拒绝、未知句柄、权限缺失、过期调用、修改副本不影响原件、复制中取消、worker 取消后复用 generation。尚未以此完成真实媒体转换插件的安装与业务验收。
- 回归：资源/产物/generation/candidate/runtime/host 共 81 项通过；Engine bridge 与 BotRuntime 共 34 项通过。`ruff check` 与 `git diff --check` 通过。

插件使用示意（仅展示公共接口，完整例子随 C 提交）：

```python
def register(self, registrar):
    self.resources = registrar.get_resource_port()
    registrar.add_capability_adapter(self.adapter)

async def invoke(self, capability_id, args, context):
    source = await self.resources.open(args["target"])
    if not source.ok:
        return CapabilityResult(is_error=True, status=source.status, reason=source.reason)
    # source.path 是可操作副本，不是原始附件缓存位置。
```

### C1 — 独立转换插件与隔离安装

- `plugins/akane_media_convert` 只使用标准库、CapCore 和公开 `plugin_api`，独立拥有 FFmpeg 业务参数，不导入宿主转换实现。输出七种音频格式，视频提取音频不伪装成视频转码。
- 健康检查运行 FFmpeg/FFprobe；宿主保留已脱敏的具体依赖失败原因。候选依赖失败不替换正在运行的已安装版本。
- 独立源码测试实际读取输出 PCM/探测结果，覆盖全部格式、时间段与倍速、采样率/声道、响度/增益、头尾静音和淡入淡出；FFmpeg 真实取消与重复取消后的进程退出等待均有测试。
- 宿主测试从复制的源码构建 wheel，经真实暂存、权限确认、隔离激活、附件与生成文件输入、转换输出、跨会话拒绝、停用、启用、卸载全链通过；未修改真实实例或向 QQ 发送。
- C1 与 host 回归 28 项通过；源码含重复取消用例 10 项通过，ruff 与 diff 检查通过。市场 C2、模型/Job/渠道验收与旧入口删除 D 仍待完成，不能把 C1 当成整项迁移结束。

### C2 — 最小静态市场

- 新增受信静态目录分发层，本地发行目录/HTTPS 共用 index 契约；浏览不导入插件，不修改运行状态。真实 wheel 大小与 SHA-256 校验后进入唯一的既有暂存器。
- `scripts/build_plugin_market.py` 已实际构建首个可安装发行目录，源码、索引声明与构建脚本纳入 Git，wheel/生成索引留在忽略的 `.plugin-market`，未发布公网源。操作说明见 `plugin_market_v1.md`。
- 模型的 `manage_extension` 与控制中心复用原审批/安装生命周期；UI 增加依赖预览和精确 hash 选择，待确认候选脱离本地安装折叠区，市场失败不冒充空目录或已可用能力。
- 真实市场转换生命周期与失败测试 9 项通过；扩展管理/host/BotRuntime/安装回归 71 项通过。控制中心动作和 V2 smoke 通过，后者实际运行 bridge，覆盖慢请求、切换来源与停止后的迟到响应。
- 浏览器在临时验收实例查看实际市场与真实暂存候选，修正卡片样式接入；没有 UI 安装真实用户实例或 QQ 发送。D 的内置入口清除及最终模型/Job/交付验收仍未完成。
- 最终市场/真实转换/安装器/后端路由回归 126 项通过；模型管理工具审批 5 项通过（包含精确 hash 绑定）；前端 smoke 与 Vite build 再次通过。临时实例通过 API 安装/卸载后，浏览器确认运行卡片真实出现/消失；验收进程与页面已关闭。
