---
name: qq-onebot-actions
description: 通过当前 NapCat 的 OneBot HTTP 接口执行 QQ 扩展动作：读取群/私聊历史、发送群/私聊消息、合并转发、戳一戳和点赞。用户明确要求主动操作 QQ 或读取聊天记录时加载；普通文字回复不需要本 Skill。
---

# QQ OneBot 扩展动作

本 Skill 只使用当前 NapCat 登录账号已经拥有的能力，不新增权限。统一入口（`cwd=alias:skills`）为：

```bash
python3 qq-onebot-actions/scripts/onebot_call.py <action> '<json params>'
```

脚本会从本机配置读取凭据、探测可用端口，并且不会打印 token。

## 动作完成契约

读取、探测、列群、读取历史和构造参数都只是准备阶段，不是发送完成。凡是用户要求对外发送、点赞或戳一戳：

1. 先确定来源数据和唯一目标（`target_group_id` 或 `user_id`）。
2. 需要历史时读取并转换成合法的消息段或转发 `node`。
3. **实际调用对应动作**，例如 `send_forward_msg`、`send_group_msg`、`send_private_msg`、`group_poke`。
4. 只有该动作返回 `status="ok"` 且 `retcode=0`，才可以告诉用户“已发送/已完成”。

没有动作调用，或动作返回失败/超时，必须明确说“尚未发送/未完成”，保留返回的 `status`、`retcode`、`message` 和 `wording`，不得根据查询成功、脚本结束或自己的计划猜测成功。动作回执到达之前不要使用完成时态。

## 多群合并转发流程

多群任务必须按下面顺序执行：

```text
source_group_ids → get_group_msg_history（每个来源群）
→ 转换为 node 数组（name/uin/content）
→ 明确 target_group_id
→ send_forward_msg
→ 检查 status=ok 且 retcode=0
→ 再回复用户
```

`send_forward_msg` 示例：

```bash
python3 qq-onebot-actions/scripts/onebot_call.py send_forward_msg \
'{"group_id":872732158,"messages":[{"type":"node","data":{"name":"某人","uin":"对方QQ","content":[{"type":"text","data":{"text":"内容"}}]}}]}'
```

每个 `node.data.content` 必须是数组；`uin` 是消息来源 QQ，`name` 是显示名。查询结果中的 `status=ok` 只证明查询成功，不能替代最终发送动作。

## 常用动作

```text
probe
get_group_list '{}'
get_group_msg_history '{"group_id":872732158,"count":10}'
get_friend_msg_history '{"user_id":1906243651,"count":10}'
send_group_msg '{"group_id":872732158,"message":"大家好"}'
send_private_msg '{"user_id":1906243651,"message":"你好"}'
group_poke '{"group_id":872732158,"user_id":1906243651}'
friend_poke '{"user_id":1906243651}'
set_msg_emoji_like '{"message_id":12345,"emoji_id":"60"}'
```

## 故障处理

脚本会把 HTTP 错误、连接失败和 OneBot `retcode != 0` 作为结构化失败返回；失败时不要换目标或猜权限后宣称成功。`probe` 的结果只用于确认端口和登录账号，若有多个账号必须使用返回的 `self_id` 与当前机器人身份匹配。

不要在命令参数、输出或 Skill 文件中打印凭据。不要读取或转发用户未要求的私聊、群聊内容，也不要伪造官方通知或他人身份。
