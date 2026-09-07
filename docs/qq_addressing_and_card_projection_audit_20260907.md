# QQ 接收对象与卡片投影审查

日期：2026-09-07。范围：本地文档插件市场补齐与安装；只读审查云端/本地 MemCore V6、群聊唤醒与消息投影。没有修改唤醒词、角色、原始历史或协议解析实现。

## 已完成：本地文档插件

- 原本 `plugins/market.toml` 已包含文档插件，但实际 `.plugin-market/index.json` 停留在 9 月 6 日 17:24 的六插件版本，早于文档插件源码清单提交。重启不会重建被 Git 忽略的发行产物。
- 使用既有 `scripts/build_plugin_market.py` 重建真实 wheel 和原子索引，市场恢复到当前八个条目；只安装 `akane.document-writer`，不批量启用其他插件。
- 通过现有管理员鉴权、市场 hash 校验、暂存检查和明确权限安装链完成安装；没有绕开身份检查或直接篡改插件状态文件。
- 当前运行目录确认 `enabled=true / runtime_status=active / pending_activation=false`，创建、修订、样式三项能力均贡献到 desktop/QQ；无需重启后端。
- 权限限于 `artifact.write`、`capability.prompt.invoke`、`resource.read`。没有安装 Python/系统依赖，没有安装 LibreOffice。
- `tests.test_document_writer_plugin` 与 `tests.test_plugin_market` 合计 10 项通过。包含真实安装、多格式生成、修订/样式、任务取消、重复请求、停启和交付端口测试；未替用户向真实 QQ 群发送测试文件。

## V6 状态

本地当前 venv 与云端当前服务解释器分别执行 `scripts/check_memcore_runtime_contract.py`，均返回投影版本 6，六个作者保真用例通过。不是仅检查版本常量。

另对本地 87 群真实 Timeline 条目，按当前 V6 `ProjectionAdapter.context_surface_payload` 回放：作者稳定 ID、多个关系字段及引用作者仍能保留。V6 会忠实呈现上游给出的错误 target，不能替上游恢复已丢失的卡片内容。

## 确认的问题

### 1. 唤醒条件和消息接收对象被混为一谈

- 本地实例兼容适配器 `bot_config_from_instance_context` 固定给出 `wake_words=("Akane",)`；QQ 群角色实际覆盖为 `cecilia`。切角色不自动改变这条默认唤醒配置。
- `channelcore-onebot` 正文唤醒匹配不包含结构化 @ 的标签，但会匹配正文中出现的 Akane。
- 实际 23:08 消息（本地 seq 2734）@ 的是云端 Akane，正文包含“已关闭 Akane 进程”。本地入站记录为 `group_wake_word`、`explicit_assistant_mention=false`，但 `primary_target=assistant`。
- 23:10 消息（seq 2746）实际 @misaka，正文提到 akane；当前 V6 回放同时呈现 `target: assistant` 与 `mentions: misaka`。这不是 V6 丢掉了 @，而是上游把词语命中当作发给本 Bot 的证据。
- 对照：同一时间只 @ 云端 Akane、正文没有唤醒词的消息，在本地是 observed；离线解析也确认 `at_other_no_wake=group_passive`。因此不能概括为“所有 @Akane 都会触发两只 Bot”。

### 2. 本 Bot 的历史 @ 显示名固定为 Akane

`QQMessageContext.to_turn_payload()` 调用有序消息渲染器时传入 `bot_label="Akane"`。
实际本地环境检查消息（seq 2687）@ 的是本地 Bot，稳定对象是 assistant，但正文历史写成 `@Akane`；当前角色为 Cecilia。这与正确的引用作者“塞西莉亚”并存，会产生输入命名矛盾。

这两项是已确认的身份理解干扰源；没有具体错误回复样本时，不能断言它们解释了所有“归属不清”，也不能据此修改长期记忆或主人关系。

### 3. JSON 小程序与音乐卡片没有语义投影

使用当前安装的解析器，以 B 站小程序常见 `json` 段和 `music` 段作离线用例：

- 两者均归为 `UnknownPart`，`display_fragment` 为空，没有标题/链接/音乐信息摘要。
- 同会话引用查询成功后，结果仍可为 `no_attachments`、空 `text`、零附件。
- 原始片段保留在私有消息链，不代表内容已经进入模型视图；问题发生在 OneBot 消息规范化到宿主投影之间，不是加大 MemCore 上下文能修复。

这里证明的是当前解析器对这些段形状的缺口，不声称已经复现用户每一张历史卡片。JSON/XML 可能有多种供应商结构，后续应使用真实、脱敏样本按类型扩展，不能把任意嵌套 JSON 直接当正文或可信指令。

### 4. 以文件发送的图片被归为文档

- `file` 段先成为 `AttachmentRef(kind="file")`，宿主 `_legacy_attachments` 一律映射为 `document`。
- QQ 摄取保留该 kind；即使下载后 MIME 为 image/png，也不转入 `kind==image` 的视觉分支。
- 对 `.png` 文件卡的离线调用得到“只保留文件名和基本信息，暂未解析内容”。因此能拿到文件与能读取图片内容是两回事。
- 另一个离线用例中，`file_id` 字段没有被当前段定位符读取逻辑保留，而回落到文件名；这可能让某些文件连下载定位也失败，需要真实 NapCat 文件段样本确认覆盖范围。

## 建议的后续修复切片

1. 先修接收对象：保留真实 @ /引用对象，区分唤醒与 addressed-to；本机与云端 Bot 的唤醒词配置明确绑定，消除固定自称显示名。覆盖提到第三人、多 @、引用、多人交错，不靠模型猜身份。
2. 再修消息内容：在 channelcore 单一解析实现增加有界、只读卡片语义；宿主消费统一结果，不再加第二套 QQ 卡片解析器。
3. 文件图片按可信 MIME/文件字节验证后进入既有图片处理链，同时保留原来源及文件定位符。覆盖直发、引用与转发，不把扩展名当作唯一判断依据。

本轮仅完成安装和审查。上述解析/接收对象修复、云端发布、历史迁移均未实施；不以“V6 门禁通过”或“源码有字段”冒充这些问题已修复。
