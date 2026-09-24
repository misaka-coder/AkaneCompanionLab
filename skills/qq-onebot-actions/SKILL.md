---
name: qq-onebot-actions
description: 规划复杂 QQ OneBot 工作流：读取历史、构造或转发多条消息、跨群投递、戳一戳、表情回应、点赞与撤回。简单动作直接使用 onebot_action，不需要加载本 Skill；涉及多来源、多目标或消息 node 结构时再加载。
---

# QQ OneBot 复杂工作流

`onebot_action` 是唯一执行入口。不要用 Shell 查找 NapCat 端口、配置或 Token，也不要自行请求 OneBot HTTP 地址。当前 Bot 身份、鉴权、权限范围和真实回执均由宿主绑定；不确定 action 或参数时先调用：

```json
{"action":"capabilities","params":{}}
```

## 动作完成契约

读取、探测、列群、读取历史和构造参数都只是准备阶段，不是发送完成。凡是用户要求对外发送、点赞、戳一戳或撤回：

1. 确定来源数据和唯一目标。
2. 需要历史时读取并转换为合法消息段或转发 `node`。
3. 调用实际动作，如 `send_group_forward_msg`、`send_group_msg`、`group_poke`、`delete_msg`。
4. 只有该动作返回 `ok=true`，才可以说“已发送/已完成”。

失败或超时必须保留工具给出的 `status/reason/code`，不得根据查询成功或自己的计划猜测成功。互不依赖的动作可以在同一模型响应中并行调用；有数据依赖的动作按结果顺序执行。

## 合并转发

真实消息可用引用节点；构造节点必须明确视为模型构造的内容，不得冒充从历史中读取到的原话。常见节点结构：

```json
{
  "action": "send_group_forward_msg",
  "params": {
    "group_id": 123456789,
    "messages": [
      {
        "type": "node",
        "data": {
          "name": "显示名",
          "uin": "来源QQ",
          "content": [
            {"type": "text", "data": {"text": "内容"}}
          ]
        }
      }
    ]
  }
}
```

`content` 必须是消息段数组。`uin` 通常决定头像，`name` 决定节点显示名；客户端最终表现以 OneBot 回执和实际 QQ 渲染为准。

展开已有合并转发时，`get_forward_msg` 的字段名虽然叫 `message_id`，值必须填写回执中的 `forward_id`/`res_id`，不是普通 QQ 消息的数字 `message_id`。当前消息及已验证同会话引用中的转发由宿主自动展开；已有节点直接使用，失败时可用该来源中的真实转发 ID 重试。普通成员传入没有本轮来源的 ID 会得到 `forward_source_unverified`；请让对方直接发送或引用那条转发，不要反复猜 ID 或误称需要主人开放所有权限。主人发起的历史/跨会话工作流仍须先读取真实回执再展开。

## 常见消息段

同一条 `message` 可以包含多个消息段，例如多个系统表情：

```json
[
  {"type":"text","data":{"text":"收到 "}},
  {"type":"face","data":{"id":"66"}},
  {"type":"face","data":{"id":"76"}}
]
```

回复、图片、语音、`mface`、音乐等段的具体字段以 `capabilities` 返回的入口和当前 NapCat 协议为准。不要把网页 URL 当作本地文件，也不要虚构不存在的 `message_id`。

## 权限与边界

- 普通参与者只能操作触发本轮的当前群或当前私聊。
- 跨群、跨私聊、全局联系人查询和消息撤回需要配置的主人账号发起。
- 凭据、账号退出、删除好友、踢人、禁言和群/账号管理不在工具面中。
- 不读取或转发用户未要求的私聊内容，不伪造官方通知；构造聊天记录时应让语境清楚它是整理或演示内容。
