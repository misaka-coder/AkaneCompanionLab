# QQ 小程序卡片生成与分享（2026-09-08）

本文保留最初模板生成切片的验收记录。后续客户端验收已证明 NapCat 的 PC B 站模板不能稳定播放，
因此 B 站生成路径已停用，改为转发真实原生卡片；最终结论见 [链接与句柄切片](qq_miniapp_link_handles_20260908.md)。
非 B 站模板仍使用 `card_ref`，替代下文正常生成后复制 `message` 的旧调用方式。

## 本轮范围

- 现有 `onebot_action` 新增 `get_mini_app_ark`，不新增发送工具、Shell 直连或平台专属发送路径。
- 当时支持 NapCat 的 `bili`、`weibo` 模板及完整参数自定义模式；`bili` 后因真实播放验收失败而停用。
  生成其他模板和签名仍由 NapCat 负责。
- `qq_miniapp.py` 只做宿主参数验证及生成结果到模型结果的投影，不实现入站卡片解析或另一套签名。
- 新增项目内 Skill `skills/qq-miniapp-share`；仅依赖 `onebot_action`，自定义参数说明按需加载。
- 原始 JSON/XML 仍可经既有消息读取能力按需读取；业务链接和签名不擅自截断。

## 调用与完成语义

1. 从真实来源准备材料。接口不搜索网页、不提取视频信息。
2. 调用生成接口。非主人可在有效当前会话生成；生成没有收件人，不扩大权限。
3. 检查 `ok=true`、`stage=generated`、`message` 数组。该步骤没有可见发送回执，不会 finish turn。
4. 用户要求发送时，将返回的 `message` 原样交给既有群聊或私聊发送接口。
5. 发送接口按既有权限和回执处理；实际 QQ 展示、点击跳转需另行验收。

`rawArkData="true"` 保留原始生成结果，但不返回发送数组；正常分享省略此选项。
参数错误、空/畸形/过大 Ark 均返回结构化失败。超时不自动重试，不自动换普通链接或其他投递形式。
链接的静态校验不进行 DNS 查询，因为此步骤是向 QQ 提交卡片材料，不是宿主抓取远端资源；
真正媒体读取的 DNS/连接安全检查继续由原有公开 URL 策略执行，未放宽。

## 验证

- Skill Creator 校验通过，Akane SkillRegistry 的实际目录筛选、加载和 reference 读取通过。
- 新增测试通过真实 native schema、handler、QQ delivery port、gateway、HTTP adapter；仅替换外部 HTTP 响应。
  覆盖生成到发送、群聊/私聊、主人跨群、普通成员跨会话拒绝、B 站/微博/自定义参数、原始输出、
  缺参/非法 URL/数字字符串、生成失败、超时、空成功、发送失败、不重复发送及不自动改投递形式。
- 回归包含 OneBot action/transport、Skill runtime/acceptance、QQ rich materials、后端路由与昵称唤醒。
  最终运行 192 项测试，191 项通过、1 项既有跳过；Ruff、py_compile、git diff --check 通过。
- 本机 NapCat 4.18.19 的已安装代码参数契约与官方源码一致。
- 本机真实生成成功：`app=com.tencent.miniapp_01`，返回完整 meta 和 JSON 消息段（本次约 1 KB）。
  使用明确标识“未发送”的生成测试材料，不冒充真实视频元数据；没有调用发送接口。
- 尝试公开 B 站元数据接口时返回 HTTP 412，未绕过限制。此结果不是小程序生成接口失败。
- 尚未做真实模型自主选 Skill 的在线验收，也未验证收件端显示/点击；微博和自定义模式目前为契约测试。
- 本地后端已重启加载；在线 `/health` 正常，`/capabilities` 返回新增 bundled Skill 与 `onebot_action` 依赖。
  没有重启桌宠、NapCat 或云端，没有修改用户角色、模型、代理或 DNS 配置。

## 本轮追加诊断：公开 B 站链接被报告为本机/内网

- 用户报告的原消息保留了正常 `https://b23.tv/...` 短链，并未被卡片解析器转换为本地路径。
- 当时本机 DNS 将 `b23.tv` 解析为 `198.18.0.117`，将 `www.bilibili.com` 解析为 `198.18.0.119`。
- 现有 `validate_public_http_url` 对这样的非公网地址返回 `remote_url_private_address`，已在本机复现。
  该地址段与代理 Fake-IP 常用范围一致；未修改代理、DNS 或内网访问保护。
- 标题来自卡片已有信息；视频读取需要额外访问链接，因此“能读标题、不能读视频”并不矛盾。
  “换公开直链”的回复不足以解释这个环境问题，不能据此认定用户链接是内网地址。
- 这项诊断与新增生成接口分开。后续若要修复，应先确认代理/DNS 配置及安全的媒体访问路径，
  不应全局允许非公网地址来绕过检查。

## 依据

- [NapCat 生成接口源码](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/extends/GetMiniAppArk.ts)
- [模板与原始结果投影](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-core/packet/utils/helper/miniAppHelper.ts)
- [Packet 依赖检查](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/packet/GetPacketStatus.ts)
