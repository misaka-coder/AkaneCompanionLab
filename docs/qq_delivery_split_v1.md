# QQ 投递模块拆分 v1

## 范围与调用链

`companion_v01/routes/qq.py` 从 5,692 行降到 5,083 行。本次抽出 23 个投递相关函数和类，路由保留命令分发、入站处理、队列和回合协调。

| 模块 | 职责 |
| --- | --- |
| `qq_stream_delivery.py` | 传输分段、按出现次数扣除已发送文字、跨模型阶段的精确前缀防重、即时发送与延迟合并 |
| `qq_voice_delivery.py` | 沿用现有 TTS 服务解析与合成、双语发声单元、有效输入指纹、音频缓存原子更新与裁剪 |
| `qq_reply_delivery.py` | 文字/语音/双发/自动模式选择、发送结果汇总、语音失败后的文字兜底、角色表情图片 |

路由直接调用上述模块。普通投递模块通过声音模块使用现有 TTS 服务，通过传入的网关发送 OneBot 消息；流式模块也使用当前回合的网关和上下文。实现模块不反向导入路由，不持有全局 Bot、角色、会话或配置选择。

现有 `routes.qq` 中的 23 个私有辅助名称保留为直接导入别名，原参数和返回注解保持一致。`_process_qq_turn_streaming` 仍在路由中，已有路由注入和插件运行时调用方式继续有效。需要替换合成器或解析器的测试现在补丁到 `qq_voice_delivery` 的实际实现位置。

## 本轮清理和错误处理

- 删除没有调用者的两套相似度函数及其对象/动作关键词常量；当前发送防重继续采用原有精确前缀和出现次数规则，不吞掉模型有意重复的文字。
- 删除语音函数内未使用的配置目录变量，并移除路由中不再使用的合成、分段和哈希导入。
- 音频缓存仍先写同目录临时文件，再替换目标。目录创建、部分写入和替换失败都返回 `voice_cache_write_failed`，不发布失败路径；写入及替换故障测试验证旧音频完整、临时文件清理。
- 未预期的合成异常返回 `voice_synthesis_failed`，避免异常文本中的内部路径或凭据进入投递结果。现有 TTS 服务返回的结构化原因码仍沿用原值。
- 保留原发送语义：语音模式失败后可通过文字兜底完成投递；双发模式的语音失败仍是部分失败，已发送文字不重复。

逐定义 AST 核对确认，除上述两个错误处理函数外，所有保留的原函数和类主体保持一致；新增边界测试检查直接别名及模块单向依赖。

## 验证与限制

检查覆盖：空回复、语音或服务不可用、双语发声、部分缓存写入、重复片段、失败发送重试、长文本降级、角色/声音变化、多 Bot 设置、群聊调用者权限、插件事件、完成通知批量投递和队列取消。

| 检查 | 结果 |
| --- | --- |
| 模块边界、语音投递、流式防重、投递身份、群聊身份与注意力、消息准入、引用来源、TTS 选择 | 115 项全部通过 |
| 后端路由、QQ 插件运行时、插件入站回合、完成通知批量投递、多 Bot、Channelcore 集成、QQ 命令 | 165 项全部通过 |
| 三类缓存 I/O 故障、未预期合成异常及模块归属定向检查 | 4 项通过，含缓存创建/部分写入/替换子场景 |
| 原函数签名、主体 AST 差异范围、23 个直接别名核对 | 通过 |
| Python 编译、`git diff --check` 及新增文件空白检查 | 通过 |

组合回归共 280 项通过。复现命令：

```bash
python -m unittest tests.test_qq_delivery_modules tests.test_qq_voice_delivery tests.test_qq_stream_replay tests.test_qq_delivery_identity_contract tests.test_qq_group_identity tests.test_qq_group_attention tests.test_qq_channelcore_admission tests.test_qq_forward_source_binding tests.test_tts_provider_selection
python -m unittest tests.test_backend_route_modules tests.test_qq_plugin_runtime_wiring tests.test_plugin_qq_inbound_turns tests.test_host_completion_batch tests.test_qq_multi_bot_dispatch tests.test_qq_channelcore_integration tests.test_qq_commands
```

QQ 的静态协议约束测试存在一项既有误判：它禁止网关文件任何位置出现动作名，把合法的动作路由比较也视为重复协议实现。测试现改为检查 AST，允许比较已选择的动作，继续禁止在网关构造协议动作和 `record` 段，并要求调用集中式消息与上传构造器。`qq_gateway.py` 的实现没有修改。

使用真实本地插件工作进程、受控回环 TTS 服务和模拟 OneBot 响应验证调用链；真实 NapCat、QQ 消息发送及外部语音服务未联调。没有进行性能基准。

上轮配置后端拆分和两项基线修复已单独提交为 `a920cd2d`。QQ 投递拆分作为后续目标的第一个切片提交；商店图片资源接入等用户已有改动保持独立。
