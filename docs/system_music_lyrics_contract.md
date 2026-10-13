# 系统音乐歌词与桌宠时间线契约

在线歌词接口内部解析和缓存使用 `start/end/text`。桌宠现有的 `normalizeTimelineLyricSegments` 读取 `time_seconds` 或 `timeSeconds`。这两个接口的时间字段此前不一致，会把接口已成功返回的整首歌词过滤为空数组；模型因此只收到歌曲元数据。

`OnlineLyricsService` 在公开响应边界为每句补充 `time_seconds = start`，保留原有 `start/end/text`。新下载和已存在的成功缓存都经过同一适配；磁盘缓存保持原格式，查询失败仍返回原状态与空歌词。

既有桌宠可以直接消费适配后的接口。已经在前端内存中缓存了空数组的桌宠需要重新进入应用；仅重启后端不会清掉该内存缓存。此次运行时应用采用原桌宠可执行文件重启，不替换桌面程序。

验证包括：

- 新下载、旧缓存、匹配失败、提供方异常及用户隔离的 Python 检查。
- 真实 Python 服务响应 → 桌宠歌词标准化 → 音乐快照 → 真实聊天控制器请求体的跨语言检查，确认 `desktop_activity.lyric_current/lyric_next/lyric_line_count`。
- 既有音乐控制器与歌词路由检查，以及 Edge 浏览器中的实际标准化和音乐快照检查。
- 运行时成功缓存返回 `time_seconds`，桌宠和设备连接恢复，两套语音进程保持原实例。

自动检查使用自拟测试句，不调用真实模型、不生成语音、不向聊天发送测试消息。浏览器检查没有页面错误；实际模型的措辞仍需用户试听确认。

```powershell
python -m unittest tests.test_music_lyrics_contract
cd desktop_pet_next
node --test scripts/system-media-lyrics.test.mjs
```
