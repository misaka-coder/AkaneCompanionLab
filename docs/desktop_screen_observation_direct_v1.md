# 桌面连续观察 V1

`desktop_pet_next` 的屏幕观察只有一条链路：用户选择共享区域 → 本地采样窗口 → 当前 `/think` 的 `desktop_screen_frames` → 已配置的视觉模型执行正常角色回合 → 原气泡、表情、工具、TTS 链路。不会先生成视觉摘要再交给另一个模型转述；不采集系统音频。

## 控制中心

“系统与诊断 → 桌面观察与主动陪伴”提供独立设置：看屏幕、适度主动、采样间隔、观察时间窗、代表帧数、单帧最长边、组织方式、主动评估间隔、清空最近画面。默认采样 2 秒、窗口 30 秒、4 张代表帧、最长边 1280。主动观察仍由用户开启。

屏幕共享要求采集 WebView 中的用户点击授权。控制中心跨窗口开启若收到 `InvalidStateError`，会展开桌宠快捷菜单并提示用户点击“屏幕共享”：它直接在点击调用栈中执行同一个采集入口，不假装已开启，也不绕过系统选择器。参见 [getDisplayMedia 授权要求](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)。

- 时间拼图：按从左到右、从上到下显示相对时间标签，并附最新清晰帧。
- 多图：按时间顺序直接发送代表帧。两种方式共用原有最多 5 张图片的回合输入契约。
- 窗口内均匀取样保留首尾，不用画面差异阈值替模型决定什么值得关注。
- 采样和评估频率互相独立；更长时间窗不是更高请求频率。
- 模型每轮可返回正常 JSON 且 `speech: ""`，表示安静陪伴；普通用户提问不因空输出被当成主动静默。
- 静默不触发气泡、TTS、表情或音乐恢复动作；用户输入会中断正在运行的主动回合。

## 边界与失败

图片仅保存在客户端滚动内存缓冲，不建立后端摘要工作区。关闭共享、系统终止共享、清空、切角色/会话/实例范围时清除帧；打包期间范围或版本变化时丢弃旧结果。最近帧过期时跳过观察请求，不拿旧图冒充当前画面。拒绝授权后关闭采样，不循环请求授权；控制中心只有收到匹配操作的实际状态才确认成功。

原 `DesktopScreenVisionWorkspace`、摘要提示注入和屏幕专用摘要模型方法已删除。四个 `/desktop-pet/vision/{clip,latest,reaction,clear}` 路由是 **thin adapter**：统一返回 HTTP 410、`status: retired`、`reason: desktop_screen_summary_retired` 和替代输入 `think.desktop_screen_frames`，不再调用模型或保留工作区。旧 Electron 桌宠冻结，不回填新链路。

## 缓存契约

观察不增加 prompt profile、工具目录、能力开关或缓存分桶。画面时间、拼图说明和是否主动属于当前轮后部；系统提示、角色身份、稳定工具定义和已有历史保持原顺序。静默许可为宿主内部规范化标志，不注入稳定提示。

这保证可复用的前缀不因采样而变化，不保证供应商必然命中：模型/服务切换、缓存 TTL、最小缓存长度和真实历史追加仍由供应商决定。纯文字回合使用 CHAT、有真实图片时使用 VISION；不同模型之间不能共享 KV 缓存。

## 验证入口

- `python -m unittest tests.test_desktop_screen_vision tests.test_backend_route_modules tests.test_vision_service tests.test_instance_writer_shutdown`
- `desktop_pet_next` 中 `npm run smoke:screen-observation`：时间取样、拼图布局、隔离/过期、权限竞态、真实流处理和渲染函数的静默/气泡/TTS 调用、设置确认。
- 既有 `smoke:control-center-v2`、`smoke:control-center-actions`、`smoke:control-center-ux`、`build`。
- 手工验收需用户选择共享区域，然后观察视频/游戏实际前后变化与回复节奏。测试替身验证调用链，不等于已完成真实屏幕授权、远端视觉识别或可听 TTS 验收。
