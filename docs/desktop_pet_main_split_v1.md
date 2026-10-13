# 桌宠 main.js 拆分与验收

2026-10-02，本次拆分对象是 `desktop_pet_next/src/main.js`。

原入口约 11,118 行；现在为 21 行，仅导入原生适配器、素材和样式，创建平台与应用并启动。`src/pet/application.js` 为 430 行，负责装配接口、初始化、桥接注册和关闭清理。业务代码放在 `src/pet/` 的 29 个模块中。

## 模块导航

| 模块 | 职责及主要状态 |
| --- | --- |
| `application.js` | 装配命名接口，确保初始化完整后启动；管理应用的 `start/dispose` |
| `platform.js` | 浏览器与 Tauri 的 HTTP、IPC、窗口能力适配 |
| `lifecycle.js` | 定时器、动画帧、DOM 监听和异步返回的原生取消监听函数 |
| `scope.js` | 实例、后端、Bot、用户、角色和会话的异步请求归属 |
| `state.js` | 设置规范化、角色状态规范化和原生持久化 |
| `character.js` | 角色选择、角色独立会话、服装、表情和资源加载 |
| `backend.js` | 实例身份校验、Bot 路由、健康检查、会话和 NDJSON 解析 |
| `chat.js` | 回复轮次、忙碌状态、流式请求、追加要求、停止和延迟记录 |
| `bubble.js` | 气泡展示、分段、去重、历史回复恢复和动作展示 |
| `tts.js` | TTS 预热、预取、音频队列与 speech units 播放 |
| `voice.js` | 语音控制器组装、设置桥接、模式选择及兼容接口 |
| `voice/recording.js`、`voice/single-turn.js` | 输入状态、普通录音/ASR、单轮实时语音 |
| `voice/call.js`、`voice/call-input.js`、`voice/call-response.js` | 持续通话连接、收音、响应、重连与收尾 |
| `voice/presentation.js` | 麦克风按钮、当前回复气泡与情绪反馈 |
| `music.js` | 音乐控制器装配、设置桥接与兼容接口 |
| `music/playback.js`、`music/workspace.js` | 本地队列/播放器、手边音频准备、目录与推荐 |
| `music/timeline.js`、`music/snapshot.js` | 歌词上传/分析/取消、音乐状态和上下文投影 |
| `music/controls.js`、`music/emotion.js` | 活跃媒体命令、音乐表情与恢复 |
| `system-media.js` | 系统媒体轮询、歌词查询和系统播放控制 |
| `perception.js` | 屏幕权限、画面缓冲、主动观察准备与调度 |
| `attachments.js` | 附件草稿、导入队列、剪贴板上传和文件交付 |
| `care.js` | 后端养成权威状态、商店操作和互动回复 |
| `window.js` | 拖动与物理、窗口尺寸、命中区域和桌面窗口操作 |
| `ui.js`、`input.js`、`interaction.js` | DOM 事件、输入历史、快捷键与本地互动 |
| `settings.js`、`panel.js`、`plugins.js`、`activity.js` | 设置命令及快照、面板桥接、插件事件和活动执行 |
| `status.js`、`webgl.js` | 连接/活动状态展示和 WebGL 探针 |
| `constants.js`、`profile.js`、`utils.js` | 常量、现有角色配置访问和无运行时状态的辅助函数 |

## 状态与接口约定

2026-10-03 继续拆分语音控制层：`voice.js` 从 1,841 行降至 156 行，主窗口检查范围扩展至 35 个模块。具体导航、修复和验收见 [桌宠语音控制器拆分记录](pet_voice_controller_split_v1.md)。

同日继续拆分音乐控制层：`music.js` 从 1,238 行降至 116 行，6 个内部模块最长为 394 行，主窗口检查范围为 41 个模块。具体请求归属、竞态修复和验收见 [桌宠音乐控制器拆分记录](pet_music_controller_split_v1.md)。

模块通过 `create…Controller` 创建；临时变量保留在对应工厂的闭包中。依赖通过 `character`、`chat`、`persistence` 等命名接口注入。领域模块不导入其他领域控制器，也不反向导入入口。现有协议与算法辅助模块继续复用。

持久设置 `state` 和后端资源契约 `resourceState` 仍共享。请求、录音、播放、缓冲和监听的临时状态分别归属各控制器；跨模块更新通过拥有者的方法完成。接口中的 getter 保留实时读取语义。

`application.js` 先创建接口引用并装配方法/getter，再调用 `boot`，解决原单体隐含的初始化顺序。`start` 返回同一个启动 Promise；关闭后的应用拒绝再次启动。`dispose` 幂等，释放定时器、DOM 事件、原生监听、录音、音频和渲染器；晚返回的原生监听也会立即取消。

配置中任一连接或角色归属字段变化都会推进请求 revision。异步结果提交前验证捕获的归属，包含 A → B → A 的情况。健康检查和角色资源刷新另有同一归属内的请求序号；后端自动选择默认 Bot 时，资源加载接续经过校验的新归属。

## 已处理的问题

- 删除始终为空的角色素材扫描分支，继续通过现有角色包桥接读取真实素材。
- 删除无效的旧窗口尺寸计算、未调用的旧入口方法和未读取的加载标记；减少窗口模块的无用依赖。
- 设置快照读取不再顺带重绘附件；最近播放历史由面板接口维护，保留去重与数量上限。
- 修复缺失的 `buildMusicRecommendationsSnapshot` 实现，以及屏幕共享提示中引用不存在的 `currentWindow`。
- 空立绘资源不再因读取不存在的表情对象 `.id` 而使停止/清理流程抛异常，仍显示资源缺失状态。
- 设置中的后端切换、资源刷新、系统媒体和手边音频操作返回实际失败；同类命令的旧完成结果不覆盖新结果。
- 分开文字轮次与语音的忙碌状态，避免语音结束清除正在进行的文字回复。
- 流式请求收到响应头后仍保留 AbortController，停止与关闭能取消浏览器的持续读取。
- 停止播放后，晚完成的 `play()` 或音频准备不再重新激活旧音轨。
- 慢会话、健康、资源、附件及歌词结果不再写回已经切换的上下文；关闭期间晚完成的持久状态加载也不再应用。
- 角色包不可用时，在修改当前角色状态前报错；角色选择取消旧录音和旧回复。
- 修复控制中心观察设置保持焦点时，角色/Bot/会话或能力变化被表单保护逻辑挡住的问题。
- 场景播放桥接提供取消监听与部分失败清理，纳入统一应用生命周期。

## 验证

在 `desktop_pet_next/` 中：

```bash
npm run check:pet-runtime
npm run smoke:pet-runtime
npm run smoke:pet-browser
npm run build
node scripts/visual-layout-browser-smoke.mjs
```

浏览器脚本使用 Playwright；可通过 `AKANE_PLAYWRIGHT_MODULE` 指定已有安装的模块。默认使用 Edge 的无头模式，测试 HTTP 响应仅来自隔离夹具，阻止访问用户的真实后端。

已通过：

- 29 个模块的接口成员、状态归属、导入边界、语法、未声明标识符和未使用绑定检查。这是针对 JavaScript 的静态检查，不等同于完整 TypeScript 类型检查。
- 完整控制器装配：在线/离线启动、默认 Bot、重复启动、关闭/重新创建、晚完成状态加载、流式去重与取消、纯快照、命令失败及竞态、健康结果倒序、会话归属、音乐停止、麦克风晚授权、忙碌状态和监听清理。
- 24 组相关 smoke：Bot 路由、养成、实例存储、speech delivery/units、气泡、观察、TTS、普通及实时语音、窗口物理、控制中心、立绘、附件、工作台和媒体控制。
- 73 项 Python 测试：`tests.test_desktop_pet_frontend_contract`、`tests.test_desktop_pet_bubble_delivery`、`tests.test_desktop_activity_runtime_contract`。
- 真实浏览器主入口：资源加载、双击输入、回复、失败提示、慢请求期间换会话、重新进入；0 个页面异常。浏览器无法打开原生面板，换会话通过保留的兼容控件调用同一生产处理函数。
- 立绘浏览器验证：18 个窗口/边缘/长文本场景、24 个桌面与预览几何比较、输入框尺寸与隐藏、10 种动作、减少动态效果、构建后入口的离线兜底；0 个页面异常。
- Vite build 与 `git diff --check`。

原先从 `main.js` 按文本偏移切函数的测试，改为按 AST 从当前模块定位声明。部分旧协议测试仍隔离执行函数体，替换 UI/传输依赖；`pet-runtime-smoke.mjs` 独立导入完整应用，验证真实装配和状态交接。Python 中遗留的名字/文本契约统一读取模块源代码；模块边界检查读取原始代码。

额外运行插件集成测试共 12 项，11 项通过、1 项失败：`tests.test_media_convert_plugin.MediaPluginInstallationTests.test_real_wheel_activation_conversion_scope_failure_and_uninstall` 中指定不存在的 FFmpeg 后，候选仍返回 `staged`，与预期 `ffmpeg_not_found` 不符。本次未修改该后端安装逻辑，单独记录该验收失败。

## 验收范围

尚未进行真实 Tauri/WebView2 桌面启动、实际音频播放、真实麦克风/屏幕授权和外部 LLM/ASR/TTS 服务验收。原生调用的装配、状态与清理通过适配器夹具验证；不据此宣称实际设备或外部服务可用。

本次保留旧 Electron 桌宠与现有后端/角色包路径契约。工作区原有的后端、技能文档和工具目录改动未修改；没有 stage、commit 或发布操作。
