# Akane Skills V1：渐进加载、自主编写与热重载

## 1. 目标与边界

Akane Skill 是一个带 `SKILL.md` 的任务操作手册目录。它把模型已经拥有的推理能力与
Akane 现有工具组织成可复用工作流，但它不是插件、不是动态 ToolSpec，也不会获得新的
权限。Skill 中的脚本仍由 `exec_run` 执行；搜索、记忆、附件、文件登记和交付仍走原有
工具及 MemCore 轨迹。

V1 已覆盖：

- 每次模型请求按目录发现合法 Skill，常驻上下文只展示稳定的 `name + description`；
- `load_skill` 按需加载完整 `SKILL.md`，并可继续读取被明确引用的文本资源；
- 模型可用 Shell 在执行工作区创建或下载草稿，再用 `manage_skill` 校验并原子发布；
- 发布、人工复制或修改后无需重启；下一次模型请求读取最新有效版本；
- 无效的半写入更新不会覆盖上一有效版本，并在目录提示中产生结构化诊断；
- 当前工具轮看到完整 Skill 结果，assistant final 后由 MemCore 按现有策略托管/压卡/回读。

V1 不做动态 Python import、插件代码加载、Skill 自建工具 schema、联网商店、依赖自动提权
或绕过现有执行审批。

## 2. 目录

| 来源 | 目录 | 语义 |
| --- | --- | --- |
| bundled | 仓库 `skills/<name>/SKILL.md` | 随 Akane release 发布的只读内置 Skill |
| managed | `DATA_ROOT/skills/<name>/SKILL.md` | 主人发布、可热更新的持久 Skill |
| draft | 执行工作区 `skill_drafts/<name>/` | Shell 编写/下载的待校验草稿，不进入模型目录 |

同名时 managed 覆盖 bundled。Skill 名必须与目录名一致，并匹配：

```text
^[a-z0-9][a-z0-9._-]{0,63}$
```

最小格式：

```markdown
---
name: system-diagnosis
description: Use when diagnosing services, ports, processes, logs, or deployment state.
---

# System Diagnosis

按这里写具体步骤。
```

可以按需增加 `references/`、`scripts/`、模板或小型资产。`SKILL.md` 必须明确什么时候读取哪
份 reference、怎样运行脚本、输入输出是什么、如何判断失败；不能要求模型把整个目录一次性
读进上下文。

## 3. 模型可见契约

### 3.1 Skills 目录

每次生成 Prompt 时，宿主输出确定性排序的目录：

```text
【可按需加载的 Skills（目录版本 ...）】
Skill 是任务操作手册，不会增加权限或自动执行代码……
- skill-creator：Use when ...
```

目录不包含 mtime、绝对路径或健康探针抖动。`name/description/source` 不变时 hash 与文本逐字节
稳定。只修改 Skill 正文、reference 或 script 不改变目录 Prompt；修改名称或 description 会在
下一次请求产生一次有意的缓存分支，之后重新稳定。

单次目录最多 100 项、16000 字符。达到上限会明确标注截断，不会悄悄声称全部可见。

### 3.2 `load_skill`

```json
{
  "type": "load_skill",
  "name": "skill-creator",
  "resource": "references/example.md"
}
```

- `name` 必须使用目录中的精确名称；
- 省略 `resource` 时加载 `SKILL.md`；
- 只有主说明明确要求时才读取对应 reference；
- 返回完整、有界正文、内容 revision、文件列表，以及脚本可使用的相对执行定位；
- managed 脚本使用 `cwd=alias:skills`，bundled 脚本使用 `cwd=alias:bundled_skills`；随后命令以
  `<skill-name>/scripts/...` 相对定位，不向 Prompt 泄漏宿主真实根目录；
- 读取失败返回 `not_found/error + reason`，不会生成假内容。

### 3.3 `manage_skill`

校验：

```json
{
  "type": "manage_skill",
  "action": "validate",
  "draft_path": "skill_drafts/system-diagnosis"
}
```

发布：

```json
{
  "type": "manage_skill",
  "action": "publish",
  "draft_path": "skill_drafts/system-diagnosis",
  "replace": false
}
```

发布先复制到 managed root 内的 staging 目录，在 staging 中再次校验文件数、体积、UTF-8、
frontmatter、目录名和 symlink，再用 rename 切换；替换失败会恢复旧目录。`published` 是唯一安装
成功状态。`validate` 不修改目录；已有同名 Skill 且 `replace=false` 返回 `conflict`。

删除不另造 Skill 专用 API。先调用 `load_skill` 确认精确名称、source 和返回的执行 cwd：managed Skill
使用 `exec_run(cwd="alias:skills")` 删除唯一的 `<name>` 相对目录并核验；bundled Skill 属于 release，
不得删除。请求内下一次模型调用会重新扫描目录，因此删除后自然热重载。安装副本与执行工作区草稿是
两个目录；只有用户明确要求丢弃草稿时，才另外删除 `skill_drafts/<name>`。普通文件操作不需要为每种
命令新增宿主代码；只有带数据库、handle 或交付状态的附件/生成物仍必须走其领域清理工具，避免脏元数据。

## 4. 热重载语义

Akane 不需要长期文件监听线程。每个模型请求构建上下文时进行有界发现并生成不可变快照：

1. 当前 provider 请求使用当次构建的目录文本；
2. 工具执行返回后，下一次 provider 请求重新发现目录；
3. 因此模型在一轮中写草稿、发布成功后，紧接着的下一次模型调用就能看到新 Skill；
4. 如果 `SKILL.md` 正被半写入、YAML 无效或目录不一致，已有 Skill 继续使用上一有效版本，新 Skill
   暂时忽略；诊断会明确显示；
5. `load_skill` 实际返回的正文进入普通工具轨迹，所以之后即使 Skill 又更新，MemCore 仍保存模型
   当时真正读到的版本，而不是用新文件改写历史。

这一语义借鉴 OpenClaw 的 snapshot version、OpenCode 的 staging/backup 切换，以及 AstrBot 的
请求级工作区 Skill 发现，但没有引入它们各自的插件/沙盒系统。

## 5. 权限

- 已安装 Skill 可被当前能力画像中的模型按需读取；Skill 自身不能授予权限。
- `manage_skill` 只随已启用的 execution 能力出现。
- 桌宠可信本地请求可以发布；QQ 只有 `MASTER_QQ` 发出的私聊或群聊请求可以发布。
- 普通群成员即使能使用已安装 Skill，也不能通过 `manage_skill` 改全局目录。
- `TrustedLocalExecutor` 仍不是 OS sandbox。主人给某个会话开放 Shell 等于允许模型以宿主用户权限
  执行命令；Skill 不会把这一事实包装成虚假的安全隔离。

## 6. 自主创建闭环

仓库自带 `skill-creator`。用户要求 Akane 创建或安装 Skill 时，推荐路径是：

```text
load_skill(skill-creator)
→ web_search / exec_run 搜索或整理资料
→ exec_run 写入 skill_drafts/<name>/...
→ manage_skill(validate)
→ 修复结构化错误
→ manage_skill(publish)
→ 下一次模型请求看到新目录
→ 按任务需要 load_skill(new-name)
```

安装来源可以是模型自己编写的文件，也可以是通过现有搜索/下载能力取得并整理的目录。下载成功
不等于安装成功；只有 `manage_skill` 返回 `published` 才能对用户确认。

## 7. 参考项目对标结论

| 项目 | 借鉴点 | Akane 的取舍 |
| --- | --- | --- |
| OpenCode | `name/description` 目录、按名 skill tool、远程更新 staging/backup | 保留渐进加载和原子替换，不接它的插件/权限运行时 |
| OpenClaw | 只关注 `SKILL.md`、debounce、snapshot version、Prompt 数量/字符上限 | 使用请求级有界重扫代替常驻 watcher，避免线程和 FD 生命周期复杂度 |
| AstrBot | 本地/插件/工作区来源优先级、local execution 只对管理员开放 | 使用 bundled/managed 两层和主人 QQ 写权限；脚本复用 Akane exec/MemCore 闭环 |

## 8. 聚焦验证

```powershell
python -m unittest tests.test_skill_runtime
python -m unittest tests.test_native_tool_schema tests.test_native_web_search_tooling
python -m unittest tests.test_execution_wiring tests.test_execution_provider_config tests.test_execution_local
python -m unittest tests.test_memcore_integration
python -m py_compile companion_v01/skill_runtime.py companion_v01/skill_specs.py companion_v01/tool_handlers/skills.py
git diff --check
```

## 9. Coding-project Skill 云端部署记录（2026-08-14）

- 云端不可变 release `e0fa6fd-coding-project` 已上线统一 Host；上一版
  `5f2b975-long-tool-paging` 保留为直接回滚点
  （`/etc/systemd/system/akane-host.service.d/90-release.conf.pre-e0fa6fd`）。
- 该 release 带来三个 coding 闭环变更：新增 bundled `coding-project` Skill
  （MVP 界定、环境检查、增量构建、真实失败传播、验证等级、诚实交付）；收紧
  compose_file / revise_generated_file / exec_run 的模型可见描述；exec 宿主
  quoting 与终端反馈修复（commit `34c4be3`）。
- 部署前对 personal/finance 的 memcore 与 akane_memory 共 6 个 SQLite 库做
  备份并全部 `PRAGMA quick_check` ok，备份目录
  `/opt/akane/backups/e0fa6fd-predeploy-20260814T141810Z`。
- 切换前在新 release 内运行 8 个聚焦测试模块 170 项回归：169 通过、2 项
  平台 skip、1 项既有失败（web-media-download SKILL.md 正文与测试断言漂移，
  在当前线上 release 同样存在，非本 release 引入）。
- 切换后 `/health` 为 ok 且 root binding 有效，`akane-host.service` active
  且 `NRestarts=0`，进程 cwd 指向新 release；NapCat 事件正常接收，无启动
  traceback/import error；vision JSONDecodeError 为既有现象。
- 新 release 内 SkillRegistry 实测加载 6 个 skill（含 coding-project，
  bundled，正文完整含 MVP/验证等级/交付边界）。
- 未修改 Bot 账号、模型密钥、host.env、bots.toml、QQ profile 或数据根；
  `.packages` 由上一 release 原样复制（本轮未改动任何抽包依赖）。
- 部署前 schema 变化说明：compose_file / revise_generated_file / exec_run
  描述各改动一次，对应 provider 前缀会冷建一次；之后保持稳定，不影响
  MemCore 压卡规则。
