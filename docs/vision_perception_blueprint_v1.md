# Akane 视觉感知系统蓝图 V1

## 目标

视觉模型在本项目中的定位不是第二个 Akane，而是 Akane 的眼睛。

V1 只做两件事：

1. 当舞台场景切换后，异步生成当前场景的视觉观察卡，并在后续对话中持续注入，直到下次切场。
2. 当当前会话里有正在讨论或被拿出来展示的视觉礼物时，异步生成该礼物的视觉观察卡，并在后续对话中注入。

后续增补：

3. 当服装切换后，异步生成当前服装的视觉观察卡，并在后续对话中持续注入，直到下次换装。

设计原则：

- 不阻塞当前回复
- 不直接替 Akane 写台词
- 不把大段文学描写塞进 prompt
- 只提供可缓存、可复用、可覆盖的结构化视觉证据

## 架构分层

### 1. `VisionObservationService`

位置：[companion_v01/vision_service.py](/companion_v01/vision_service.py:1)

职责：

- 解析当前场景或礼物对应的图像资源
- 基于资源指纹读取/写入视觉观察缓存
- 命中 override 时直接返回人工观察卡
- 未命中时异步调用视觉模型生成观察卡
- 将观察卡格式化成 prompt 上下文

### 2. `vision_observations` 缓存表

位置：[companion_v01/store.py](/companion_v01/store.py:170)

核心字段：

- `observation_type`: `scene | gift | outfit`
- `resource_fingerprint`
- `target_id`
- `source_path`
- `public_path`
- `prompt_version`
- `provider`
- `model_name`
- `status`: `pending | running | ready | error`
- `summary`
- `observation_json`

缓存唯一键：

- `(observation_type, resource_fingerprint, prompt_version)`

这保证了：

- 同一张图重复出现时不重复烧视觉 API
- 同名文件被替换后不会复用旧观察
- 提示词版本变化时可以安全重新生成

### 3. 引擎接入层

位置：[companion_v01/engine.py](/companion_v01/engine.py:1)

接入方式：

- 生成前：把当前场景观察卡、当前服装观察卡和当前礼物观察卡注入 `extra_context`
- 生成后：异步调度新场景、当前服装或当前焦点礼物的观察任务

这样视觉模型永远不在回复的关键路径上。

## 观察卡 Schema

V1 统一使用结构化观察卡，而不是文学段落：

```json
{
  "type": "scene_observation",
  "summary": "黄昏教室里有偏暖的窗边余光，整体很安静。",
  "entities": ["教室", "窗边", "课桌"],
  "mood_tags": ["黄昏", "安静", "放学后"],
  "uncertainty": ["无法确认是否有人在场"]
}
```

字段约束：

- `summary`: 1 句中文概括
- `entities`: 2 到 6 个可见实体短词
- `mood_tags`: 2 到 6 个氛围短词
- `uncertainty`: 模型拿不准的地方

为什么不用散文：

- 避免视觉模型文风污染 Akane
- 降低 prompt 噪声
- 方便未来进入短期标签系统
- 更利于缓存、测试和覆盖

## 场景观察流

### 触发点

当 Akane 完成一轮回复后，如果输出中包含新的 `scene/character/emotion` 组合，就调用：

- `VisionObservationService.schedule_scene_observation(...)`

### 缓存键

场景观察不按场景名缓存，而按**背景资源指纹**缓存。

当前实现指纹由这些信息组成：

- 文件内容
- 文件修改时间
- 文件大小

这避免了同名换图时命中脏缓存。

### Prompt 注入

下一轮对话进入 `_prepare_final_response_context(...)` 时：

- 先解析当前有效演出状态
- 再从缓存中读取对应场景观察卡
- 若缓存尚未 ready，则返回空字符串，不阻塞主回复

注入形式类似：

```text
当前场景视觉观察：
- summary: 黄昏教室里有偏暖的窗边余光，整体很安静。
- entities: 教室, 窗边, 课桌
- mood_tags: 黄昏, 安静, 放学后
```

## 礼物观察流

### V1 定位

礼物观察只服务视觉礼物，不处理音频礼物。

当前代码已经预留：

- `VisionObservationService.schedule_gift_observation(...)`
- `VisionObservationService.build_gift_prompt_context(...)`

V1 主要面向未来的 `image` 礼物类型。

### 触发策略

礼物观察不常驻全量注入，只针对当前会话的焦点礼物：

- 正在讨论中的礼物
- 或被 Akane 拿出来展示的礼物

### 缓存键

礼物观察同样按图像文件指纹缓存，而不是礼物名称。

这样同一张图被多次拿出来时，只生成一次观察卡。

## 服装观察流

### V1.1 定位

服装观察卡服务的是 `outfit` 层，不追求精细表情分析。

它的目标是让 Akane 知道自己现在穿的是：

- 偏居家
- 偏正式
- 偏轻松
- 偏冷色或暖色

而不是只拿一个 `outfit id` 当字符串猜测。

### 触发策略

服装观察和场景观察一样，属于低频切换、高频复用的静态资源：

- 当前 outfit 进入会话上下文时读取缓存
- 缓存不存在时异步生成
- 当前 outfit 改变后再重新切换到新的观察卡

### 观察边界

服装观察优先描述：

- 服装轮廓
- 颜色与材质
- 穿搭风格
- 整体气质

不需要细致描述：

- 微表情
- 眼神变化
- 极短暂的动作差异

### 缓存键

服装观察按 outfit 选定的代表立绘资源指纹缓存，避免同一套服装因表情不同重复生成。

## Override 通道

为了保留主创的绝对控制权，场景图或礼物图旁边都可以放 sidecar override 文件。

支持：

- `xxx.vision.json`
- `xxx.vision.md`
- `xxx.vision.txt`

优先级：

1. `vision.json`
2. `vision.md`
3. `vision.txt`
4. 远程视觉模型

适用场景：

- 剧情高潮 CG
- 容易被视觉模型误判的抽象背景
- 需要强控制语气/意象的关键画面

## 配置项

位置：[config.py](/config.py:50)

需要配置的环境变量：

- `VISION_API_KEY`
- `VISION_BASE_URL`
- `VISION_MODEL_NAME`
- `VISION_API_PROTOCOL`
- `VISION_ENABLED`
- `VISION_REQUEST_TIMEOUT`
- `VISION_PROMPT_VERSION`
- `VISION_AUTO_SCENE_OBSERVE`
- `VISION_AUTO_GIFT_OBSERVE`
- `VISION_AUTO_OUTFIT_OBSERVE`
- `VISION_MAX_IMAGE_BYTES`

推荐最小配置：

```env
VISION_API_KEY=your_key
VISION_BASE_URL=https://your-openai-compatible-endpoint/v1
VISION_MODEL_NAME=your-vision-model
VISION_API_PROTOCOL=openai
VISION_ENABLED=true
VISION_REQUEST_TIMEOUT=60
VISION_PROMPT_VERSION=v1
VISION_AUTO_SCENE_OBSERVE=true
VISION_AUTO_GIFT_OBSERVE=true
VISION_AUTO_OUTFIT_OBSERVE=true
VISION_MAX_IMAGE_BYTES=8388608
```

## 当前实现状态

已落地：

- 视觉观察缓存表
- `VisionObservationService`
- 场景观察卡 prompt 注入
- 服装观察卡 prompt 注入
- 生成后异步调度场景观察
- 生成后异步调度服装观察
- 礼物观察骨架与缓存接口
- override 通道
- 资源指纹缓存

暂未完整落地：

- `image` 礼物正式上传/管理链路
- 前端“当前展示礼物”显式 UI
- 视觉观察向更长期记忆层的筛选流转

## 推荐实施顺序

1. 先填好视觉 API 配置，验证场景观察缓存命中。
2. 让现有场景描述逐步退场，只对关键 CG 保留 override。
3. 接入 `image` 礼物类型，让礼物观察真正进入用户流程。
4. 之后再考虑把视觉 `mood_tags` 作为短期上下文标签接进更高层的演出导演。

## 边界

- 音频礼物不走视觉模型
- 不默认偷偷看用户屏幕
- 不在每轮回复都调用视觉模型
- 不把视觉模型输出直接当 Akane 台词

一句话总结：

这套系统不是让视觉模型来“演 Akane”，而是让 Akane 在不增加回复延迟的前提下，稳定拥有一双会留下视觉记忆的眼睛。
