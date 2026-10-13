# CapabilityRegistry 职责拆分

日期：2026-10-04。

`companion_v01/capability_registry.py` 从 1,986 行收敛为 171 行兼容入口。
实现按工具领域和回合职责归入 `capability_registry_runtime/`，共 11 个实现模块，最大模块 296 行。
既有调用方继续从原入口导入；工具参数、描述、风险声明和默认能力分组保持原有契约。

## 定位入口

| 文件 | 修改或排查的内容 |
| --- | --- |
| `memory_contracts.py` | MemCore 原生契约投影、来源会话与分页约束、记忆和角色上下文工具。 |
| `browser_contracts.py` | 公开搜索、托管页面操作、桌面浏览器打开工具。 |
| `attachment_contracts.py` | 附件检查与焦点、重试、图像材料重载、文档分段读取。 |
| `workspace_contracts.py` | 工作区查询、读取、登记，以及生成文件检查和生命周期管理。 |
| `delivery_contracts.py` | 已有文件交付、QQ 表情包、OneBot 动作、音乐卡片和语音交付。 |
| `media_contracts.py` | 音乐搜索、公开媒体获取、媒体信息检查。 |
| `models.py` | 不可变快照、模块定义、回合选择结果、能力披露和运行时披露投影。 |
| `context.py` | 附件与生成文件分类，以及文档、媒体、图像、宿主执行配置的触发判断。 |
| `modules.py` | 有序工具分组、客户端范围、提示和潜在能力的激活规则。 |
| `offers.py` | 本地处理器登记、带会话范围的可用性探测与 TTL 缓存。 |
| `selection.py` | 本轮工具选择、隐藏和允许列表、稳定 schema、桌面执行器凭据。 |

内部实现直接依赖所属模块，避免反向导入兼容入口。原入口明确列出公开导出；类型和规范工具定义只保留一份对象。
`ExecutorBroker`、执行回执和请求指纹仍由既有 `executor_broker.py` 提供。
`_memcore_tool_contract` 的包构建器注入接口保留，真实投影实现归于 `memory_contracts.py`。

## 真实调用链和边界

`engine_services/tool_rounds.py` 组装当前处理器、宿主配置和会话快照，刷新 `ServerLocalOfferIndex`，再调用 `CapabilityRegistry.select()`。
`modules.py` 与 `context.py` 决定当前配置和材料范围，`selection.py` 分别组装发布的工具契约与可执行工具。
本地探测失败或桌面执行器断线时，能力会标记为不可用，当前配置拥有的 schema 继续保留。
执行配置触发的内置媒体工具重叠处理，继续以宿主配置为准；已安装扩展的发现规则保持独立。

可见提示、已发布契约和执行许可各有归属。执行允许范围、冻结处理器、执行器凭据和审批仍走现有宿主与 Broker 链路。
拆分没有增加路由入口，也没有让隐藏工具或懒加载 MCP 越过执行范围。

## 本轮修正

- 探测缓存以工具、用户、会话、客户端模式为键。处理器替换、移除或显式重新登记会同时使缓存与在途探测失效，包含同一对象移除后重新安装的情况。
- 同一范围内较晚发起的探测拥有最新结果。旧探测不能覆盖它；新探测尚未完成时，旧调用返回不可用；已经完成时可复用仍有效的新结果。
- 未变化的处理器映射刷新保留原有缓存与在途探测；探测继续在锁外执行，独立工具和范围可以并发检查。
- 读取 `capability_status` 属性本身出错时，按不可用处理。无探测方法的静态处理器继续可用，未知工具仍不可用。
- `CapabilityRegistry(modules=())` 保持显式空分组；只有 `modules=None` 使用默认分组。
- `open_browser` 被隐藏或不在允许列表内时，跳过无用的桌面执行器回执查询；其他已配置工具的查询照常进行。

现有就绪与不可用缓存期限、显式 `offered` 配置事实的优先级保持原样。以上修正不更改正常配置下的工具契约。

## 验证记录

拆分前后对比了 27 个完整工具定义、有序默认模块、材料分类和 4,392 组选择结果，内容一致。
矩阵涵盖客户端、材料和执行标记的组合、允许与隐藏列表，以及桌面执行器离线和在线状态。

`test_tool_readiness` 新增确定性并发、缓存期限、范围隔离、探测异常和空分组测试；
`test_capability_registry_modules` 检查规范对象与旧入口的同一性、Broker 导出和无环依赖。
同时复查原生 schema、QQ 执行开关、受限选择、执行器凭据、媒体 Shell、工具暴露、能力发现、联网搜索与导入链。
共复查 269 项，266 项通过；Python 编译与 `git diff --check` 通过，未出现新的失败。

完整复查中的以下 3 项在本轮改动前已经失败，并单独保留基线记录：

- `test_vn_extensions.EngineExtensionTests.test_build_embedding_provider_uses_jina_without_hashed_fallback`
- `test_vn_extensions.EngineExtensionTests.test_build_embedding_provider_wraps_huggingface_provider_with_cache`
- `test_vn_extensions.EngineExtensionTests.test_desktop_workspace_materials_expand_matching_tools_without_upload`

前两项是嵌入提供者类型断言，后一项是既有工作区稳定提示文案断言；本轮未修改对应实现或测试。
本地检查采用模拟服务与临时工作区，没有进行真实 QQ、桌面执行器或外部服务的端到端验收。
