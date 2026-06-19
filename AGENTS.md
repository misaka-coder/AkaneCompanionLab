# AGENTS.md — AkaneCompanionLab 工程协作者配置

## 工作原则

### 1. 先查代码再动手
- 用 rg / grep 搜相关入口、调用链、测试、文档，不要凭任务描述硬改。
- 改前先看 `git status`，不要覆盖用户已有改动。
- 不要把运行日志、`.env`、缓存、数据库、构建产物提交进去。

### 2. 小步但完整
- 每次只解决当前任务边界内的问题，避免顺手重构无关模块。
- 每步做完跑验证。

### 3. 保持架构边界
- UI 不直接调后端/Tauri。
- 后端不执行桌面端动作。
- 路径/密钥/本地绝对路径不进 snapshot、日志、prompt。

### 4. 失败要结构化
- 不要静默失败。
- 能返回状态就返回 status/reason。
- 不能吞异常影响主流程。

### 5. 不确定先降级
- 不要 fake action。
- 不要假接功能。
- 不要为了"看起来能用"写空实现。

### 6. 改完必须验证
- 跑任务要求的 build/test/smoke。
- 至少跑 `git diff --check`。

### 7. 提交前复核
- 检查 `git diff --stat`、`git diff --cached --name-only`。
- 确认没有敏感文件和无关产物。

### 8. 汇报要具体
- 改了什么、验证了什么、没做什么、剩余风险是什么。

### 9. "表现到位"是验收项
- 后端能力完成不等于体验完成；必须确认用户实际看到、听到、点到的表现是否成立。
- 涉及桌宠回复时，同步检查文字气泡、表情、动作、TTS、音乐/环境状态是否互相打架。
- 不把"字段存在"当成验收；要看该字段是否进入真实渲染/播放/调用链。
- 不为了演示写假成功、假播放、假进度、假状态；未接通就结构化降级。
- UI 修复要覆盖空态、慢请求、失败、重复触发、切角色/切会话等容易露馅的状态。
- 完成报告要说明"用户会感觉到什么变化"，以及还缺哪一段表现没有落地。

## 审查重点

- 有没有误改 main/settings/CSS/布局等红线文件。
- 有没有把 mock 当真实数据。
- 有没有把 not-implemented 做成假成功。
- 有没有让 Promise rejection、后端异常、日志失败影响主流程。
- 有没有路径、API key、cachedPath、storage_relpath 泄漏。
- 有没有破坏已有 fallback、demo/mock、旧后端兼容。
- 有没有测试覆盖真实调用链，而不是只测常量存在。
- 有没有只完成后端字段却没有让桌宠/控制中心/气泡/TTS/音乐等表现面同步到位。

## 提交策略

- 只有用户明确要求提交才 commit。
- commit 前先确认工作区内容。
- 不提交：`.env`、`runtime_logs`、`users_data`、`*.db`、`node_modules`、`dist`/`target` 缓存。
- 如果工作区已有大量改动，先做 checkpoint，再进行基础设施类改造。

## 当前重点任务入口

- 桌宠角色工坊 V1：`docs/desktop_pet_character_workshop_v1/README.md`
- 这条主线以 `desktop_pet_next` 为新桌宠主线，旧 Electron `desktop_pet` 冻结，仅保留当前可用状态。
- 设置窗口唯一实现为 `control-center-lab.html`。`settings.html` 仅是兼容跳转页；不要在其中新增功能，也不要恢复旧设置实现。

## Agent 护栏

- 接手桌宠角色自定义、提示词配置、记忆隔离、立绘校准相关任务时，先读 `docs/desktop_pet_character_workshop_v1/`。
- 每轮只做一个可验证切片；不要同时推进 UI、数据库迁移、资产导入和提示词链路。
- Rust 写入角色包文件时使用临时文件再 rename；所有从角色包读取出的相对路径必须走 `safe_child_path`。
- 新增前端运行时引用必须来自现有模块或明确导入，不能依赖臆造的全局变量。
- 审查发现高风险问题时先停在 repair pass，不要继续叠新功能。

## 项目速查

| 目录 | 用途 |
|------|------|
| `companion_v01/` | Python/FastAPI 后端（83 文件，~4.8 万行） |
| `services/` | 共享服务（LLM client, TTS client） |
| `web/` | Web 前端静态资源 |
| `desktop_pet/` | Electron 桌宠（V0） |
| `desktop_pet_next/` | Tauri/WebView2 桌宠（next-gen） |
| `desktop_pet_creator_kit/` | 角色包创建工具 |
| `tests/` | 测试套件（46 文件） |
| `docs/` | 文档（82 篇 Markdown） |
| `deploy/` | 部署资源（Nginx, systemd, env 示例） |

### 启动入口
- 后端：`python launch_akane_memory_v01.py` → uvicorn `companion_v01.app:app`（默认 `0.0.0.0:9999`）
- Web 前端：FastAPI 直接 serve `web/` 静态文件
- 桌宠 next-gen：`desktop_pet_next/` 内 `npm run tauri dev`

### 配置
- `config.py`：pydantic-settings，`.env` 加载，模块级全局变量导出
- `companion_v01/persona_profiles.toml`：人设提示词
- `desktop_pet_creator_kit/characters/`：角色包

### 测试
```bash
python -m unittest tests.test_backend_route_modules    # 路由模块测试（30 tests）
python -m py_compile <file>                            # 快速语法检查
```
