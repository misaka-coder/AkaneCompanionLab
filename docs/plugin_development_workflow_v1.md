# Akane 插件开发工作流 V1

## 目标

模型开发插件时只需要一条权威路径：独立代码项目、当前 release SDK、隔离暂存、精确权限确认、原子发布和真实运行验收。普通聊天不加载 SDK 手册；只有 `plugin-development` Skill 的一行路由描述常驻 Skill 目录。

## 唯一工作流

1. 用 `manage_project_workspace(create/open/select)` 建立当前代码目录。
2. 从只读 `alias:akane-sdk` 选择最接近需求的样例，并以当前 `companion_v01.plugin_api` 为接口权威。
3. 在项目内实现和测试，不修改 release、实例配置或宿主源码。
4. `manage_extension(stage_source, path)` 从源码构建 wheel，并在隔离进程中探测 manifest、权限和贡献项；暂存不会激活代码。
5. `manage_extension(publish, stage_id, approved_permissions)` 只接受该 stage 返回的完整权限集合。
6. 新插件 `enable`；已启用插件更新后按返回状态 `restart`；最后 `list` 并验证真实用户行为。

`stage_wheel` 为已有 wheel 提供同一条审计路径。`discard_stage`、`rollback` 和 `uninstall` 分别处理废弃候选、错误更新和明确卸载。它们不构成第二套安装器。

## 模型看到什么

- 稳定 Skill 目录：插件开发能力的一句描述。
- 加载 Skill 后：上述开发闭环和关键边界。
- 当前请求尾部：平台、Shell、当前 working directory。
- 工具结果：真实 stage id、贡献项、所需权限、发布/重载/激活状态和失败原因。

插件 API 全量符号、样例源码和权限解释不进入普通系统提示词。需要时由模型从 `alias:akane-sdk` 精确读取，避免把插件开发注意力成本施加给所有对话。

## 87 群 hydration 半成品结论

旧实现的源码和 wheel 落在共享执行根，且在 artifact 发布前先写入了启用选择，因而运行时只会得到 `plugin_activation_failed`。源码还使用了已移除的 `add_background_job`，声明了没有实际网络访问的 `network.read`，并未通过当前隔离 generation 完成端到端验收。

它不应继续作为可安装版本。部署本工作流时移除无 artifact 的选择，将散落源码归档；之后从独立项目按当前 SDK 重写，新的调用轨迹才可用于评价插件开发体验。
