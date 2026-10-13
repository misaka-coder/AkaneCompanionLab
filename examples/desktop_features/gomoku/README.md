# 完整桌宠交互参考：五子棋

这是已经接入桌宠的功能，参考直接指向生产代码。先读 [开发入口](../../../docs/DEVELOPMENT.md)，再按下表看需要修改的部分。

| 顺序 | 实现 | 负责什么 |
|---|---|---|
| 1 | [主窗口控制器](../../../desktop_pet_next/src/pet/gomoku.js) | 接收玩家操作，调用程序服务，用正常聊天唤醒模型，刷新盘面 |
| 2 | [插件](../../../plugins/akane_gomoku/src/akane_gomoku/__init__.py) | 程序服务与模型工具共享业务状态，使用真实 SDK 上下文 |
| 3 | [状态管理](../../../plugins/akane_gomoku/src/akane_gomoku/session.py) | 棋局归属、落子、版本、悔棋和重开 |
| 4 | [子窗口](../../../desktop_pet_next/src/gomoku.js)与[HTML](../../../desktop_pet_next/gomoku.html) | 用户操作与盘面显示 |
| 5 | [原生窗口](../../../desktop_pet_next/src-tauri/src/pet_windows/gomoku.rs) | 复用 docked 窗口，保持原有打开命令 |
| 6 | [应用组合入口](../../../desktop_pet_next/src/pet/application.js) | 初始化功能控制器，注册跨窗口事件，与正常聊天共用已有端口 |

业务规则可换成其他游戏；服务调用、正常聊天入口、scope 和原生窗口助手沿现有实现复用。

程序服务调用的最小写法（主窗口控制器内）：

```javascript
const services = createPluginServiceClient({ state, platform, scope, persistence, backend });
const binding = services.bind("akane.gomoku.service.board.v1.control");
const content = await services.invoke(binding, { action: "state", game_id: gameId });
// content 来自真实程序服务；错误包含 reason，切换客户端后旧结果会被拒绝。
```

用户落子保存成功后，控制器调用 `chat.sendMessage(message, { requireNewTurn: true, includeAttachments: false })`。
这是一轮正常聊天，当前游戏说明和盘面放在本轮消息中。模型用 `place_piece` 操作同一盘面，窗口显示实际状态。
回复正文中的坐标只是文字，不作为落子指令。棋局内存、重启后的行为和重试方式见 [功能说明](../../../docs/gomoku_companion_game_v1.md)。

## 跑起来

在设置中本地源码安装 `plugins/akane_gomoku` 并启用，启动桌宠，从菜单或面板打开五子棋。玩家执黑；点击一处，角色经正常工具链落白子。

修改代码后的更新与核验规范见 [从源码更新到运行实例指南](../../../docs/source_to_runtime_update_v1.md)。注意：

- 若修改了后端插件（`plugins/akane_gomoku`），必须重新走 Stage → Review → Install 生命周期，仅重启宿主不会加载新源码。
- 若修改了前端页面、控制器或原生 Rust，标准 Tauri 构建已通过 `beforeBuildCommand` 自动运行 Vite；生成 EXE 后还需部署到目标实例并更新桌宠进程。指南中的根启动器 `-Rebuild` 一并完成这条链路，不需要先手动构建一遍。

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -B scripts/check_desktop_feature.py gomoku
.\.venv\Scripts\python.exe -B -m unittest tests.test_plugin_gomoku tests.test_plugin_desktop_scope
cd desktop_pet_next
npm run test:gomoku
cd ..
```

只需离线构建核验时，使用下面的替代路径；它不部署到实例。需要更新运行桌宠时，直接按指南准备相同实例和环境，再用根启动器 `-Rebuild`。

```powershell
Push-Location .\desktop_pet_next
try {
    npm run tauri -- build --no-bundle
} finally {
    Pop-Location
}
```

需要检查运行目录和当前 Windows 桌宠时，在仓库根目录给诊断追加 `--backend-url <本机后端地址> --running`。
接线诊断检查控制器、跨窗口事件和打开命令的源码引用；不运行游戏，也不证明插件安装代码与当前源码相同。
active 和贡献项只证明运行目录声明已加载；安装 digest/激活结果用于核对插件部署，`--running` 只比较 EXE 哈希。
未传后端地址或 `--running` 时对应项是 SKIP。浏览器可用 `npm run dev` 后打开 `/gomoku.html` 检查布局，浏览器预览为本地人机，不能证明插件或模型接通。
真实验收：拖动与关闭；关闭重开续局；点击落子后工具确实落白子；悔棋/重开；模型慢响应时重复点击；切换角色后旧结果不覆盖新盘面。
JS 测试隔离了聊天/传输边界，Python 测试使用真实 SDK；模型参与的最终验收在原生桌宠进行。
