# M1 实施任务交接 Prompt

> 这份文档是给**下一个接手实施 M1 的 AI** 看的执行手册。
> 用法：把下面 "===" 之间的整段内容作为你给那个 AI 的初始任务提示词。

===

# 任务：执行 AkaneCompanionLab Capability Adapter v1 — M1（骨架）

你将完成 `F:\Akane\AkaneCompanionLab` 项目里 Capability Adapter v1 的第一个里程碑（M1: 骨架）。这是一个有完整设计文档和实施清单的工程任务，不是开放性探索。

## 0. 第一件事——按顺序读完三份文档再动手

不要边读边改。全部读完再开工：

1. `CLAUDE.md` — 项目工作纪律。每一条都要遵守，特别注意"小步但完整"、"失败要结构化"、"不要 fake action"、"不确定先降级"。
2. `docs/capability_adapter_v1.md` — 完整设计（这是 **why**）。
3. `docs/capability_adapter_v1_m1_ticket.md` — M1 实施清单（这是 **what**）。**整篇是你的 ground truth**。

读完后**用一两句话向用户复述 M1 的边界**（在做什么 / 不在做什么），用户确认后再动手。这是防止误读。

## 1. 开工前的现实验证（pre-flight）

ticket 第"M1 期间的本地决议"节里是上一个 AI（设计阶段）做出的 6 条假设。**动手前先验证前 4 条与代码现实一致**：

1. `grep -E "^yaml|PyYAML" requirements.txt` —— 是否已含 PyYAML？没含就要把"M1 引入 PyYAML 作为运行时依赖"明确写进你的 commit message，并在 requirements.txt 加上。**不要私自换成 ruamel/strictyaml**。
2. 读 `companion_v01/engine.py` 的 `AkaneMemoryEngine.__init__` 末尾 ~30 行，确认在哪一行插入 `self.capability_adapter_registry = ...` 不和其他 service 初始化冲突。
3. 在 engine.py 上 grep `profile_user_id`，确认怎么取到当前 profile id。如果没有现成路径——**停下，报告用户，不要私自加**。
4. 读 `companion_v01/local_capability_config.py` 全文，确认 ticket 提到的可复用件真的存在：`LOOPBACK_HOSTS`、`MCP_SECRET_MARKERS`、`MCP_SAFE_TYPE_RE`、`capability_approval_mode()`、`APPROVAL_MODE_*` 常量。任一缺失 → 停下报告。

pre-flight 完成后向用户报告一次状态（"4 项验证完成，PyYAML 已/未存在，engine 插入点为 line X，profile_user_id 从 Y 取，常量全部存在"），用户认可后进入第 2 节。

## 2. 执行顺序

严格按这个顺序，每完成一步跑一次相关验证再走下一步。**不要并行做多步**。

**Step A — 数据 / 契约层**（纯新增，不破坏任何现有代码）：

- `companion_v01/capability_adapters/__init__.py`
- `companion_v01/capability_adapters/types.py`（dataclasses + `InvalidManifest` + 异常类）
- `companion_v01/capability_adapters/protocol.py`（`CapabilityAdapter` Protocol，仅签名）
- 验证：`python -c "from companion_v01.capability_adapters import CapabilityManifest, CapabilityAdapter"` 不报错

**Step B — Loader**：

- `companion_v01/capability_adapters/manifest_loader.py`
- 同步写 7 个 fixture 到 `docs/fixtures/capability_adapter_m1/`
- 同步写 `tests/test_capability_adapter_manifest_loader.py`（10 用例，见 ticket §测试要点）
- 验证：`python -m unittest tests.test_capability_adapter_manifest_loader` 全绿

**Step C — Registry**：

- `companion_v01/capability_adapters/registry.py`
- 同步写 `tests/test_capability_adapter_registry.py`（5 用例）
- 验证：`python -m unittest tests.test_capability_adapter_registry` 全绿

**Step D — engine 接入**：

- 只加 registry 字段 + `_resolve_profile_capability_manifests_dir` 方法 + 在 `__init__` 末尾调一次 `scan()`
- 不动其他任何东西
- 启动后端一次，确认日志能看到 `CapabilityAdapterRegistry scanned: 0 valid, 0 invalid`（内置目录暂空）

**Step E — 回归与收尾**：

- `python -m unittest tests.quick_regression_suite` 必须不退步
- `git diff --check` 必须通过
- 在 `docs/capability_adapter_v1.md` §8 那张 milestone 表，把 M1 行追加 "Completed: YYYY-MM-DD" 标记

## 3. 边界纪律（CLAUDE.md 在此重申）

- ❌ 不改任何 UI 代码（Tauri / web / desktop_pet / desktop_pet_next）
- ❌ 不改 `companion_v01/routes/`（M1 不加路由）
- ❌ 不改 `tool_runtime.py` / `capability_registry.py` / `tool_orchestration_engine.py`（M2 起再说）
- ❌ 不动 `mcp_stdio_discoverer.py` / `local_workflow_runners/comfyui.py`（M2/M3）
- ❌ 不顺手重构看不顺眼的相邻模块
- ❌ 不为了让测试过而放宽校验规则——校验规则是契约，错的是 fixture 不是 loader
- ❌ 不引入任何新依赖（PyYAML 除外，且仅在 pre-flight 验证它不存在时）
- ❌ 不创建 README / docs 类的"装饰性"新文档，除非用户明确要求

## 4. 失败 / 不确定时的处理

ticket 是设计阶段写的，可能跟代码现实有偏差。处理原则：

- **小偏差**（字段名拼写、helper 函数位置）：自行修正，在 commit message 注明
- **中偏差**（ticket 假设的常量不存在、engine 接口不一样、`__init__` 里没有合适的插入点）：停下，把现状报告给用户，**等指示**
- **大偏差**（设计前提不成立，比如 manifest schema 跟现有系统冲突无法调和）：停下，**绝对不要自行扩大范围去"修复"设计**——退回到讨论

校验失败的处理（这是设计的关键纪律）：
- `manifest_loader.load_manifest()` 校验失败 → **返回** `InvalidManifest`，**不抛异常**
- 任何 yaml 解析错也走 `InvalidManifest` 路径
- 单个坏 manifest **不能**让 registry.scan() 报错

## 5. 完成时报告这些（不要只说"做完了"）

- 新建文件列表 + 每个文件行数（`wc -l`）
- `engine.py` 改了哪几行（贴 diff）
- 两个新测试文件的用例数和通过 / 失败结果
- `tests.quick_regression_suite` 的结果（是否仍然全绿）
- pre-flight 验证里踩到的坑（哪条假设错了、你怎么处理的）
- 跟 ticket "M1 期间的本地决议"有任何偏离都列出
- 你认为 M2（MCP 闭环）实施前还需要先知道什么

## 6. 心智校准

M1 是骨架阶段。**M1 完成时，用户不会感觉到任何功能变化**——没有新 UI、没有新工具能调用、没有路由变化、桌宠 / web / QQ 行为完全不变。

如果你在做的事让你觉得"这下用户能用上 X 了"，那你就走偏了——你做到 M2 / M3 的事情上去了，立即停下回头。

M1 唯一的可观察变化是：engine 启动日志多一行 `CapabilityAdapterRegistry scanned: ...`。仅此而已。

===

## 给用户的使用说明

复制上面 "===" 之间的整段，粘贴到任何能阅读你这个项目的 AI 会话里（Claude Code / Codex CLI / Cursor / 任何 agent）。它读完会先做 pre-flight 报告，你确认后它再动手。

整个 M1 估时 3-5 小时一个工作切片。如果 AI 跑了 8 小时还没完成，说明它走偏了，让它停下。

完成后让它把报告贴给我（任何 AI 审查会话），我可以做最后一道审。
