# Akane 目录记忆模式

Akane 默认使用 `MEMORY_BACKEND=memcore`、`MEMCORE_RETRIEVAL_MODE=navigation`。
MemCore 0.1.4 的公共配置支持该模式；MemCore 自身默认仍为 `hybrid`，不改变其他宿主的默认行为。

目录模式保留对话写入、近期上下文、摘要与长期记忆压缩，以及以下读取入口：

- `browse_memory`：按时间、关键词或参与者找目录卡片。关键词可匹配卡片与来源中的实际内容；不提供向量近义召回。
- `open_memory`：用 `memory_id` 展开正文与来源，用 `match_ref` 回读关键词命中的原话；按返回的凭据分页。
- `read_memory_timeline`：读取指定时间或原文锚附近的完整时间线。
- `list_memory_conversations`：列可读会话，再明确选择来源。现有跨会话权限、说话人和角色域校验继续有效。

不创建 Embedding 模型、缓存或搜索索引，不启动索引预热，也不做影子检索。
`retrieve_memory` 从常规工具、原生声明、旧格式提示和能力发现中撤下。
已有会话保存的混合检索声明也立即撤下；其余工具仍遵守原来的发布规则。
保留的读取工具使用当前契约，避免旧说明继续引导模型调用已停用的工具。
显式请求旧检索入口返回 `unavailable` / `retrieval_disabled`，不能解释成没有历史记录。

目录未命中时可以调整已知关键词、参与者或时间范围，并分页读原文核实。
目录不保证近义表达召回；旧事实、偏好和约定的回答仍需实际证据。
长期记忆压缩继续使用已配置的摘要 LLM，与本地 Embedding 无关。

## 启用与恢复

模式在构造时固定，修改后需重启后端。旧的 `EMBEDDING_*` 设置可保留，目录模式不会实例化它们。
`legacy` / `dual` 兼容后端继续使用原来的 Embedding 行为。
设置目录将新模式标为需要重启的配置，不支持在当前实例中途替换依赖。

本次采用正常 wheel 制品接入，宿主精确依赖 `memcore-kernel==0.1.4`。
本地开发制品由 MemCore 仓库的 `python -m build` 生成，安装时只更新该包：

```powershell
.venv/Scripts/python.exe -m pip install --no-index --no-deps <memcore_kernel-0.1.4-py3-none-any.whl>
.venv/Scripts/python.exe scripts/check_memcore_runtime_contract.py
```

部署到另一台机器时，先把 0.1.4 制品加入受信任 wheelhouse 或正式包源，再使用宿主的引导流程；
本地安装不代表已向外部包源发布。运行时不添加相邻源码目录。

恢复混合检索时设置 `MEMCORE_RETRIEVAL_MODE=hybrid`，配置真实 Embedding 后重启。
首次访问命名空间会执行原有索引预热，从同一 SQLite 真值重建索引。
不删对话、不迁移数据库，也不把目录模式的记录伪标为已经入索引。

## 验证与资源

自动验证覆盖无模型构造、真实写入与上下文、关键词原话、说话人、分页、跨会话权限、
持久工具声明撤销、摘要/长期记忆压缩，以及同库重启和混合索引恢复。
`/metrics` 中 Embedding 模型就绪、权重和 CUDA 分配为零；停用状态不表示模型加载失败。
资源验收只启动 Akane 后端，既有 RVC 训练保持运行，不加载第二份 BGE 模型进行对照。

仓库已有的设置目录完整性检查缺少 16 个其他配置项；本次的新模式和资源参数均已登记，
该既有缺口另行处理。外部真实模型测试按依赖条件跳过，目录模式验收不需要模型或 GPU。

本次验证：MemCore 全量 859 项中 850 项通过、9 项依赖测试跳过；宿主 543 项相关检查通过。
MemCore Ruff、格式检查和制品构建通过，宿主相关静态检查、13 个运行包的精确版本检查、启动契约和 `git diff --check` 通过。
设置目录的既有完整性检查单独记录，不计入上述宿主通过数。

2026-10-06 本地 QQ 实例已启动，健康和实例绑定正常，实时设置为 `navigation`。
实时工具目录保留四个导航入口，已无 `retrieve_memory`。
稳定取样时主进程驻留约 159 MiB、私有提交约 882 MiB；含后台插件合计驻留约 324 MiB、私有提交约 996 MiB。
这些内存指标口径不同，会随系统分页和请求变化；对比先前约 17 GiB 的主进程私有提交应使用同一口径。
后端未加载 CUDA 模块、没有 GPU 进程内存条目，Embedding 指标为停用/零。
启动前 Akane 已离线，因此本次只启动后端；四个 RVC 相关进程的 PID 与启动时间保持一致。
