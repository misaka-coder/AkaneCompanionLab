# Akane 闭测身份隔离一期实现说明

## 目标

在不引入账号系统、不改后端存储语义的前提下，让不同测试者通过浏览器访问 Akane 时拥有各自独立的：

- 长期身份
- 当前会话
- 视觉状态
- 提醒轮询上下文

同时避免误触当前全局 `/reset` 导致所有测试数据被清空。

---

## 一期范围

本阶段只做前端隔离，不做服务端用户级删除。

### 会做

- 前端首次自动生成并持久化 `profile_user_id`
- 前端首次自动生成并持久化 `session_id`
- 所有请求从写死 demo 用户切换为动态身份
- 视觉状态按 `profile_user_id` 分桶存储
- 新增“新会话”按钮，仅轮换 `session_id`
- 从普通用户 UI 中移除“重置记忆”按钮

### 不做

- 登录/注册/验证码
- JWT 或服务端签名身份
- 用户级数据删除接口
- 当前会话级清库
- 身份导入/导出

### 已知限制

- 当前 reminders 仍然按 `profile_user_id + session_id` 共同过滤。
- 这意味着用户点击“新会话”后，不会自动看到旧会话里尚未触发的提醒。
- 这属于二期再处理的产品语义问题，不阻塞一期闭测隔离。

---

## 身份语义

### `profile_user_id`

长期身份。

用途：

- 长期记忆
- 摘要与语义记忆
- 提醒
- 向量检索
- 视觉状态隔离

生命周期：

- 浏览器本地长期持久化
- 仅在未来“重置身份”或清空浏览器数据时改变

### `session_id`

当前会话线程。

用途：

- 当前聊天线程
- 当前 session 级上下文
- 当前 session 级 reminders 过滤

生命周期：

- 浏览器本地持久化
- 点击“新会话”时生成新的 UUID

---

## 存储方案

### localStorage

使用 `localStorage` 保存：

- `gal_shell.identity.v1`
- `gal_shell.visual_state.v1.{profile_user_id}`

选择 `localStorage` 的原因：

- 刷新后仍能保留身份与会话
- 更适合陪伴型产品
- 比 `sessionStorage` 更符合“会话可续接”的体验

---

## 关键交互

### 首次访问

前端检查本地是否存在：

- `profile_user_id`
- `session_id`

若不存在，则自动生成 UUID 并保存。

### 新会话

点击“新会话”时：

- 仅重新生成 `session_id`
- 保留原有 `profile_user_id`
- 不调用任何后端 reset 接口
- 清空当前页面上的本地历史展示与流式状态
- 重新初始化当前舞台内容

### 视觉状态

视觉状态不再使用全局固定 key，而是按 `profile_user_id` 存：

`gal_shell.visual_state.v1.{profile_user_id}`

这样同一浏览器未来切换不同身份时，不会互相覆盖立绘/场景状态。

---

## 为什么移除“重置记忆”按钮

当前 `/reset` 是全局清空：

- chat_messages
- memory_summaries
- memory_semantic_summaries
- eval_turns
- reminders
- vector store
- NPC runtime memory

这不是用户级 reset，而是开发级核按钮。

所以一期必须从普通用户路径移除，避免多人闭测时误清空所有人数据。

---

## 二期再做

后续再补：

- 身份备份 / 导入
- `MemoryStore.reset_user_data(profile_user_id)`
- `VectorStore.delete_user_entries(profile_user_id)`
- `SummaryTaskQueue.discard_user_tasks(profile_user_id)`
- `npc_runtime` 的用户级数据清理
- `/reset_identity`

---

## 验收标准

完成后应满足：

- 不同测试者在不同浏览器访问时，记忆不串
- 同一测试者刷新页面后，仍保持原身份与会话
- 点击“新会话”后，开始新线程，但长期记忆仍可继承
- 普通用户界面中不再存在“全局清空所有数据”的入口
