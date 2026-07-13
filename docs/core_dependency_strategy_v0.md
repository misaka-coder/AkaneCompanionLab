# Core Dependency Strategy v0

Updated: 2026-07-13

Akane 正在把可复用内核从宿主项目里拆出来：

- `capcore`: 能力接入、manifest 校验、tool schema 投影、调用参数校验、权限决策。
- `capcore-adapter-mcp`: MCP stdio 能力适配器，负责 MCP tool 到 capcore descriptor/invoke 的转换。
- `capcore-adapter-python`: 本地 Python callable 能力适配器，负责显式注册 callable 到 capcore descriptor/invoke 的转换。
- `capcore-adapter-speech`: 语音 TTS/ASR 能力适配器，负责 GPT-SoVITS/Edge TTS/OpenAI-compatible ASR 到 capcore descriptor/invoke 的转换。
- `capcore-adapter-comfyui`: ComfyUI workflow 能力适配器，负责 loopback client、workflow slot patch、输出图片回收和 capcore descriptor/invoke 的转换。
- `memcore`: 分层记忆、可见记忆、检索工具、时间线读取、压缩沉淀。

## 当前形态

Akane 不 vendoring core，也不从 sibling checkout 导入。运行时包在
`requirements-packages.txt` 中精确锁定为版本化发行物，由完整 wheelhouse 或
显式配置的包索引提供。源码仓库的位置不属于安装和运行契约。

`capcore-adapter-mcp` 先由 Akane 的兼容包装层接入；Akane 仍保留 profile
配置、approval UX、prompt 暴露策略和 AnySearch dotenv hydration 等宿主逻辑。
`capcore-adapter-speech` 由 Akane 的旧 TTS/ASR 模块 re-export 接入；Akane 仍
保留 voice route、profile 存储、公开配置脱敏、音频交付和 UI。
`capcore-adapter-comfyui` 由 Akane 的旧 ComfyUI 模块 re-export 接入；Akane 仍
保留 workflow route、profile 配置、job 状态、图片交付和 UI。
`memcore` 通过 `MemorySystem` 公共 facade 使用，不复制 `memcore` 内部模块到 Akane。

## 为什么不 vendoring

不把 core 代码复制进 Akane，原因是：

- core 可以独立测试、发布和授权。
- Akane 的宿主逻辑不会污染 core 的通用边界。
- 修复能力安全或记忆系统问题时，可以先在 core repo 收紧，再反哺 Akane。
- 其它项目可以复用同一套 core，而不是从 Akane 里二次拆代码。

## 开源安装策略

当前发行策略：

- 为所有 extracted packages 构建独立 wheel；
- Akane 对内部运行时包使用精确版本，不允许 editable/path override；
- Windows bootstrap 只接受带完整 manifest 的 wheelhouse 或显式包索引；
- clean-environment gate 在禁用 `PYTHONPATH` 和 user site 后离线安装并跑 smoke；
- 联动开发也先构建本地版本化 artifact，不用 sibling path 覆盖消费者环境。

长期产品化：

- Akane 的基础安装只依赖小 core 和必需 Web 后端包。
- 重型能力按 extra 或独立 requirements 拆分，例如 vector、document、media、local-ML。
- CI 至少覆盖 core-only startup、capcore adapter smoke、memcore chat smoke。

## 边界规则

Akane 保留：

- UI、路由、profile 配置、审批队列、用户授权记录。
- Akane 的 MCP profile 配置、dotenv hydration、ComfyUI、voice route、profile 存储和音频交付。
- Akane 特有的角色、人设、桌宠、QQ/Web/Desktop 宿主行为。

core 保留：

- `capcore`: descriptor、manifest、projection、invocation validation、permission decision。
- `capcore-adapter-mcp`: MCP stdio descriptor conversion 和 JSON-safe invoke bridge。
- `capcore-adapter-speech`: TTS/ASR descriptor、loopback client、安全 profile 清洗和 invoke bridge。
- `capcore-adapter-comfyui`: ComfyUI loopback client、workflow slot patch、输出图片抽取和 workflow execution 数据结构。
- `memcore`: namespace、memory lifecycle、retrieval/timeline tools、prompt context rendering。

当 Akane 需要补能力判断或记忆行为时，优先问：

```text
这是通用机制吗？是则进 core。
这是 Akane 宿主表现吗？是则留在 Akane。
```
