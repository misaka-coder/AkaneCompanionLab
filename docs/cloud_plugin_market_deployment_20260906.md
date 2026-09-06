# 云端插件市场部署验收（2026-09-06）

## 结果与范围

- 共享 Host 已从 `e636331` 切换到 Akane `604b9eb`，配套 MemCore `42658a3`。
- personal 与 finance 的真实 QQ self-check 均为 `connected`；两个 Bot 均 online。
- personal 原有 `akane.test.hydration 0.1.0`、`akane.timer 0.2.0` 保持 active。
- finance 仍只有原有 `akane.finance 0.8.3`，保持 active；没有为 finance 安装新插件。
- Host 环境文件、QQ 配置、Bot 清单、实例绑定及受保护的 Bot 设置在切换前后哈希一致。personal 的插件选择是本次明确允许的变更。
- 最终服务于北京时间 23:16:24 启动；复核为 active/running，`NRestarts=0`。该计数不代表本次没有主动重启。

## 市场与实际安装状态

市场由已提交源码构建真实 wheel，云端本地发行目录包含 8 项。安装通过现有 bot-scoped 管理 API，校验市场摘要、实际权限后激活；没有直接伪造安装目录或跳过健康检查。

| 插件 | personal 最终状态 | 能力边界 |
|---|---|---|
| `akane.document-writer` | 已安装、active | Word/Excel/PDF/文本等；中文字体与渲染环境已补齐 |
| `akane.image-generation` | 已安装、active | 沿用 personal 图片连接；未执行付费图片请求 |
| `akane.media-convert` | 已安装、active | 使用云端现有 FFmpeg/FFprobe |
| `akane.voice-clean` | 已安装、active | basic 可用；未安装 AI 净化模型 |
| `akane.voice-dataset` | 已安装、active | 切片整理；可调用已安装的 basic 净化 |
| `akane.cover-song` | 已安装、停用 | 本地媒体连接不可达；库依赖健康不等于 RVC 服务可用 |
| `akane.file-transcription` | 未安装 | 暂存健康检查返回 `asr_runtime_incompatible` |
| `akane.audio-separation` | 未安装 | 暂存健康检查返回 `demucs_runtime_incompatible` |

没有在小容量 VPS 下载大型音频模型，也没有把不可用的功能标成可用。安装未修改用户原有工具审批策略。

## 发布依赖与修正

首次 Linux 回归发现旧 MemCore 不支持宿主已经使用的 `append_final` 参数，导致 11 项子代理收尾测试失败。按照 `tool_result_continuation_v1.md` 的既定发布要求，从 MemCore **已提交的** `42658a3` 构建配套包，没有带入其工作区的研究改动。

- MemCore wheel：`memcore-0.1.0-py3-none-any.whl`，SHA-256 `578215aa963fb1ebef2acf5b6e8f791f874e17bea89a046631923b4cbc07738c`。版本号相同不足以区分本次契约，必须同时核对提交和摘要。
- 使用新的 Host 虚拟环境安装该包，其他依赖只读复用原环境；没有覆盖旧环境中的 MemCore。
- 原 release 留下的 `PYTHONPATH` 指向共用 site-packages，会优先加载旧 MemCore，也会干扰文档依赖隔离。最终 release 配置显式清空该继承值；使用实际运行进程的解释器和环境复核，`append_final` 存在且来自候选环境。
- 文档使用独立环境：python-docx 1.2.0、openpyxl 3.1.5、ReportLab 4.5.1；最终运行环境下再次确认 ReportLab 为 4.5.1。
- 安装 `fonts-wqy-zenhei`，使用其现有 TrueType 集合字体；未复制 Windows 字体、未修改共享环境中的 ReportLab 5。

## 验证

- MemCore 提交版本：580 项测试通过。
- Akane Linux 扩大回归：563 项，558 通过、3 跳过、2 项既有失败；不是全绿。
- 两项既有失败为 `test_group_observation_and_current_addressee_remain_distinct_in_projection` 与 `test_open_turn_user_steer_projects_as_timestamped_user_message`。已在旧 `e636331` 和旧环境复现完全相同的断言及输出；没有修改断言、跳过用例或合入用户未提交测试改动。
- 最终有效运行环境再次隔离复验子代理、工具续推、文档插件：28 项通过。覆盖真实中文文档生成/读回、版本与样式、隔离 worker、取消和产物链；模型及渠道交付端口采用测试夹具。
- 控制中心生产动作桥 smoke 与 V2 smoke 通过；动作桥覆盖 78 个动作、28 个事件、9 个 invoke、13 条后端路由。
- 云端真实 API 验证市场 8 项、安装/启停结果、既有插件、双 Bot QQ 连接及受保护配置未变化。
- 最终进程日志未发现本次排查的 `unexpected keyword argument`、`memcore backend requested but unavailable`、`subagent_trace_complete_failed` 标记；这不替代一次完整真实对话验收。

## 备份、回退与剩余边界

- 保留部署前配置与插件状态，以及 13 份非空 SQLite 数据库备份。每份快照做了 SQLite `quick_check`、压缩读回和 SHA-256 校验，完整备份约 604 MB。
- 这是逐数据库一致的在线快照，不声称跨数据库原子快照；素材、缓存和工作文件没有修改。
- 备份助手最初未及时关闭 SQLite 连接，触发磁盘余量保护；修正关闭逻辑后重新完成完整备份。仅清理本次不完整备份及重复上传包，保留完整备份与旧 release，未清理用户数据或既有历史备份。
- 最终剩余磁盘约 2.1 GiB，仍偏紧；大型模型依赖不适合直接补装。
- 首次切换旧宿主退出超过 systemd 的 60 秒停止期限，由 systemd 终止旧进程。新版本已稳定运行；没有声称切换期间所有在途消息无损，也没有自动重复投递。
- 回退必须配套旧代码、旧 Python 环境和部署前 personal 插件选择/目录配置，不能只切回代码却保留新插件激活状态。数据库快照只在确有必要时恢复，避免覆盖切换后的新增数据。
- 本次没有更新桌宠安装包，没有做真实桌宠气泡/动作/TTS/系统播放或 QQ 文件投递验收；生图服务商请求、RVC/ASR/分轨的推理表现仍待单独验收。
- Akane 与 MemCore 两个仓库的用户未提交改动均未打包上线。
