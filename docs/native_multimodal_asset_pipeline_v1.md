# 原生多模态与图片资产复用实现细节 v1

> 状态：P0/P1 已实现并通过回归、重启与运行态探针
> 分支：`feature/qq-finance-assistant-emquant`
> 建档日期：2026-07-12
> 目标：让 QQ 当前图片由聊天模型原生观看，同时保留视觉观察缓存，并为文档/PPT/云端参考图生图建立安全资产边界。

## 1. 已确认的产品决定

1. 原生多模态模型应直接看到当前用户上传的图片，不再只看到另一个模型生成的文字摘要。
2. `VisionObservationService` 不删除，降级为后台观察器：负责摘要、OCR、可见细节、缓存与非多模态模型降级。
3. 当前聊天模型、视觉观察器和未来图像生成器都只通过受管 asset/material handle 访问图片；本地绝对路径、base64 和原文件内容不进入 memcore。
4. 临时附件仍是短期材料。清理焦点默认不删除原文件；只有用户明确要求时才删除存储。
5. 以后需要长期复用的图片必须显式提升/固定为资产，不能把群聊里每张临时图片永久保存成角色资产。
6. 多图输入由宿主做数量、类型和总字节限制；模型自主理解图片关系，不做僵硬的图片组合白名单。

## 2. 当前真实状态与 401 根因

### 2.1 QQ 图片当前是旁路视觉

```text
QQ image
  -> AttachmentInbox pending_observation
  -> VisionObservationService 独立请求
  -> observation summary / OCR / details
  -> 主聊天模型只看到文字 prompt
```

`companion_v01/engine.py` 目前只把桌面屏幕帧放进 `user_images`；QQ 图片没有进入聊天模型原生图片列表。

### 2.2 原生图片协议底座已存在

- `LLMRuntime` 已接受最多 5 个 data URL 图片项；
- OpenAI-compatible 使用 `image_url`；
- Anthropic adapter 会把 data URL 转成原生 base64 `image` block；
- 工具轮重建最终请求时可以继续携带同一批 `user_images`。

因此 P1 不需要另写一套 Sonnet 图片 API，只需要把安全附件图片接入现有 `user_images` 边界。

### 2.3 401 是旧视觉配置残留

2026-07-12 的运行日志证明视觉请求仍发往：

```text
https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
```

并返回 `401 invalid_api_key`。与此同时当前保存的主模型服务是 Anthropic protocol、`claude-sonnet-5`，且 `useForVision=false`。

根因是 `apply_model_service_settings(...)` 在 `useForVision=false` 时只是不覆盖 `VISION_*`，没有清空旧值，导致旧 DashScope 地址和失效 key 继续被 `VisionObservationService` 使用。

## 3. P0：配置边界修复

### 3.1 `useForVision=false`

必须清空运行态：

```text
VISION_API_KEY
VISION_BASE_URL
VISION_MODEL_NAME
```

视觉服务 reload 后应返回 unavailable/disabled，不得继续请求历史 provider。

### 3.2 `useForVision=true`

当前可见模型服务同时写入 chat 与 vision 配置。只有以下条件一致时，QQ 图片才能直接交给主聊天模型：

- chat/vision API key 相同；
- base URL 相同；
- protocol 相同；
- 当前 chat model（含会话 override）与 vision model 相同。

如果用户为观察器配置了不同的专用视觉模型，则仍走独立观察器，不把图片静默发给另一个聊天模型。

## 4. P1：QQ 当前图片原生输入

### 4.1 附件物化与观察解耦

图片下载完成、受管存储文件存在后即可构造原生图片输入；不再等待 observation 变成 ready。

```text
download/materialize ready
  -> native chat image 可用
  -> observation 继续后台运行
```

### 4.2 受管图片载荷

Attachment service 返回内部临时对象：

```python
{
    "attachment_id": "...",
    "attachment_handle": "img_001",
    "title": "...",
    "media_type": "image/jpeg",
    "data_url": "data:image/jpeg;base64,...",
}
```

约束：

- 只从当前 hard/session namespace 下的指定 attachment ids 读取；
- 路径必须经过 AttachmentInbox 的受管路径解析；
- 只允许 JPEG/PNG/GIF/WebP；
- 单轮最多 5 张；
- 单张沿用 `VISION_MAX_IMAGE_BYTES` 上限；
- 总字节设置独立保守上限；
- data URL 只存在当前请求内存，不写数据库、日志、prompt audit 或 memcore。

### 4.3 QQ 路由决策

```text
当前 chat model 被配置为可看图
  -> 等待附件物化（不等观察摘要）
  -> 原图直接进入 process_turn user_images
  -> 当前回复只发送一次
  -> 后台 observer 完成后只更新缓存，不再补发重复回复

原生视觉未启用或图片物化失败
  -> 保留旧 observation wait/followup 降级路径
```

### 4.4 主模型提示

动态上下文只加入短提示与 handle：

```text
本轮有 2 张原始图片已通过 provider 原生多模态通道提供：img_001、img_002。
请直接依据图片和用户文字回答；视觉摘要只是辅助证据。
```

不得把 base64、存储路径或临时 URL写入文本 prompt。

## 5. P2：材料重新加载与资产提升

P2 不在 P0/P1 中假装完成。

目标：

- 当前新上传图片自动进入本轮；
- 较早但仍 focused 的图片可由模型选择后加载；
- cleared 但未删除的临时材料默认不再可见；
- 用户明确要求长期复用时，通过 `promote_material`/`pin_asset` 创建稳定 asset handle；
- memcore 只写 `material_trace`/asset handle 和清理事件。

群聊资产必须保留 uploader Actor；不能把不同成员的图片归并成同一人的私有资产。

## 6. P3：文档、报告与 PPT 图片复用

当前通用 `compose_file` 不支持 PPTX，也没有通用 `asset_ids` 图片布局协议；金融图表目前只可通过 `chart_ids` 嵌入金融 PDF/XLSX。

后续目标：

```text
compose_document(asset_ids=[...])
compose_presentation(asset_ids=[...])
compose_finance_report(chart_ids=[...], asset_ids=[...])
```

程序层负责：

- 解析 handle；
- 校验 namespace 与文件存在性；
- 读取尺寸和 MIME；
- 确定性缩放、裁切和排版；
- 保存来源/provenance；
- 注册输出为 GeneratedFile/Asset。

模型只提供章节、图片用途和布局意图，不生成任意文件路径或执行任意绘图代码。

## 7. P4：云端图片生成与参考图

未来 provider 工具使用：

```text
generate_image(
  prompt="...",
  reference_asset_ids=["asset_001", "asset_002"],
  reference_roles=["subject", "style"]
)
```

宿主在调用 provider 前把 handle 解析为受控图片字节；provider 输出先进入 Generated Asset Store，再决定发送、嵌文档或继续作为参考图。

不得让模型直接传本地路径、任意 URL、API key 或未经授权的群聊历史图片。

## 8. P0/P1 验收

1. `useForVision=false` 后旧 DashScope 配置被清空，重启/热重载不再发起 401 请求。
2. `useForVision=true` 且 chat/vision 配置一致时，QQ 图片不等待 observer ready 即可进入聊天模型。
3. Anthropic payload 出现原生 `image` block；OpenAI-compatible payload出现 `image_url`。
4. 单图、多图、带文字图片和纯图片消息均只回复一次。
5. 工具调用后续轮仍能看到本轮图片。
6. observer 成功时更新摘要；失败时不覆盖主模型已经完成的回复。
7. 图片 base64、本地路径和私密 URL 不进入 memcore、日志或 prompt audit。
8. 原生视觉未启用/模型不匹配时继续走旧降级路径。
9. QQ、金融推送、桌面屏幕视觉和普通纯文本聊天不退化。

## 9. 当前实施顺序

- [x] P0：修复模型服务配置清理与状态测试。
- [x] P1a：Attachment service 安全构造当前图片载荷。
- [x] P1b：Engine 合并 QQ 图片与桌面帧，并在工具后续轮继续携带原图。
- [x] P1c：QQ 路由优先原生图片、失败回退观察器，避免重复回复。
- [x] 相关单元/路由/LLM/视觉回归、ruff、py_compile 与 `git diff --check`。
- [x] 热配置 `claude-sonnet-5` 为可用于视觉，重启并用合成小图 live probe。
- P2/P3/P4 在后续独立切片推进。

## 10. 2026-07-12 验证记录

- 相关模型服务、附件、QQ 路由、原生工具轮、LLM runtime、视觉观察测试共 208 项通过。
- `ruff check`、`ruff format --check`、`py_compile`、`git diff --check` 通过。
- 快速宽回归 113 项中有 1 个既有前端场景资源断言失败：测试仍期望旧黄昏街道，当前前端默认资源为白天客厅；本轮未修改相关文件。
- 运行态模型服务：Anthropic protocol、`claude-sonnet-5`，chat/vision 同路由。
- 使用项目 `LLMRuntime` 将内存中的 32×32 红色 PNG 通过原生图片块送入模型，模型正确返回主色为 red 且确认看到了图片；无 fallback、无运行时错误。
- 重启后健康检查、QQ bridge 与财经事件 worker 正常；新日志未出现 DashScope 请求或 `invalid_api_key`。
