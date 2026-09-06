# 剩余可选业务能力插件化 V2

日期：2026-09-06。起点：`87c1c6f`。状态：审计与执行清单已建立；分轨独立工作进程实施中，尚未替换业务入口。

目标是持续完成七项已识别能力，不以一个插件完成代替整项目标完成。已完成的媒体转换及其资源、产物、市场、Job 基础见 [V1 验收](optional_media_plugin_migration_v1.md)。每项验证、清除旧实现并聚焦提交后继续下一项。

## 审计结论与顺序

下表的“边界”是实现前审计结论，不代表能力已发布。最终包数量由共享依赖与生命周期决定，不强制一工具一包。

| 顺序 | 能力 | 当前权威与调用者 | 迁移边界 / 必须保留的行为 | 状态 |
|---|---|---|---|---|
| 1 | 分轨 | `generated_files_media.separate_audio_stems`、`GeneratedFileService`、工具 handler；独立 Demucs 函数还被两个本机 worker 脚本使用 | Demucs CPU/CUDA 与媒体执行服务的 Demucs/UVR 路径；两件真实产物；wav/flac/mp3；不把双声道拆分冒充分轨 | 待迁移 |
| 2 | 人声净化 | `generated_files_media.clean_voice_track` 与服务内 CLI、滤镜 helper | FFmpeg 基础处理、DeepFilterNet AI、auto 降级及真实后端说明；四种现有模式、post_filter、三种格式 | 待迁移 |
| 3 | 文件转写 | `generated_files_media.transcribe_media`、本地模型 helper、媒体执行服务客户端 | 多输入、部分失败、合并/独立输出、md/txt/srt/vtt/json、时间戳、语言/VAD/模型选项；不迁移实时语音 ASR | 待迁移 |
| 4 | 训练素材准备 | `generated_files_media.prepare_voice_dataset` 与 PCM 切片、分析、manifest helper | 原 profile/采样/切片规则、真实 WAV/ZIP/manifest、来源关联与质量统计；不扩展为模型训练 | 待迁移 |
| 5 | 生图 | `image_generation.py` 的 provider/service、Engine 绑定、模型服务配置 | 生成及参考图编辑、多图、输入大小/质量/格式参数、现有实际 provider；密钥不进入模型参数或产物 | 待迁移 |
| 6 | 翻唱 | `cover_song.py`、`LocalRvcExecutorProvider`、Engine 绑定 | RVC WebUI 与本机服务、模型选择、分轨/推理/混音、现有缓存与交付语义；复用已迁移分轨权威 | 待迁移 |
| 7 | 文档生成 | `GeneratedFileService`、`generated_files_io.py`、`generated_files_delivery.py` 及 compose/revise/style 调用链 | 创建、修订、已有 docx/xlsx 格式处理必须共同审计；txt/md/html/json/csv/xlsx/docx/pdf；不能只移 compose 而留下第二套渲染实现 | 待迁移 |

`plugins/market.toml` 当前只有媒体转换条目。上述七项仍在 `tool_handlers/catalog.py` 中注册为内置能力；不能把已补齐的插件基础算成它们已经迁移。

## 宿主与插件职责

- 宿主保留附件/生成文件句柄、会话隔离、公共输入资源端口、文件登记/发送、权限、执行协调、Job、MemCore 和渠道/桌面交付。
- 插件拥有可选业务参数、模型/外部服务调用、文档渲染、产物内容与业务健康检查。只导入公开 SDK，不回调宿主私有旧业务服务。
- 复用现有静态市场、wheel 暂存、权限确认、generation 和 last-good 生命周期；不新增安装器、任务队列或完成事件总线。
- 业务间确有共享实现时必须只有一个权威。不得为了“各自独立”复制 Demucs、RVC、文档渲染或来源解析。
- 不把插件名或 future-only 状态硬编码进稳定模型提示。能力与可用选项来自实际安装描述，native/兼容投影同源。
- 普通 FFmpeg 降噪不是专用去回声/去混响模型；保留原模式参数时也必须如实描述能力限制，不承诺恢复录音或完全移除混响。

## 第一组必须处理的跨边界调用

1. `GeneratedFileService.audio_separation_status()` 目前优先本机媒体服务，随后查 Demucs 包/命令。旧的“包存在即 ready”不满足新健康验收。
2. `scripts/akane_demucs_worker.py` 与 `scripts/akane_local_capability_host.py` 直接导入宿主 `separate_audio_with_demucs`。不能只删工具 handler 而遗漏这些执行入口。业务函数迁入唯一插件/可复用业务包后，这些脚本只能保留薄绑定；依赖未安装时返回明确不可用。
3. 本机媒体服务既提供分轨，也提供文件/实时 ASR 与 RVC。删除独立分轨宿主入口不意味着可删除整个 `LocalMediaExecutorClient` 或关闭该服务。
4. 旧媒体执行服务采用同步 HTTP 请求；断开请求不证明服务端推理已取消。新桥接必须等待真实终态或使用服务确有支持的取消确认，不能把客户端取消冒充远端进程退出。此项必须在替换前解决并测试，不绕过既有 Job。
5. 净化的 `auto` 可以在 AI 失败后回落基础处理，但结果必须带实际后端与降级原因；明确 `ai` 失败时不得输出基础结果并宣称 AI 成功。

## 依赖交付

市场 wheel 不是 GPU 环境安装器。每个条目必须写清运行时、模型/权重、系统二进制或外部服务如何提供，并以实际探测结果决定可用性。

- 可执行程序/独立 Python 环境用管理员配置的真实入口；不把依赖悄悄安装进宿主全局 Python，不假定 host site-packages 等于插件已交付依赖。
- FFmpeg/FFprobe 路径与前一插件保持一致的配置语义。重型运行时独立探测，不污染轻量插件 worker 的导入环境。
- 启动健康检查不能隐式下载大模型、调用付费生成接口或把 provider 密钥输出。缺依赖、版本不兼容、模型缺失应区分为稳定 reason。
- 模型配置/密钥继续遵守既有宿主保密边界。生图迁移前须审计 `model_service_config.py`、`runtime_settings.py` 与路由/UI 的现有设置链，不能留下新旧两套配置权威，也不能擅自删除用户已配置数据。
- 外部服务及权重需要新授权时记录具体阻塞；仍有同目标内可做工作则继续，不以 mock 替代业务完成。

### 2026-09-06 本机只读探测

| 探测 | 真实结果 | 能证明什么 |
|---|---|---|
| `importlib.util.find_spec` | torch、torchaudio、demucs、df、faster_whisper、reportlab、docx、openpyxl 均存在 | 仅模块定位，不证明可执行 |
| `shutil.which` | FFmpeg、FFprobe、Demucs、deepFilter 存在 | 仅入口定位 |
| 导入 torch 与 Demucs | 成功；torch `2.12.0+cpu`，当前解释器 CUDA 不可用 | CPU 导入可用，不代表所有独立环境均无 GPU |
| 禁止下载时 `get_model('htdemucs')` | 成功加载现有缓存；44100 Hz，drums/bass/other/vocals | 可使用现有权重开展真实 CPU 推理验收；尚未证明音频分离质量 |
| `deepFilter --help` | 失败：`ModuleNotFoundError: torchaudio.backend` | 当前默认 DeepFilterNet 环境不兼容，不能标记 AI ready；未修改环境 |

本轮没有读出密钥、安装依赖、下载模型、修改用户实例或发起付费请求。其他模型、RVC 服务与生图 provider 尚未进行真实业务验收。

## 单项完成门槛

每个能力组使用相同门槛，并补充其业务信号/内容检验：

1. **来源与删除清单**：`rg` 列出内置实现、所有调用者、schema、提示/Skill、配置、状态/UI 和测试。明确最终 `deleted / thin adapter` 状态。
2. **依赖与安装**：从源码构建真实 wheel，经市场 hash/大小校验、暂存、审批、隔离激活；缺依赖/坏候选不替换 last-good；声明与实际依赖一致。
3. **发现**：未安装不可见；安装/启用可见；停用/卸载不可见；native 与兼容投影来自同一 descriptor；普通调用前后稳定 schema/hash 不变。
4. **实际业务**：附件和生成文件两种输入，原件不变；真实第三方推理/FFmpeg/provider/render 输出，检验内容而非文件存在；保留旧选项和真实 fallback，不缩成容易通过的子集。
5. **资源与安全**：跨会话、未知/过期句柄、越权、路径逃逸、坏媒体/文档、超预算、部分失败；公开结果无内部缓存/数据库路径和密钥。
6. **Job 与取消**：真实长调用前台返回 Job；重复触发幂等；取消等待实际清理、无孤儿进程、无迟到登记或重复完成；抑制取消的真实完成不能误报 cancelled；错误不能误报 succeeded。
7. **体验**：每件真实产物进入已有工作区、QQ/桌面交付边界；不默认重复发送/播放；失败有可见状态，气泡/TTS/音乐状态不矛盾。文本、音频、图片、文档各按实际媒介检查。
8. **生命周期**：运行中停用/卸载、再启用、依赖失败与候选失败；工具、worker、资源与任务状态一致，不留未来占位提示。
9. **唯一权威**：删除旧业务函数、注册、配置及提示；共享调用改为薄绑定。`rg` 与行为测试证明没有第二套入口继续拥有业务。
10. **提交**：相关 build/test/smoke、`ruff`、`git diff --check`；审查 status/stat/cached 清单，只提交本项文件，然后继续下一项。

业务专项验收：分轨两轨可解码、长度/声道符合输入与模型，并有真实推理证据；净化检查信号变化与实际后端；转写检查真实语音文本/字幕时间；素材集核验 ZIP/manifest/切片一致性；生图检查实际生成/编辑图片；翻唱检查实际模型输出与混音；文档打开/渲染检查内容和版式。

## 迁移窗口与状态记录

审计起点 `87c1c6f`：七项仍由原内置实现提供，尚无第二条已发布业务路径。窗口内不向模型同时发布同一业务的新旧工具。每项替换必须记录新权威、保留旧逻辑的原因、待删除具体符号、约束测试及关闭提交；不能长期保留迁移窗口。

审计切片仅新增本文档（`276f2f7`）。DeepFilterNet 环境问题留作明确风险，不在宿主里伪造兼容模块掩盖错误。

审计切片验证：`git diff --check` 通过。`python -m unittest tests.test_package_reintegration_policy -q` 共 12 项，11 通过、1 失败：`test_dynamic_adapter_execution_uses_single_capcore_prepare_gate` 禁止源码出现 `capcore_build_permission_request`，但基线 `87c1c6f` 的 adapter 已有该导入。已用 `git show` 核对基线导入与断言，两个文件相对基线均无修改。本切片不把这组标为全绿，也不扩大到权限策略修复。

### 分轨 A：独立离线工作进程

- `plugins/akane_audio_separation` 中的业务工作进程不导入宿主私有实现。显式 ML Python 负责加载已存在、校验通过的 Demucs 模型；不使用下载兜底。
- 输入为父进程预处理后的 PCM WAV，工作进程不启动 FFmpeg 子进程或多进程推理池；真实模型生成两条独立音轨，保留 CPU/CUDA 选择与 CUDA 失败后的 CPU 重试。
- 进程生命周期覆盖启动中的取消、重复取消、超时、关闭时仍在启动的子进程；等待实际退出才确认取消。此处不引入另一条宿主任务队列。
- 已运行真实 CPU 分轨：两秒双声道混合信号输出完整双声道两轨、长度不变、不同于输入和彼此，原输入哈希不变。120 秒输入的实际 ML 工作进程在取消后退出，未生成伪成功音轨。这是模型/信号与进程验收，不代表真实歌曲的主观听感已验收。
- 临时迁移窗口从 `276f2f7` 开始：新包尚无插件注册或市场条目，不能被模型调用；原内置仍是唯一产品入口。接入资源/descriptor、远端 Demucs/UVR、市场与双产物交付并验收后，删除 `generated_files_media.separate_audio_stems` / `separate_audio_with_demucs`、服务专属 helper/handler/spec，将两个本机脚本改为薄绑定并关闭窗口。
- 约束命令：`python -m unittest discover -s plugins/akane_audio_separation/tests -v`；最终入口删除仍须宿主安装/发现/生命周期和旧名称守卫测试，不能以本地工作进程测试代替整项迁移。
- 本切片最终 9 项测试全部通过（含真实 CPU 模型、坏权重校验和实际进程取消，无跳过）；新包 `ruff check`、格式检查与 `git diff --check` 通过。未安装插件、未修改宿主运行时或全局依赖。

## 最终交付与授权边界

- 全部七项逐项关闭，才可完成整个目标。每项报告提交、真实命令/结果、用户能感受到的变化和仍未实测部分。
- 公网市场发布、云端部署、真实 QQ 投递、需要新授权的付费服务或重大环境部署不自动执行。替身验证和真实外部验收分别记录。
- 不修改桌宠 main/settings/CSS/布局；原有角色文档、角色资源测试及 `work/` 不夹带进提交。
- V1 扩展回归已有两个 MCP 家族禁用测试在基线失败；不混入本目标，若相关行为因本轮变化再受影响则重新核查。
