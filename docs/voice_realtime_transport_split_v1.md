# 实时语音传输拆分记录

## 范围与边界

原 `companion_v01/voice_runtime/realtime_transport.py` 为 2,361 行，含单轮实时语音、持续通话、协议解析、音频输入、播放回执和清理逻辑。分片迁移到固定职责模块，保留旧导入路径、路由、类名及方法签名。

本片只处理语音传输路径。工作区同时实施的能力目录差量、MCP 发现及契约绑定执行单不纳入此次改动。现有服务端认证、角色归属、主人共享会话与工具目录作用域保持原样。

## 第一片：协议与解析

- `voice_runtime/realtime_protocol.py` 提供请求/工厂类型、协议常量、打开请求和开始新轮请求的解析。
- 原单轮/通话两处音频 sequence 与时钟校验收敛为 `parse_audio_metadata`，继续拒绝布尔值、非整数和负数。会话状态及轮次匹配仍由各会话入口检查。
- 生产服务和包级惰性导出直接引用协议模块，导入请求类型不初始化传输会话或生产客户端；旧入口导出同一类型对象。

迁移前 11 个相关模块共 111 项检查通过；第一片相关的 5 个模块共 56 项通过。AST 对照保留原有 63 个函数/方法的参数、默认值与返回类型，除两处重复校验收敛外，原函数体保持一致。新增冷导入检查，验证类型导出身份与传输依赖边界。

## 第二片：单轮输入与播放回执

- `realtime_turn.py` 保存单轮会话状态、接收循环、打开和失败回执；初始化仍集中在会话类中。
- `realtime_turn_input.py` 处理音频输入、端点、收尾超时、打断和取消；`realtime_turn_delivery.py` 处理播放通知、回执及响应完成。
- `realtime_events.py` 收敛两种会话相同的播放控制和音频头字段。发送顺序继续是 JSON 头、音频字节、实际发送确认。
- 包级单轮会话导出直接引用实现模块，原传输入口仍导出同一类。

回归测试先复现、再修正两处问题：单轮响应取消支持异步回调，等待实际结果后才发送取消成功；同步或异步取消异常转为失败回执。播放通知等待任务抛异常或被取消时读取任务结果、发送终止失败并清理通道，避免反复重新等待和未读取的任务异常。

6 个相关测试模块共 60 项通过。AST 对照继续保留原有 63 个函数/方法签名，函数体差异仅包含第一片的校验收敛、本片的两个故障修复和播放字段收敛。收尾超时测试的注入位置随实现迁移到 `realtime_turn_input`；断言不变。

## 第三片：持续通话与兼容入口

- `realtime_call.py` 保存整通电话的状态、接收循环和新轮生命周期。原输入协调器、待接收音频及播放归属字典仍由同一个实例维护。
- `realtime_call_input.py` 处理当前轮音频、端点、收尾、打断和指定响应取消；`realtime_call_delivery.py` 按服务端记录的归属处理播放通知和回执；`realtime_call_lifecycle.py` 负责失败、清理、指标。
- `realtime_dispatch.py` 按已接收的首条消息选择单轮或持续通话。认证和服务端签发的身份信息仍由既有路由处理。
- `realtime_transport.py` 只保留兼容导出；包级会话和分发导出直接指向实现。实现模块没有反向依赖兼容入口。

持续通话的播放等待也复现了遗漏读取任务结果的问题，已修正：异常或子任务取消产生明确的终止失败，清理所有播放等待、当前输入和提供方会话，不再重新挂起等待。外层取消仍在资源清理后传出 `CancelledError`。终止后不继续处理同批完成的其他播放任务。

新增行为检查覆盖失败/取消的播放等待、外层取消后的资源回收，以及旧播放回执带有新轮 ID 时仍按实际 delivery 归属完成旧轮、保留新输入和待接收音频。继续由既有路由和生产集成检查验证重复端点、跨轮播放、真实发送确认、打断、断线、慢 TTS、迟到音频和持久化恢复。

## 最终模块导航

| 文件（位于 `companion_v01/voice_runtime/`） | 职责 | 行数 |
| --- | --- | ---: |
| `realtime_transport.py` | 原路径兼容导出 | 28 |
| `realtime_dispatch.py` | 首条消息分发 | 50 |
| `realtime_protocol.py` | 协议、请求类型和解析 | 283 |
| `realtime_events.py` | 共享播放消息字段 | 52 |
| `realtime_turn.py` | 单轮状态和接收循环 | 402 |
| `realtime_turn_input.py` | 单轮音频、收尾、取消 | 282 |
| `realtime_turn_delivery.py` | 单轮播放与响应完成 | 241 |
| `realtime_call.py` | 通话状态与新轮生命周期 | 437 |
| `realtime_call_input.py` | 通话输入、收尾、打断 | 314 |
| `realtime_call_delivery.py` | 跨轮播放路由与响应完成 | 283 |
| `realtime_call_lifecycle.py` | 通话失败、清理、指标 | 185 |

## 验证与验收

完成后 AST 对照仍保留原有 63 个函数/方法的参数、默认值和返回类型。新增冷导入、导出身份和依赖方向检查，避免兼容入口重新长成实现单体。

完整语音回归覆盖 13 个测试模块，共 121 项通过；包含迁移前的 111 项及新增的 10 项。相关文件 `py_compile`、Ruff 未定义/未使用名称检查（`--select F`）和 `git diff --check` 均通过。

回归模块：`test_voice_realtime_route`、`test_voice_realtime_call_route`、`test_voice_runtime_production`、`test_voice_runtime_asr_realtime_turn`、`test_voice_runtime_imports`、`test_voice_playback_context`、`test_voice_runtime_host`、`test_voice_runtime_durable_ports`、`test_voice_runtime_launcher`、`test_voice_runtime_asr_bridge`、`test_voice_runtime_tts_executor`、`test_voice_realtime_boundaries`、`test_voice_realtime_transport_contract`。

未启动实际麦克风、桌宠播放器、ASR/TTS 外部服务。测试使用受控 WebSocket、音频会话和播放通道，真实音频体验仍需现场验收。
