# Akane 插件开发工作流 V1

## 目标

模型开发插件时只需要一条权威路径：独立代码项目、当前 release SDK、隔离暂存、精确权限确认、原子安装和真实运行验收。普通聊天不加载 SDK 手册；只有 `plugin-development` Skill 的一行路由描述常驻 Skill 目录。

## 唯一工作流

1. 用 `manage_project_workspace(create/open/select)` 建立当前代码目录。
2. 从只读 `alias:akane-sdk` 选择最接近需求的当前 release 样例，不搜索宿主物理 release。
3. 在项目内实现并编写普通 `unittest`，直接引用插件使用的真实公开 SDK，不伪造 CapCore、adapter 或 Plugin API。
4. `manage_extension(test_source, path)` 在独立子进程中使用当前 release 的真实 SDK 运行项目测试；它不暂存、不安装、不激活插件。
5. `manage_extension(stage_source, path)` 从源码构建 wheel，并在隔离进程中探测 manifest、权限和贡献项；暂存不会激活代码。
6. `manage_extension(install, stage_id, approved_permissions)` 只接受该 stage 返回的完整权限集合，并由宿主一次完成制品发布、selection 更新和 generation 激活。
7. 最后 `list` 并验证真实用户行为；模型不手工协调 publish、enable、restart 或 reconcile。

`stage_wheel` 为已有 wheel 提供同一条审计路径。`discard_stage`、`rollback` 和 `uninstall` 分别处理废弃候选、错误更新和明确卸载。它们不构成第二套安装器。

## 与参考项目的校准

- AstrBot 对外提供创建、安装、更新、移除和重载等完整意图；插件加载失败时展示错误并允许修复后重载。调用方不负责拼装内部插件管理状态。
- Alife 由统一 `PluginSystem` 协调市场、安装和运行环境同步；其源码也明确建议调用统一系统，而不是让上层分别操作内部组件。
- OpenCode 以项目/全局插件目录或配置中的包名表达安装意图，宿主负责发现、安装依赖和重载。

Akane 保留两步而不是一步，是因为“精确权限确认”需要一个稳定候选作为审批边界：
`stage_*` 只构建、审计和探测，`install` 则一次完成发布、selection 持久化与隔离代切换。
这比参考项目多出的复杂度只服务于权限审阅、进程隔离和 last-good；内部 catalog、
selection、generation 与 reconcile 不暴露给模型或外部开发者。

## 模型看到什么

- 稳定 Skill 目录：插件开发能力的一句描述。
- 加载 Skill 后：上述开发闭环和关键边界。
- 当前请求尾部：平台、Shell、当前 working directory。
- 工具结果：真实 stage id、贡献项、所需权限、安装/激活状态和失败原因。

插件 API、样例源码和权限解释不进入普通系统提示词。需要时由模型从 `alias:akane-sdk` 精确读取，避免把插件开发注意力成本施加给所有对话。

普通 `exec_run` 不继承 Akane 宿主私有模块。插件测试需要的真实公开契约只通过
`test_source` 注入：它从当前 release 动态取得 SDK，适用于 capability、命令、事件、后台服务等
所有插件形态，不绑定某个样例或业务。这样普通项目不会偶然依赖宿主 venv，插件测试也不会通过
`sys.modules` 假替身制造与生产不一致的成功。

## 真实闭环要求

单个组件通过不代表插件可用。回归必须覆盖全新源码项目从 stage 到 install、active 和真实 capability/command/event/background 行为。运行时未尝试某个插件时不能将其报告为 activation_failed，目标插件没有进入候选 generation 时也不能报告安装成功。
