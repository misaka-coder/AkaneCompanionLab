# Akane 沉浸式互动场景 V1

新设计评审入口：[互动剧情与演出设计 V2](interactive_story_v2_design.md)（2026-09-20，评审稿，尚未执行）。

日期：2026-09-19。状态：房间与剧情原型已实现，正在收口体验与验收。

本文保留最初设计基线。当前已有 Vue/Pixi 房间、养成操作、演出队列和可导入剧本；这不代表全部里程碑已验收。后续顺序见 [下一轮计划](next_iteration_plan.md)。用户已授权由开发者补全未定设计，先做出可体验版本，再按实际反馈调整。

## 产品决定

在现有项目中新增独立的沉浸式场景入口，沿用宿主、角色身份、模型接入、MemCore、CapCore、插件与权限。重新设计场景前端和演出协议。旧 Galgame 代码只作为行为与接口参考，不作为新页面的实现骨架。

体验以「操作 → 即时反馈 → 真实状态变化 → 角色理解与回应 → 留下经历」为主线。聊天、触摸、换装、购买、投喂、资源导入和剧情活动都能发起交互。

程序确认库存、消费、装备与场景等事实；模型理解事件并表达。按钮和自然语言进入同一个动作服务。一次触摸无需等待模型才有反馈，也无需每次都开一个模型回合。

## 已选技术栈

| 层 | 决定 | 用途与边界 |
| --- | --- | --- |
| 桌面外壳 | 沿用 Tauri 2 / Windows WebView2 | 新增 `scene` 窗口，复用应用生命周期和既有桥接 |
| 场景界面 | Vue 3 SFC + TypeScript strict | 菜单、对白、衣柜、背包、资源抽屉按功能拆组件 |
| UI 状态 | Pinia 3，按领域拆 store | 保存已确认状态的前端投影及 UI 状态，不持有另一份经济系统 |
| 构建 | 沿用当前 Vite 6，多入口；npm + 单一 package-lock | 增加 Vue 插件与独立 scene 入口，不迁移现有 JS 页面 |
| 二维舞台 | PixiJS 8，优先 WebGL | 背景、前景遮挡、立绘、光照、粒子与角色命中区域；首版静态差分图 |
| 界面样式 | CSS 变量设计令牌 + Vue scoped CSS | 自建少量界面原语；不引入重型后台组件库或全局样式重置 |
| 动画 | Pixi ticker + 小型时间轴；DOM 使用 CSS / Web Animations | 统一取消、暂停和 reduced-motion；不并行引入多套动画框架 |
| 音频 | 现有 TTS 服务 + 独立音频调度器，基于 HTMLAudio / Web Audio | 对白、BGM、环境音、短音效分通道，支持压低 BGM 与取消 |
| 后端 | 沿用 Python / FastAPI / Pydantic 与现有存储 | 新增 scene 领域模块及窄适配器；不在 Rust 重写模型与记忆 |
| 契约 | Pydantic 导出 JSON Schema，生成 TypeScript 类型，边界按 schema 校验 | 新契约有版本；前后端不分别手抄同一份字段定义 |
| 验证 | Vitest + Vue Test Utils；Playwright；现有 Python unittest / Rust 检查 | 纯调度逻辑、真实服务行为、浏览器视觉及 Tauri 集成分层验收 |

M0 解析与当前 Node/Vite 相容的确切依赖版本并锁定；不使用 `latest` 浮动版本，不因搭建场景顺带升级全仓工具链。`vue-tsc` 必须独立执行，Vite 构建通过不代表类型检查通过。

Live2D 后续通过渲染器接口接入，首版不安装未验证的 Live2D 桥接库。Pixi 并不自动保证美观；美术资源、画面层次、构图、文本可读性和演出节奏都有独立验收。

## 默认审美与交互

- 主题：「黄昏小屋」。暖米白、木色、柔和灰蓝，少量琥珀强调色；由昼夜背景扩展氛围。
- 场景和角色占画面主体，低对比界面与克制阴影。避免大面积高模糊玻璃和持续晃动。
- 默认 1280×800 可调整窗口；重点验收 1024×720、1280×800、1920×1080，以及 Windows 125%/150% 缩放。
- 底部紧凑对白区，支持收起、历史、点击推进与 AUTO；衣柜、背包、场景、活动通过一个可识别的侧边工具条打开抽屉。
- 场景物件热点是辅助入口，所有核心操作都有可见文字入口、键盘操作与焦点状态，用户无需猜点击位置。
- 首版头部、手部、肩部三个触摸区域；命中区域随角色位置/缩放一起变换。局部反馈先发生，模型回应按冷却与合并策略触发。
- 换装先可预览，确认后落地；衣柜、投喂卡片明确显示当前选择、数量、价格和实际结果。好感等详细数值放状态抽屉，主画面用自然反馈表达。
- UI 反馈目标 100ms 内可见；转场约 200–450ms。它们是待测目标，不是现有性能结论。
- 缺素材时明确显示缺失和修复入口。正式演示的两套服装必须有真实差分资源，不能靠改名称假装换装。

## 交付范围

**第一份可用成品（M0–M3）**：一个房间、一个角色、两套服装、三处触摸、至少一种食物的购买与投喂、分段表情和语音、点击/AUTO、背景与服装导入、切回桌宠后状态一致。使用现有好感/养成规则的已确认结果，不另造奖励台账。

**V1 完整体验（再完成 M4–M5）**：增加一个可导入的短情节包，具备固定演出、AI 节点、一次选择分支、暂停/恢复和真实可验证的存档。可视化剧情编辑器、多角色舞台、Live2D、插件市场与任意自由走位列为后续扩展，不阻塞第一份成品。

主要设计与工程边界见 [architecture.md](architecture.md)，按顺序执行 [execution_plan.md](execution_plan.md)。

## 依据

本地入口核对：`companion_v01/client_protocol.py`、`final_output_engine.py`、`routes/think.py`、`routes/desktop_pet.py`、`care_runtime.py`、`routes/satellite.py`；前端 `desktop_pet_next/vite.config.js`、`src/speech-delivery.js`、`src/visual-renderer.js`。这些是复用候选，不代表老链路已经满足新场景契约。

外部技术依据（2026-09-17 查阅）：[Vue SFC](https://vuejs.org/guide/scaling-up/sfc)、[Vue TypeScript](https://vuejs.org/guide/typescript/overview)、[Pinia](https://pinia.vuejs.org/introduction.html)、[Tauri 架构](https://v2.tauri.app/concept/architecture/)、[PixiJS Application](https://pixijs.com/8.x/guides/components/application)。

演出参考：[LingChat 固定研究版本 848fd34](https://github.com/SlimeBoyOwO/LingChat/tree/848fd3448c20ca32e0436a498d20e85f63393e7f)。借鉴事件队列和舞台分层思路，以本项目契约独立实现；其存档字段存在不作为断点恢复完整可用的依据。
