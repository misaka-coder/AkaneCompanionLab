# Akane 桌宠 V0.1

## 定位

桌宠是 Akane 的桌面陪伴客户端，是 Akane 在桌面上的一个轻量身体，不是新的决策大脑。它只负责呈现、输入、提醒和少量本地互动；长期记忆、工具调用、后台任务和真实决策仍然由 Akane 后端负责。

## 身份规则

- 桌宠共享 `profile_user_id=master`，因此长期记忆和 Akane 主体保持一致。
- 桌宠使用独立 `session_id`，默认形如 `desktop_pet_xxx`，用于把桌宠对话和 Web/QQ 会话区分开。
- `profile_user_id` 是“同一个 Akane”，`session_id` 是“不同身体/窗口的当前对话”。

## 当前能力

- 静态立绘：通过后端资源 URL 加载 Akane 立绘。
- 透明置顶：窗口背景透明，桌宠保持在桌面上层。
- 拖动：拖动立绘移动窗口，位置和大小会持久化。
- 双击输入：双击立绘打开输入框。
- 分段气泡：优先渲染 `speech_segments`，避免重复合成大气泡。
- 单击本地短句：单击立绘会显示本地短互动，不请求后端、不写入记忆。
- 托盘设置：支持显示/隐藏、重载立绘、设置后端地址、设置服装名、透明度和退出。
- 后台任务完成提醒：轮询只读任务状态，在完成、阻塞、部分完成时轻量冒泡提醒。

## 启动方式

先启动 Akane 后端：

```powershell
python launch_akane_memory_v01.py
```

再启动桌宠：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_akane_desktop_pet.ps1
```

Windows 上也可以双击：

```text
启动_Akane桌宠.bat
```

## 使用方式

- 拖动：按住立绘拖动到想放的位置。
- 单击：显示一句本地短互动气泡。
- 双击：打开输入框。
- Enter：发送输入框内容。
- Escape：隐藏输入框或关闭当前输入。
- 托盘菜单：右键系统托盘里的 Akane 图标，可显示/隐藏、重载立绘、修改后端地址、修改服装名、切换透明度或退出。

## 调试说明

- 后端默认地址是 `http://127.0.0.1:9999`。
- 如果桌宠提示后端未连接，先确认已经运行 `python launch_akane_memory_v01.py`。
- 如果缺少 Electron/npm 依赖，运行：

```powershell
cd desktop_pet
npm install
```

- `start_akane_desktop_pet.ps1` 会在 `desktop_pet/node_modules` 不存在时自动执行 `npm install`。
- 如果换了后端地址，可通过托盘菜单的“设置后端地址”修改。
- 如果换了服装名，可通过托盘菜单的“设置服装名”修改并重载立绘。

## 当前边界

- 暂不支持 Live2D。
- 暂不支持 TTS。
- 暂不支持桌面截图感知。
- 暂不支持文件拖放。
- 暂不自动发送后台任务产物。
- 桌宠提醒只提示“有事发生了”，最终确认、发送文件、继续处理仍由 Akane 正常对话/工具完成。

## 后续路线

- TTS：让桌宠能把正式回复读出来。
- 桌面感知：在明确授权后感知桌面上下文。
- `Live2DRenderer`：替换或并行静态立绘渲染器。
- 完整设置面板：替代当前托盘菜单和简单输入框。
