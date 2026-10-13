# 能力路由拆分 v1

起始入口 `companion_v01/routes/capabilities.py` 为 1,840 行。本轮按服务商/声音、
MCP、工作流/任务、审批/目录和音乐职责拆分，保留 HTTP 路径、请求身份、
响应契约及已有 Host Job、插件、能力执行权威。目标入口约 200–400 行，
按实际职责边界调整；不通过模块拆分宣称性能提升。

本轮完成后入口为 **183 行**，只负责共享依赖装配、六组路由注册、原路由
顺序和兼容 helper 导出；职责实现位于 `capabilities_runtime/`，最大单模块
291 行。具体阶段及验收记录如下。

工作区已有 `companion_v01/skill_runtime.py`、技能说明和 `tools/` 改动独立保留。

## 切片一：媒体插件依赖验收与失败回滚

原 FFmpeg 验收期待 `stage_source` 在依赖不可用时失败，但当前暂存流程调用
`prepare`，只审查声明与权限；健康检查在 `install_stage` 的候选激活阶段执行。
测试现在构建新的 0.1.1 临时候选，确认暂存可审阅，再对真实激活入口严格
验证 `ffmpeg_not_found`，没有恢复暂存阶段的健康检查或绕过隔离环境。

实际回滚存在另一个缺陷：候选明确返回 `published=False` 后，旧 generation
仍在运行，但管理层再次重新构建它。同一个缺依赖环境可能使这次重建失败，
掩盖原失败原因并把结果报告为 `rollback_failed`。现在这种情况下只恢复
artifact 目录和持久选择，用现有运行状态完成目录核对；不重建仍在服务的实例。
已经发布后发生的持久化失败仍走运行时恢复流程。

新增回归在原实现上失败，检查失败候选只尝试一次切换；真实 wheel 验收同时
检查旧 generation 编号、插件 digest、选择记录、待激活标记和原能力继续可用。

拆分前基线：路由、音乐、工具曝光与 MCP 管理等 139 项通过，原媒体插件
验收 1 项失败。

修正媒体测试中仍使用旧 `request.event` 的完成/取消夹具，按现有
`SessionTurnRequest.event_type` 和 `data` 核对相同事件语义。扩展管理、媒体
插件及安装生命周期 26 项全部通过；另有插件安装、Host Tool Job 36 项通过。
真实 wheel 验收涵盖 FFmpeg 转换、缺依赖激活拒绝、旧实例保持、取消清理、
任务记忆和模拟 QQ/桌宠投递；Python 编译与 diff 检查通过。

## 切片二：公共上下文与服务商/声音

第一切片提交：`f1845fd6`。

| 模块 | 职责 |
|------|------|
| `context.py` | 每个路由实例的不可变依赖绑定；请求数据仍为局部变量 |
| `definitions.py` | 回调类型、限制、固定 ID 与兼容常量 |
| `common.py` | 请求读取、身份投影、标量校验、指标与日志边界 |
| `providers.py` | 服务商/声音配置、健康检查、目录检查及试音路由 |
| `voice_payloads.py` | 试音载荷、声音参数和私有字段校验 |
| `tts_probe.py` | 现有 TTS 服务链路、试音结果和音频投影 |

入口收敛到 1,291 行。共享审批 store、ExecutorBroker、工作流运行时和音乐
服务仍由原构造入口装配；没有新建第二套执行或任务权威。已有 32 个顶层
函数的签名、36 条 HTTP 路由的顺序/方法/名称和完整 OpenAPI 与原源码一致。

同步环境探测和注入式 TTS 回调原先直接在事件循环执行，会阻塞其他请求。
现在统一在线程调用后接续可等待结果，异步回调仍在事件循环执行主体。两个
针对性回归在原调用方式下失败，修复后通过；新增模块依赖无环检查。

服务商、路由、音乐、MCP、曝光、ASR 与插件 TTS 首轮 146 项全部通过。
回调修复后的后端路由、新模块和 ASR 三个套件共 111 项全部通过；签名、
HTTP/OpenAPI 核对、编译和 diff 检查通过。

## 切片三：MCP 与工作流/任务

第二切片提交：`a5b1aefb`。

`mcp.py` 承接服务器配置、发现及生命周期路由，`workflows.py` 承接工作流
配置、预检、任务启动、状态和输出；`workflow_support.py` 集中输入资产校验
及可执行状态投影。入口收敛到 684 行，仍使用注入的 MCP 管理服务、Broker
和 Host Workflow Job Runtime，任务与输出的 owner 检查保持不变。

发现两个直接相关的问题：同步注入式 MCP 发现回调阻塞事件循环；未绑定
任务调度器时，目录仍把已配置工作流标成可执行，而真实预检返回
`workflow_scheduler_not_bound`。现在发现回调复用公共线程桥，目录投影先检查实际
任务运行时。两个问题均先以针对性回归复现，修复后通过。

后端路由、新路由模块、MCP 管理和本地工作流执行器共 132 项全部通过；
Host Workflow Job 另有 4 项全部通过。32 个原函数签名、36 条有序 HTTP
路由与完整 OpenAPI 核对通过，Python 编译和 diff 检查通过。

## 切片四：目录、审批与音乐

第三切片提交：`621928ac`。

| 模块 | 行数 | 职责 |
|------|-----:|------|
| `catalog.py` | 191 | 能力/技能目录、工具曝光与本地环境探测 |
| `approval.py` | 159 | 审批策略及申请、读取、决定，复用 engine-owned store |
| `music.py` | 237 | 歌词、共听历史和音乐控制权限 |
| `providers.py` | 189 | 服务商与声音设置、检查和试音路由 |
| `mcp.py` | 219 | MCP 配置、发现和生命周期 |
| `workflows.py` | 291 | 工作流配置、预检、任务与输出 |

上下文、公共校验、声音载荷、TTS 探测和工作流投影为单向依赖的支撑模块。
合并技能目录两处相同不可用返回，集中音乐控制名称并移除重复时间模块导入。
没有用反向导入、动态 globals 复制或通配符隐藏模块依赖。

拆分时修复以下直接相关边界；新增四个回归方法均在修复前复现问题：

- 音乐权限把 `"false"`、`"0"`、`"off"` 等明确否定字符串按 False 保存，
  仍支持 `"yes"`、原布尔输入、局部更新和忽略未知控制名。
- 非法共听历史条数回退到现有默认 5，合法数字字符串及最小 1 的语义保持。
- 环境探测和音乐控制存储异常使用稳定失败原因及异常类型，不再返回原异常
  中的路径或凭据内容；环境探测日志也只记录异常类型。原 HTTP 状态、
  `ok=False` 和不自动启用行为保留，原因不被成功状态掩盖。

最终检查：后端路由、新模块、音乐、审批与兑换、工具曝光、MCP、本地工作流
及目录共 **232 项全部通过**。身份/历史隔离、审批重复决定、任务与输出 owner、
不可用/失败/空态和阻塞回调检查由这些套件覆盖。32 个原函数签名、36 条路由
顺序/方法/名称及完整 OpenAPI 核对仍一致；模块依赖无环、编译与 diff 检查通过。

最终回归命令：

```powershell
python -m unittest tests.test_backend_route_modules tests.test_capability_route_modules tests.test_co_listen_route tests.test_music_control_permissions_route tests.test_music_context tests.test_music_control_store tests.test_capability_approval tests.test_execution_approval_redemption tests.test_tool_exposure_api tests.test_mcp_host_manager tests.test_local_workflow_runners tests.test_host_workflow_jobs tests.test_local_capability_catalog
python -m compileall -q companion_v01/routes/capabilities.py companion_v01/routes/capabilities_runtime
git diff --check
```

没有前端变更。本地完成了真实媒体 wheel/FFmpeg 和隔离环境拒绝激活验收；
真实桌面音频、外部 TTS/MCP/ComfyUI 及在线 QQ/NapCat 联调本轮未执行。
