# 桌宠五子棋

完整源码阅读顺序与复用方法见 [交互开发参考](../examples/desktop_features/gomoku/README.md)。

棋盘窗口复用桌宠的聊天入口、角色和记忆。棋盘数据由插件维护，界面显示插件返回的盘面；回复中提到坐标不会直接触发落子。

## 使用

从桌宠面板或菜单打开五子棋。支持玩家执黑（先手）或玩家执白（伴侣执黑先手）：

- **宿命对决**：
  - 玩家执黑时，玩家点击落子后，自动唤醒当前角色，模型使用五子棋工具落白子。
  - 玩家执白时，开局或重开时自动唤醒当前角色先手落黑子（建议天元起手），随后由玩家落白子。
  - 棋盘随实际结果更新，准星与点击保护限制仅在玩家回合生效。
- **军师参谋**：玩家与本地人机对弈；点击“求助伴侣”把当前盘面送到正常聊天，角色针对玩家持方提供走法建议。玩家选白棋时本地 AI 自动落第一步黑子。
- 悔棋撤回上一轮；对方尚未落子时只撤回玩家刚才这手。重开清空棋盘。
- 关闭窗口保留本次桌宠进程中的棋局，重新打开可以接着下。窗口标题可拖动。
- 模型未落子或聊天失败时显示原因，并提供重试。正在处理别的对话时不会把落子误当作对话追加指令。
- 切换角色、会话或 Bot 时打开独立棋局。棋盘暂存在内存中，后端或桌宠重新启动后开始新棋局。

“羁绊共斗”尚未实现，当前不显示该选项。

## 实际调用链

1. 棋盘通过 Tauri 定向事件向主窗口请求操作。
2. 主窗口使用既有管理桥接访问插件程序服务 akane.gomoku.service.board.v1.control。管理接口绑定当前用户、客户端会话和角色，与聊天工具的实际调用身份一致；聊天记忆仍按已有统一记忆策略入库。
3. 插件验证坐标、轮次和盘面版本，保存落子，返回完整界面快照和供模型阅读的 ASCII 盘面。
4. 主窗口通过原聊天控制器发起一轮对弈，保留正常工具调用、语音、表情及 MemCore 处理。
5. 模型调用 akane.gomoku.place_piece 落自己的棋子（根据持方落黑子或白子）。主窗口在这一轮期间读取状态，在变化时更新棋盘；最终回复后再确认一次结果。

玩家操作服务是程序接口，不加入模型工具目录；模型可用工具为 get_game_state、analyze_board、suggest_moves 和 place_piece。模型落子只操作自己的棋子；参谋模式提供建议，不替玩家下棋。不增加全局游戏提示词，对弈说明和当前盘面仅放在本轮消息中。

## 状态处理

每盘棋由用户、会话、角色与对局 ID 隔离。插件内的落子、悔棋、重开使用单局锁，版本随改变递增。旧落子版本无法覆盖新盘面；前端不接受倒退的快照。重开、悔棋后仍在进行的模型请求不会再获得玩家新一手，直到原请求结束。切换会话后旧请求的界面更新会被丢弃。

## 验证命令

~~~powershell
python -m unittest tests.test_plugin_gomoku tests.test_plugin_desktop_scope tests.test_plugin_host
$env:PYTHONPATH = "$pwd/plugins/akane_gomoku/src"
python -m unittest discover -s plugins/akane_gomoku/tests
cd desktop_pet_next
node --test scripts/gomoku.test.mjs scripts/gomoku-bridge.test.mjs
npm run smoke:pet-browser
npm run tauri -- build --no-bundle
~~~

原生发布必须用 Tauri 构建命令，把前端嵌入 EXE。仅运行 Cargo 编译不能保证发布版带有页面资源。

接线、运行插件和 EXE 版本的只读诊断：`python scripts/check_desktop_feature.py gomoku`。
加 `--backend-url` 检查指定后端的插件目录，`--running` 比对 Windows 当前桌宠 EXE；原生行为与模型参与仍按上面的实际链路验收。
