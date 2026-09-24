# Akane 访问权限分组 V1

## 目标

让用户只需要理解两组权限，同时继续复用 CapCore 的精确动作校验、参数指纹、审批队列和短时 grant。权限分组只决定默认处理方式，不替代能力自己的风险声明，也不改变工具协议。

## 两组权限

| 分组 | 包含 | 不包含 |
|---|---|---|
| `ops` | Shell 命令、浏览器点击/输入/按键、会产生外部影响的 MCP 与插件工具 | Web 搜索、公开页面读取/截图、记忆读取、Skill/MCP 能力说明、工作区内已有安全文件工具 |
| `extensions` | Skill 发布，以及 MCP/插件的安装配置、启停、更新、重启和移除 | Skill/MCP 列表、校验、发现和加载说明 |

每组只有三个模式：

- `on` / `trusted_auto_allow`：直接允许，但仍执行路径、密钥、URL、身份和参数校验；
- `ask` / `ask_each_time`：为精确调用创建审批请求；
- `off` / `disabled`：不执行该组动作，并把原因返回模型。

精确 capability override 优先于分组；分组未配置时使用宿主内部的保守默认值 `ask_each_time`。公开配置没有第三套全局开关。

## 用户入口

QQ 主人命令：

```text
/access
/access ops on|ask|off
/access extensions on|ask|off
/access all on|ask|off
/approvals
/approve [request-id 后缀]
/deny [request-id 后缀]
```

控制中心直接展示 `ops` 与 `extensions`，每组可独立选择“直接允许 / 每次询问 / 关闭”。安全的读取能力不展示虚假审批开关。

## 审批续接

审批请求仍绑定 profile、session、capability、action 和参数指纹，批准不能复用于另一条调用。QQ 收到 `/approve` 或 `/deny` 后，宿主把结果作为一次不落入 MemCore 的临时宿主事件送回原会话，并通过正常会话队列续接；用户不再需要额外发送“继续”。

## 边界

- 模型可以在执行工作区写扩展草稿；真正发布、安装或启用时才走 `extensions`。
- 浏览器读取与截图保持可用；只有会改变页面或触发外部动作的交互进入 `ops`。
- 分组不新增工具轮数、文件大小、路径别名或上下文限制。
- 控制中心由用户本人直接修改配置，不为配置开关再套一层审批。
