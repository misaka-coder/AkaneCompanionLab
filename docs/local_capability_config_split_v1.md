# 本地能力配置模块拆分 v1

## 目的和入口

`companion_v01/local_capability_config.py` 原有 4,098 行，集中处理服务、声音、MCP、工作流、审批策略和配置落盘。现在它是显式导出兼容入口，实现放在 `companion_v01/local_capabilities/`，现有路由、运行时和适配器继续使用原导入路径。

本次只处理这个配置核心，不扩展到 QQ、LLM 或记忆核心的重构。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `definitions.py` | 配置版本、字段白名单、限制、服务与工作流规格 |
| `common.py` | 角色和主机路径边界、标量规范化、公共配置范围 |
| `storage.py` | 配置读取与清洗、角色配置写锁、私人设置更新、原子 JSON 写入 |
| `approval.py` / `approval_policy.py` | 审批策略读写 / 无 I/O 的策略规范化及 Capcore 投影 |
| `providers.py` / `provider_validation.py` | 本地服务配置和有超时的健康检查 / 回环地址及配置校验 |
| `voice_profiles.py` / `voice_validation.py` | 声音配置与模型目录检查 / 私人字段和公共情绪声音投影 |
| `mcp.py` / `mcp_validation.py` | 主机注册表、角色覆盖、发现和迁移 / 传输、占位符、工具模式与公共字段校验 |
| `workflows.py` / `workflow_validation.py` | 绑定、文件保存、运行前检查 / 相对路径、输入槽和资源句柄校验 |

操作模块依赖共用存储层和校验模块。存储层只使用无 I/O 的规范化逻辑，不反向导入操作模块；实现模块也不导入兼容入口。架构测试检查这些边界与循环依赖，避免拆完后通过入口重新耦合。

## 兼容性与实际改进

- 原有 192 个自定义顶层定义都通过兼容入口直接导出，所有原有函数的参数、默认值和返回注解保持一致，没有额外转发包装。
- 配置版本、文件位置、JSON/YAML 读取、公共字段裁剪、凭据占位符、角色路径边界沿用原实现。
- 角色配置及插件连接、工具曝光设置继续使用存储层的同一个 `RLock`。旧配置保存仍保留磁盘中最新的私人连接和工具曝光设置，避免覆盖凭据轮换和较新的偏好。
- 将角色配置、MCP 主机注册表和工作流文件中重复的 JSON 落盘代码合并为 `storage._atomic_write_json`：先写同目录临时文件，关闭后替换目标，并在失败时清理临时文件。补齐原先 MCP 和工作流保存失败时缺少的清理逻辑，失败仍向调用方抛出原异常。
- 故障注入测试的私有补丁目标从兼容入口移到真正的 `storage._write_capability_config`。公共导入方式无需修改。
- 修正文字兜底被错误归属为 TTS 插件服务的问题：内置文本显示始终保持可用，真实语音服务仍按插件状态判断；显式选择文字模式仍禁用语音合成。
- 给运行设置隔离测试注入独立的主机配置，避免读取真实 MemCore 摘要路由。专用摘要路由的生产行为保持原样。

逐定义 AST 核对确认：只有三个文件保存函数因调用共用写入逻辑而调整主体；其余原有定义的主体保持一致。未做性能基准，不能据此宣称运行速度提升。

## 验证记录（2026-10-02）

| 检查 | 结果 |
| --- | --- |
| `tests.test_local_capability_modules`、`tests.test_local_capability_storage`、`tests.test_local_capability_config` | 21 项通过，含模块边界、共享锁、三类真实保存入口的写入及替换失败恢复 |
| 配置、目录、插件连接、工具曝光、MCP 主机、TTS 选择、工作流运行器测试 | 72 项全部通过，含文字兜底与语音插件状态解耦回归 |
| `tests.test_backend_route_modules` | 102 项通过；拆分前基线亦为 102 项通过 |
| 新模块测试及 ComfyUI、TTS、ASR、MCP 适配器、能力诊断、MCP 曝光、Capcore、运行设置、QQ 声音调用检查 | 117 项全部通过 |
| `python -m compileall -q companion_v01/local_capability_config.py companion_v01/local_capabilities tests/test_local_capability_modules.py tests/test_local_capability_storage.py` | 通过 |
| 原定义 AST、签名、全部导出身份及依赖环核对 | 通过 |
| `git diff --check` 及新增文件的空白检查 | 通过 |

初验发现的两项既有失败已收尾：

1. `tests.test_local_capability_catalog.LocalCapabilityCatalogTests.test_static_provider_catalog_projects_confirm_through_capcore`：文字兜底项错误地继承了语音插件的不可用状态，审批被投影为 `disabled`。目录现在只给实际语音服务投影插件状态；原审批断言保留，并增加插件可用/不可用、文字模式关闭合成的行为检查。
2. `tests.test_runtime_settings.LLMRuntimeSettingsIsolationTests.test_two_runtime_instances_build_clients_from_their_own_settings`：真实主机的专用摘要配置让每个测试运行时多构建一个客户端。测试现在通过已有 `config_module` 接口注入独立配置，继续严格校验两实例各自的辅助与聊天客户端；专用摘要路由另有原有测试覆盖，没有修改主机配置。

路由和适配器测试覆盖结构化失败、配置缺失、非法路径、角色隔离和私人字段投影；未启动真实 TTS、ASR、ComfyUI、外部 MCP 服务或桌宠设备进行端到端验收。

本轮前端拆分已独立提交为 `fdda6b5e`（`refactor(desktop-pet): split main runtime into owned controllers`）。后端配置拆分和直接相关的基线修复组成独立提交，QQ 投递拆分另作小切片；用户已有的技能运行时、技能文档与 `tools/` 改动保持独立。
