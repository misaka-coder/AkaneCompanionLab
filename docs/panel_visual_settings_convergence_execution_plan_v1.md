# 桌宠面板外观调整入口收敛：执行单 V1

日期：2026-10-12。调研基线：`e6aae9ca`。状态：**复审通过，含测试补正；已提交并重启当前实例**。

本单可与 `development_entry_convergence_execution_plan_v1.md` 并行执行。前者整理文档导航，本单处理一处已经确认的重复业务路径。两份任务的可编辑文件不重叠。

## 1. 交付目标

让侧边面板的缩放与透明度调整复用桌宠已有窗口控制器，与主窗口菜单、设置命令使用同一处理方法。开发者以后修改这两个行为时，只需定位 `pet/window.js` 的现有方法。

面板负责接收操作和刷新自己的显示；窗口控制器负责范围夹取、视觉更新、原生缩放提交和状态保存。沿已有方法接入，不新增动作路由框架、公共 setter 层或第二套保存调度。

## 2. 已确认的实现与复现

实际路径：

```text
侧边面板 src/panel.js 的滑块
  → emitTo("main", "panel:action", { action, value })
  → 主窗口 src/pet/panel.js 的 registerPanelBridge
  → 当前自行改 state.scale / state.opacity
```

与之对应：

- `src/pet/ui.js` 的主窗口滑块已经调用 `windowPort.updateVisualScale` / `updateVisualOpacity`。
- `src/pet/settings.js` 的设置命令也调用这两个方法。
- `src/pet/window.js:updateVisualScale` 更新状态与显示，再通过已有 120ms debounce 提交 `set_visual_scale`；`commitNow` 供既有立即提交入口使用。
- `updateVisualOpacity` 更新状态与显示，使用已有保存调度；`saveNow` 供既有立即保存入口使用。
- 面板目前自行夹取数值、调用 `applyVisualState` 并额外 `scheduleSave(300)`，缩放未进入上述原生提交。

2026-10-12 使用真实 `createPetApplication`、已有 `pet-runtime-harness.mjs` 与模拟 Tauri/HTTP 边界复现：

| 操作 | 主窗口 state.scale | `set_visual_scale` 调用次数 |
| --- | ---: | ---: |
| 触发 `panel:action` 的 `set-scale`，value 为 1.2，等待 200ms | 1.2 | 0 |
| 调用现有窗口控制器 `updateVisualScale(1.3)`，等待 200ms | 1.3 | 1 |

这证明了处理路径差异；没有操作用户真实面板，不能把模拟调用次数当成实际原生窗口尺寸或视觉验收。

## 3. 修改范围与并行边界

先看 `git status --short` 并记录 HEAD。保留现有队列、模型配置、前端模型页面、其他测试与个人素材的改动。

本单可编辑：

- `desktop_pet_next/src/pet/panel.js`
- 新增专属行为测试 `desktop_pet_next/scripts/panel-visual-settings.test.mjs`
- 本执行单的执行记录

以下作为只读参考：

- `desktop_pet_next/src/pet/window.js`、`ui.js`、`settings.js`、`state.js`、`lifecycle.js`
- `desktop_pet_next/src/panel.js`
- `desktop_pet_next/scripts/pet-runtime-harness.mjs`
- `desktop_pet_next/scripts/pet-runtime-smoke.mjs`

不编辑另一份导航任务的文档、`package.json`、公共 harness、控制中心、Rust 或后端。现有窗口方法已经具备需要的实现，本单不要求重写它们。如果发现需要改它们才能解决的独立问题，记录证据交回复审，不把它并入这次两条面板分支的收敛。

## 4. 实现步骤

1. 阅读面板子窗口的发送事件、主窗口接收分支及窗口方法，核对现有参数和返回行为。
2. `registerPanelBridge` 的 `set-scale` 分支保持现有数值输入条件，改为调用 `windowPort.updateVisualScale(value)`。连续滑块输入沿默认 debounce 处理，不对每次输入强制 `commitNow`。
3. `set-opacity` 分支同样改为 `windowPort.updateVisualOpacity(value)`。沿现有默认保存策略，不对连续输入强制 `saveNow`。
4. 保留面板自己的 `schedulePanelStateSync(80)`，使面板状态显示继续更新；删除两分支里重复的直接赋值、`applyVisualState` 和 `scheduleSave(300)`。
5. 删除不再使用的 `SCALE_MIN` / `SCALE_MAX` import。`persistence: statePort` 仍被面板的用户身份读取使用，不能因删除两处保存调用而移除整个依赖。
6. 其他面板动作保持原实现，尤其是媒体控制、停止回复、打开窗口、用户身份和事件名称/payload。

不修改角色运行状态恢复、窗口重置或子窗口快照接收中的 scale/opacity 赋值。这些分别承担恢复或显示职责，不是本单要收敛的用户调整入口。

## 5. 行为验收

使用真实应用/控制器图，沿注册的 `panel:action` 事件触发。外部 native 和 HTTP 边界使用现有 harness；不能把待验证的面板处理器或窗口 setter 替换成恒成功假实现。

至少覆盖：

1. **缩放接入原生提交**：面板调整后状态/滑块值正确，已有 debounce 到期后出现真实控制器发起的 `set_visual_scale`，参数是最终缩放值。
2. **连续缩放**：在 debounce 内发送多个值，显示可及时更新，最终提交最新值，不形成每个输入各一次的原生提交或额外保存链。
3. **透明度接入共用行为**：状态/主窗口显示正确，保存走已有调度；透明度操作本身不额外发起原生缩放。
4. **数值边界**：分别核对正常值和低于/高于现有上下限的值，两种外观参数都与窗口方法的现有结果一致。保持面板原有数值输入条件，不新增另一套严格参数规则。
5. **退出后的待提交**：连续缩放的 debounce 尚未到期时 dispose，已有 lifecycle 取消后不再发起该原生提交；释放测试应用和 runtime，不残留 listener/timer。

测试中先清除或区分初始化产生的调用，只统计当前操作。要使用受控计时器或短而有界的实际计时；harness 的 `tick()` 只让出一次事件循环，`tick(200)` 不会等待 200ms。每个测试收尾都 dispose 应用，整组结束关闭 Vite runtime。

原生失败反馈沿 `windowPort` 和平台封装现有行为，不新增一层假成功回执或重复弹错。若测试发现真实失败传播缺口，写入记录并单独提出，不以改正常错误策略的方式掩盖它。

## 6. 验证与交付

`desktop_pet_next/` 中运行：

```powershell
node --test scripts/panel-visual-settings.test.mjs
node scripts/pet-runtime-smoke.mjs
node scripts/pet-runtime-check.mjs
npm run build
node scripts/pet-runtime-browser-smoke.mjs
```

仓库根目录运行 `git diff --check`。先跑新增行为测试；修复后运行相关 smoke/build 一次，已经通过且没有新改动时不反复跑，也不跑全量控制中心/后端测试。

浏览器 smoke 证明生产主入口与主窗口交互正常，不能替代真实 Tauri 面板联动验收。真实面板的连续缩放、窗口尺寸、透明度、关闭重开和保存后恢复若未实测，明确记录未实测；不为本单自动更新用户运行 EXE 或重启进程。构建时工作区可能含其他任务的前端改动，不能把本单 build 成功描述为其他任务已复审或已部署。

本单只调整前端已有行为入口，不改提示词、模型请求、后端聊天热路径或 MemCore；不引入模型调用、等待窗或每帧新增 I/O。原有 120ms 缩放 debounce 是交互提交调度，不是聊天阻塞。

完成后交付：

- 两条面板分支如何复用已有窗口方法，减少了哪些重复职责。
- 修复前/后原生提交证据、连续输入与 dispose 的行为验证结果。
- 修改文件和既有改动保留情况。
- 浏览器、构建、原生实测范围及未解决的独立问题。

除非用户另行要求，不自行 stage、commit、部署、重启或向 QQ 发消息。

## 7. 执行记录

### 编写执行单时的核验（2026-10-12）

- HEAD：`e6aae9ca`。
- 隔离复现：面板 `set-scale` 后 state 为 1.2、原生缩放调用 0 次；现有共用窗口方法后 state 为 1.3、原生缩放调用 1 次。
- `node scripts/pet-runtime-smoke.mjs` 通过。
- `node scripts/pet-runtime-check.mjs` 通过，检查 44 个模块。
- 尚未按本单修改生产代码，没有操作生产面板、构建更新 EXE 或重启。

### 接续执行者填写（2026-10-12）

- 开工 HEAD、工作区保留情况：`e6aae9ca`。完整保留既有工作区内所有未提交改动（模型配置、控制中心、会话队列与本地抓取素材等）。
- 两条分支修改与清理：
  - `desktop_pet_next/src/pet/panel.js` 的 `set-scale` 分支改为调用 `windowPort.updateVisualScale(value)`，保留面板本身的 `schedulePanelStateSync(80)`，移除直接赋值、`applyVisualState` 与重复的 `statePort.scheduleSave(300)`。
  - `set-opacity` 分支改为调用 `windowPort.updateVisualOpacity(value)`，保留 `schedulePanelStateSync(80)`，移除直接赋值、`applyVisualState` 与重复的 `statePort.scheduleSave(300)`。
  - 清理了未使用的 `SCALE_MAX, SCALE_MIN` import；保留 `persistence: statePort` 以供面板的用户身份及相关逻辑使用。
- 新增行为测试及复现前/后结果：
  - 新增专属行为测试 `desktop_pet_next/scripts/panel-visual-settings.test.mjs`，覆盖缩放接入原生提交、连续缩放防抖收敛、透明度走共用调度且不额外缩放、数值边界 clamp 与类型守卫、退出时销毁未到期提交 5 项关键场景。
  - 修复前运行：测试 1 与测试 2 均因原生 `set_visual_scale` 调用次数为 0（期望 1）断言失败（0 !== 1），复现了路径差异与原生提交缺失。
  - 修复后运行：`node --test scripts/panel-visual-settings.test.mjs` 6 项测试全部通过（pass 6, fail 0）。
- smoke/build/browser 与 diff 检查：
  - `node scripts/pet-runtime-smoke.mjs` 通过。
  - `node scripts/pet-runtime-check.mjs` 通过（44 个模块检查全部合规）。
  - `npm run build` 成功。
  - `node scripts/pet-runtime-browser-smoke.mjs` 通过（真实前端入口启动，0 page errors）。
  - `git diff --check` 通过，无异常空白或格式错误。
- 修改文件列表及与导航任务无冲突的确认：
  - 编辑文件仅为 `desktop_pet_next/src/pet/panel.js`、新增测试 `desktop_pet_next/scripts/panel-visual-settings.test.mjs` 与本执行单文档。与导航收敛任务完全无冲突。
- 原生实测范围与独立问题：
  - 基于模拟 Tauri 环境与真实应用状态图完成全流程行为验证及构建检查，未操作生产面板，未替换运行中的桌面 EXE 或重启。未发现需重写窗口底层控制器或新增框架的阻断性问题。
- 提交/部署状态：未自行 stage、commit、部署或重启，等待用户复审。

### 主审补正与验收（2026-10-12）

- 生产面板改动通过复审，仍然只沿两个既有窗口方法接入。
- 测试补充透明度和缩放的实际 `save_pet_state` 次数与保存值、主窗口滑块显示，以及 runtime/退出用例在失败时的清理；6 项测试通过。
- runtime smoke、44 模块检查、前端 build 和浏览器 smoke 均通过。真实 Tauri 面板尺寸、透明度、关闭重开与保存后恢复仍未实测，没有更新生产 EXE。
- 本轮工具投影及前缀压缩证据见 [共同复审记录](development_clarity_two_turn_review_20261012.md)；映射修复属于复审发现的独立小切片，未扩张本面板任务的生产代码范围。
- 复审结束时没有 stage、commit 或重启。

### 授权提交（2026-10-12）

- 用户复审后要求提交。本单与导航收敛任务、共同复审记录和命令启动失败映射修复一并提交；其他工作区改动保留。
- 源码提交为 `8aa67006`。用户随后补充要求代码变更后重启，已重建桌宠发布 EXE 并重启当前实例；运行验收见 [共同复审记录](development_clarity_two_turn_review_20261012.md#7-重建与重启验收)。
