# Core Dependency Strategy v0

Updated: 2026-06-30

Akane 正在把可复用内核从宿主项目里拆出来：

- `capcore`: 能力接入、manifest 校验、tool schema 投影、调用参数校验、权限决策。
- `capcore-adapter-mcp`: MCP stdio 能力适配器，负责 MCP tool 到 capcore descriptor/invoke 的转换。
- `memcore`: 分层记忆、可见记忆、检索工具、时间线读取、压缩沉淀。

## 当前形态

源码 Alpha 使用 sibling checkout，而不是 vendoring：

```text
Akane/
  AkaneCompanionLab/
  capcore/
  capcore-adapter-mcp/
  memcore/
```

这些包已是 Akane 运行时依赖，`requirements.txt` 使用：

```text
-e ../capcore
-e ../capcore-adapter-mcp
-e ../memcore
```

`capcore-adapter-mcp` 先由 Akane 的兼容包装层接入；Akane 仍保留 profile
配置、approval UX、prompt 暴露策略和 AnySearch dotenv hydration 等宿主逻辑。
`memcore` 通过 `MemorySystem` 公共 facade 使用，不复制 `memcore` 内部模块到 Akane。

## 为什么不 vendoring

不把 core 代码复制进 Akane，原因是：

- core 可以独立测试、发布和授权。
- Akane 的宿主逻辑不会污染 core 的通用边界。
- 修复能力安全或记忆系统问题时，可以先在 core repo 收紧，再反哺 Akane。
- 其它项目可以复用同一套 core，而不是从 Akane 里二次拆代码。

## 开源安装策略

短期源码 Alpha：

- README 明确要求 sibling checkout。
- Windows bootstrap 在安装依赖前检查 `../capcore/pyproject.toml` 和
  `../capcore-adapter-mcp/pyproject.toml`。
- `requirements.txt` 保留 editable path，方便本地联动开发。

中期公开包：

- 为 `capcore` / `capcore-adapter-mcp` / `memcore` 发布版本包。
- Akane 把 editable path 替换为版本范围，例如 `capcore>=0.1,<0.2`。
- 开发者仍可用 editable install 覆盖本地 core。

长期产品化：

- Akane 的基础安装只依赖小 core 和必需 Web 后端包。
- 重型能力按 extra 或独立 requirements 拆分，例如 vector、document、media、local-ML。
- CI 至少覆盖 core-only startup、capcore adapter smoke、memcore chat smoke。

## 边界规则

Akane 保留：

- UI、路由、profile 配置、审批队列、用户授权记录。
- Akane 的 MCP profile 配置、dotenv hydration、ComfyUI / TTS / ASR 等宿主 adapter。
- Akane 特有的角色、人设、桌宠、QQ/Web/Desktop 宿主行为。

core 保留：

- `capcore`: descriptor、manifest、projection、invocation validation、permission decision。
- `capcore-adapter-mcp`: MCP stdio descriptor conversion 和 JSON-safe invoke bridge。
- `memcore`: namespace、memory lifecycle、retrieval/timeline tools、prompt context rendering。

当 Akane 需要补能力判断或记忆行为时，优先问：

```text
这是通用机制吗？是则进 core。
这是 Akane 宿主表现吗？是则留在 Akane。
```
