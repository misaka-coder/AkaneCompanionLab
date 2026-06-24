# Akane QQ / NapCat 接入 V1

本文档记录当前项目的 QQ 最小接入方式。V1 目标是先跑通“QQ 纯文本客户端”，验证 Akane 能离开 Web 场景端继续对话，而不是一次性搬旧项目里很重的 QQ 桥接器。

## 1. 当前方案

V1 使用 NapCat / OneBot 的 HTTP 事件上报：

```text
QQ / NapCat
  -> POST /api/qq/napcat/event
  -> AkaneMemoryEngine.process_turn(client_mode=qq_text)
  -> OneBot HTTP send_private_msg / send_group_msg
```

暂不启用旧项目的 WebSocket bridge 进程。旧项目的 `qq_bridge.py` 仍然很有参考价值，但里面包含任务执行、进度推送、插件运行、主动提醒等大量逻辑，直接搬会污染当前架构。

## 2. 环境变量

在 `.env` 中开启：

```env
QQ_BRIDGE_ENABLED=true
QQ_ONEBOT_HTTP_URL=http://127.0.0.1:3001
QQ_BOT_QQ=你的机器人QQ号
MASTER_QQ=你的主人QQ号
QQ_CHARACTER_PACK_ID=
QQ_GROUP_PLAINTEXT_ENABLED=false
QQ_GROUP_FOLLOW_TTL_SECONDS=180
QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS=180
QQ_ATTACHMENT_DEBOUNCE_SECONDS=1.2
QQ_ATTACHMENT_READY_WAIT_SECONDS=8
QQ_REPLY_SEGMENT_DELAY_SECONDS=0.8
QQ_EVENT_MAX_AGE_SECONDS=300
QQ_ALLOW_STALE_EVENTS=false
QQ_REQUIRE_FILE_DELIVERY_INTENT=true
```

字段说明：

- `QQ_BRIDGE_ENABLED`：是否开启 QQ 事件入口。默认关闭，避免公网误触发。
- `QQ_ONEBOT_HTTP_URL`：NapCat OneBot HTTP 地址。
- `QQ_BOT_QQ`：机器人 QQ，用于识别群聊里是否被 at。
- `MASTER_QQ`：主创 QQ。该 QQ 的私聊会映射到 `master` 记忆身份。
- `QQ_CHARACTER_PACK_ID`：QQ 文字聊天默认使用的 Creator Kit 角色包 id。留空时使用内置 Akane 人设；例如设为 `reimu` 后，QQ 每轮会把 `character_pack_id=reimu` 传给后端，角色包 persona 会进入 `qq_text` prompt，聊天记忆也会按该角色包隔离。
- `QQ_GROUP_PLAINTEXT_ENABLED`：是否允许群聊不 at 也回复。默认关闭。
- `QQ_GROUP_FOLLOW_TTL_SECONDS`：旧配置名，仍可作为附件缓冲窗口的兜底 TTL。
- `QQ_GROUP_ATTACHMENT_BUFFER_TTL_SECONDS`：群聊被 at 后，允许同一用户补发图片/文件的时间窗口。普通文字不受这个窗口影响。
- `QQ_ATTACHMENT_DEBOUNCE_SECONDS`：QQ 连发图片/文件时的短防抖窗口。窗口内较早事件只入库不触发回复，最后一个事件统一唤醒 Akane，默认 `1.2` 秒。
- `QQ_ATTACHMENT_READY_WAIT_SECONDS`：附件入库后，主回复最多等待视觉观察 / 文件解析完成的秒数，默认 `8` 秒。超时后仍会回复，但 Prompt 会显示仍有附件在处理中。
- `QQ_REPLY_SEGMENT_DELAY_SECONDS`：`speech_segments` 分多条发到 QQ 时，每条之间的象征性停顿秒数，默认 `0.8`，最大 `3.0`。
- `QQ_EVENT_MAX_AGE_SECONDS`：忽略超过该秒数的旧 QQ 事件，避免 NapCat / OneBot 重连后把历史消息重新灌进当前对话。设为 `0` 可关闭时间拦截。
- `QQ_ALLOW_STALE_EVENTS`：是否允许处理旧事件，默认 `false`。只建议临时排查回放事件时打开。
- `QQ_REQUIRE_FILE_DELIVERY_INTENT`：QQ 文件发送保护，默认 `true`。开启后，当前消息没有明确的文件/结果发送意图时，后端不会真正调用 OneBot 上传文件。

## 3. NapCat 配置

NapCat 需要满足两件事：

- OneBot HTTP API 可用，例如 `http://127.0.0.1:3001/send_private_msg`。
- 事件上报地址指向当前后端：

```text
http://127.0.0.1:9999/api/qq/napcat/event
```

可先访问状态检查：

```text
GET http://127.0.0.1:9999/api/qq/napcat/status
```

## 4. 行为规则

私聊：

- 机器人会回复所有私聊消息。
- 如果发送者是 `MASTER_QQ`，会话身份是 `master`。
- 其他 QQ 私聊会话身份是 `qq_pri_{user_id}`，长期身份是 `qq_{user_id}`。

群聊：

- 默认只有 at 机器人时才回复。
- 不启用 follow 窗口；@ 之后的普通群消息仍然不会自动触发回复。
- @ 之后会为同一个发送者打开一个短暂的“附件缓冲窗口”；在窗口内补发的图片/文件可以不再次 @，用于适配手机端不能边 @ 边发图的限制。
- 附件缓冲窗口只接收图片/文件/音频等附件消息，不接收普通文字消息，也不接收其他群成员的附件。
- 连发附件会经过短防抖合并：每条附件消息都会立即登记和解析，但窗口内较早消息不会单独触发 Akane 回复，最后一条消息负责等待附件观察结果并统一回复。
- 附件上下文排序按 `sequence_no`，即用户发来的原始顺序；视觉模型或文件解析谁先完成，不会改变 Akane 看到的“第 1 张 / 第 2 张”顺序。
- 群聊会话身份是 `qq_group_shared_{group_id}`。
- 同一个群里的成员共享该群的群聊记忆；发送者 QQ 和昵称会作为本轮上下文注入，让 Akane 知道是谁在说话。
- 群聊记忆不直接写入 `master` 私聊身份，避免公共群聊污染主人的私有长期记忆。

## 5. `qq_text` 模式

QQ 入口会把消息转成：

```json
{
  "client_mode": "qq_text",
  "client_capabilities": ["speech_segments", "file_drop", "choices", "tool_actions"],
  "character_pack_id": "可选；来自 QQ_CHARACTER_PACK_ID"
}
```

`qq_text` 模式不会要求模型输出 `scene`、`character`、`bgm`、`live2d`、`pet` 等演出字段。后端 `QQTextOutputAdapter` 也会剥掉这些字段，保证 QQ 侧只拿纯文本回复。

如果配置了 `QQ_CHARACTER_PACK_ID`，后端只注入该角色包的身份、称呼、说话风格、边界和 persona 参考；不会把桌宠服装、立绘、场景或 BGM 渲染规则带到 QQ prompt。

### QQ 表情包投递

QQ 侧的表情反馈由模型输出的 `emotion` 驱动，不走图片工具调用。默认会从当前 QQ 会话的角色包里找到对应 emotion 图片，并通过 NapCat / OneBot `image` 消息段发出去；这保证表情图片来自当前 `character_pack_id`，不会跨角色包共用。

如果需要 QQ 原生 / 商城表情包，可额外配置 NapCat / OneBot 的 `mface` 消息段。`mface` 不能直接由本地 PNG 生成，必须使用 QQ 已知表情包的 `emoji_package_id / emoji_id / key / summary`。当 `mface` 命中时优先发 `mface`；没有命中时回退到当前角色包的本地 emotion 图片。

角色包配置示例：

```json
{
  "qq_delivery": {
    "emotion_images": {
      "enabled": true,
      "min_interval_seconds": 20
    },
    "emotion_mfaces": {
      "enabled": true,
      "min_interval_seconds": 20,
      "map": {
        "happy": {
          "emoji_package_id": 123,
          "emoji_id": "abc",
          "key": "napcat-market-face-key",
          "summary": "开心"
        }
      }
    }
  }
}
```

`emoji_package_id`、`emoji_id`、`key`、`summary` 可从 NapCat 收到的商城表情 / 表情包事件中抓取。NapCat 有时会把收到的商城表情映射成 `image` 段，但只要 `data` 中带有这些字段，就可以复制到角色包配置里。缺少映射、配置关闭、或同一会话在 `min_interval_seconds` 内重复发送同一个表情时，后端会结构化跳过，不会假装发送成功。

`map` 会按同一个角色包的 `emotion_aliases` 双向展开。例如 `emotion_aliases` 里有 `"happy": ["开心", "卖萌"]` 时，`map.happy` 可以同时匹配模型输出的 `happy`、`开心` 和 `卖萌`；反过来只配置 `map.开心` 也可以匹配 `happy`。

主人可以在 QQ 里发送 `表情包配置 happy` 并同时带上要抓取的表情包。Akane 会从该消息里的 NapCat `mface` 段，或带有 `emoji_package_id / emoji_id / key / summary` 的 `image` 段中提取字段，并回复一段可粘进当前角色包 `character.json` 的 `qq_delivery` 配置片段。该命令只允许 `MASTER_QQ` 使用。

### QQ 角色切换指令

QQ 支持会话级角色切换；私聊和每个群聊各自保存当前角色包，重启后回到 `.env` 中的 `QQ_CHARACTER_PACK_ID` 默认值。

- `角色列表`：列出当前已安装的 Creator Kit 角色包。
- `当前角色`：查看当前 QQ 会话正在使用的角色。
- `切换角色 reimu` / `使用角色 reimu`：把当前 QQ 会话切到指定角色包。
- `切回默认角色`：清除当前会话临时切换，恢复 `QQ_CHARACTER_PACK_ID`。
- `切回Akane`：当前会话强制使用内置 Akane 人设，不绑定角色包。

切换成功后，后续 QQ 消息会继续带对应 `character_pack_id`，聊天记忆也按该角色包隔离。

## 6. QQ 附件

- QQ 图片 / 文件默认进入临时附件上下文，不默认进入礼物系统。设计见 `docs/attachment_focus_inbox_v1.md`。
- 图片优先通过 NapCat / OneBot 的 `get_image` 读取本地缓存；如果失败才尝试直连临时 URL。
- 文件会优先保留原始文件名；可解析文件会进入临时附件工作台。新的一批附件默认 Auto-Focus，未展开附件只在 Manifest 中显示结构化识别信息，不展示半截正文。
- 如果用户要求“整理成文件发我”，Akane 可使用 `compose_file` 生成 `gen_001` 这类生成文件。
- 如果用户只是要求 TXT/Markdown 等原始附件或 `gen_001` 这类已生成文件忠实转成 PDF/Word，Akane 应使用 `compose_file` 但留空 `content_markdown/table_rows`，让后端从来源读取正文；最终文件不会包含 `任务/来源摘录/用途` 这类工作台元信息。
- 如果用户继续要求“把刚才那份改一下”，Akane 可使用 `revise_generated_file` 生成 `gen_002`，旧文件不会被覆盖。
- 如果用户要求“再发一次刚才的文件”或“把原附件也发我”，Akane 可使用 `send_file` 发送已有 `file_001/img_001/audio_001` 或 `gen_001/gen_002`，不重新生成内容。
- 如果用户直接给了公开视频/音频链接，Akane 可使用 `fetch_media_from_url` 先把素材下载进临时附件工作台；下载成功后，它就会像普通 `file_001/audio_001` 一样继续参与转写、转码、发送和后续协作。
- 如果用户要求“标红、加粗、黄色高亮、某列/某行上色”，Akane 可在 `compose_file` 或 `revise_generated_file` 里使用声明式 `formatting`；QQ 端只负责发送生成后的文件。
- 如果用户只要求给已有 `docx/xlsx` 文件套样式，Akane 应优先使用 `apply_style_to_existing_file`，这样不需要把大文件全文或大表格重新输出一遍。
- QQ 收到普通音频/视频附件时，会尽量用 `ffprobe` 自动写入轻量媒体卡，包含时长、音频编码、采样率、声道、视频分辨率和帧率等低成本信息；这些信息可以直接进入附件 Focus/Manifest，不需要 Akane 额外调用工具才知道基础规格。
- 如果用户要求复查规格、查看生成物媒体信息，或转换前需要更确定的参数，Akane 可使用 `inspect_media_info`。它只读取信息，不生成文件。
- 如果用户要求普通音频转码、压缩或从 `mp4/mov/mkv/webm` 等视频提取音频，Akane 可使用 `convert_media_file`，输出格式支持 `mp3/wav/flac/m4a/aac/ogg/opus`。它也支持单文件截取片段、响度标准化、整体音量增减、淡入淡出和调速。采样率、声道、码率以及这些精修参数都是可选项，用户没指定时不需要硬填；`normalize_volume` 用于“调正常/更舒服”，`volume_gain_db` 用于“放大一点/压低一点”；`kgm/ncm/qmc` 等平台加密或专有缓存格式只做识别和说明，不做解密转换。
- 如果用户要求清理 Akane 生成过的 `gen_001/gen_002`，Akane 可使用 `manage_generated_file`；临时附件 `file_001/img_001` 仍使用 `clear_attachment_focus`。
- 如果用户要求看长文件里的某页、某几行、某个 sheet，Akane 可使用 `read_attachment_section`；本地原始附件还在时会优先重新读取原文件，读不到时才回退到解析预览。
- QQ 端会根据 `generated_file_ready` 事件尝试调用 NapCat / OneBot 上传文件；若上传失败，生成文件仍保留在本地 `GeneratedFileStore`。

## 7. 当前边界

- 群聊全量免 at 回复默认关闭，避免打扰和刷屏。
- 旧项目的 WebSocket bridge、进度消息、任务执行器以后可以按模块逐步迁移，不建议一次性照搬。
