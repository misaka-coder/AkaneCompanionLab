# 角色引擎拆分蓝图 V1

## 目标

这份蓝图回答一个核心问题：

> 如何把当前的 Akane 项目，逐步拆成“可持续创作的作品层”与“可复用的角色引擎层”，最终逼近“以后几乎不用改代码就能造新角色”的状态。

一句话目标：

> Akane 不只是一个作品，而是第一个跑在角色存在引擎上的角色实例。

---

## 一、先说结论

你现在的项目，已经不是“单角色 demo”了。

它已经具备了很明显的双层结构雏形：

- `作品层`
  - 决定 Akane 是谁、她住在哪个世界、她穿什么、她说话像什么。
- `引擎层`
  - 决定任何角色如何记忆、如何切场景、如何处理礼物、如何调用工具、如何感知视觉内容。

真正要做的，不是推翻重写，而是：

- 识别哪些东西已经是通用引擎
- 识别哪些东西还写着 Akane 的名字
- 把“角色专属信息”从 Python 逻辑中拔出来，收进角色包

---

## 二、定义：什么叫作品层，什么叫引擎层

### 1. `作品层`

作品层只回答“这个角色是谁”。

它应该包含：

- 角色设定
- 口吻与人设
- 世界观边界
- 初始资源包
- 特定作品里的命名、叙事、审美和 override

作品层不应该决定：

- 数据库怎么存
- 礼物状态机怎么跑
- 视觉缓存怎么命中
- Prompt 组装流程怎么调度

### 2. `引擎层`

引擎层只回答“任何角色怎样活起来”。

它应该包含：

- 身份与会话隔离
- 记忆系统
- 检索与摘要
- Prompt 组装
- 工具协议
- 礼物流转
- 资源投影
- 视觉感知
- Web / 桌面壳

引擎层不应该决定：

- Akane 喜欢什么风格
- 某张图应该叫“窗边黄昏”
- 某个角色该不该用妹妹口吻
- 某套服装是不是“更像她”

这些属于作品层。

---

## 三、你当前仓库的现状映射

下面是按“更偏作品层 / 更偏引擎层 / 混合层”做的现状判断。

### 1. 已经很像引擎层的部分

- [companion_v01/engine.py](/companion_v01/engine.py)
  - 统一调度记忆、工具、礼物、视觉、资源投影。
- [companion_v01/store.py](/companion_v01/store.py)
  - 会话、消息、提醒、礼物、视觉缓存等持久层。
- [companion_v01/resource_manifest.py](/companion_v01/resource_manifest.py)
  - 资源协议、运行时 manifest、视觉对象解析。
- [companion_v01/gift_system](/companion_v01/gift_system)
  - 礼物仓储、处理器、投影、决策上下文。
- [companion_v01/vision_service.py](/companion_v01/vision_service.py)
  - 场景 / 礼物 / 服装观察卡。
- [companion_v01/tool_runtime.py](/companion_v01/tool_runtime.py)
  - 工具协议与 handler 体系。
- [companion_v01/memory_compaction_service.py](/companion_v01/memory_compaction_service.py)
  - 记忆压缩与摘要循环。

这些模块的价值，不是只服务 Akane，而是已经接近“任何角色都需要”的基础设施。

### 2. 已经很像作品层的部分

- [companion_v01/persona_profiles.toml](/companion_v01/persona_profiles.toml)
  - 人设、说话边界、输出风格、角色示例。
- `assets/` 下具体的场景、服装、表情、音乐资源
  - 这些决定角色可见世界的审美。
- 各类 `.vision.json/.md/.txt` override
  - 这些是主创意志，不该算通用逻辑。
- 未来某角色专属的收藏名、礼物命名偏好、世界规则
  - 这些都应进入角色包。

### 3. 当前仍然混合的部分

- [companion_v01/prompt_builder.py](/companion_v01/prompt_builder.py)
  - 结构是通用的，但当前仍然紧贴某个 persona 实例。
- [companion_v01/persona_config.py](/companion_v01/persona_config.py)
  - 仍然像“默认角色入口”，还不是真正的角色加载协议。
- [web/app.js](/web/app.js) / [web/index.html](/web/index.html)
  - 已经有通用壳，但仍有 Akane 名称和单角色默认体验。
- [companion_v01/app.py](/companion_v01/app.py)
  - 目前是“引擎 API + 当前作品入口”混在一起。

这些混合层，就是接下来最值得拆的地方。

---

## 四、最终目标形态

目标不是“让用户改 Python 配置”。

目标是：

> 用户准备一个角色包，填好角色卡，导入资源，系统就能跑出一个自己的长期角色。

理想结构如下：

```text
/companion_engine
  /core
  /memory
  /gifts
  /vision
  /tools
  /resources
  /shells

/characters
  /akane
    character.toml
    /assets
    /notes
    /overrides
    /policies
  /another_character
    character.toml
    /assets
    /notes
    /overrides
    /policies
```

这个结构里：

- `companion_engine`
  - 只放可复用代码
- `characters/akane`
  - 只放 Akane 专属内容

---

## 五、作品层应包含什么

### 1. `character.toml`

这是未来最重要的角色入口文件。

它至少应该能表达：

- 角色名
- 对用户的称呼体系
- 说话风格
- 人设摘要
- 世界观边界
- 默认舞台模式
- 默认礼物偏好
- 默认视觉偏好
- 默认工具策略

示意：

```toml
[identity]
id = "akane"
name = "Akane"
default_user_title = "主人"

[persona]
summary = "带一点撒娇感、亲近、细腻、不会过度解释。"
style = "轻小说式口吻，亲密但不做作。"

[world]
theme = "日常陪伴 / gal shell / 轻叙事"

[preferences.gifts]
audio_internalize_default = true
image_collection_default = "memories"

[preferences.vision]
prefer_scene_mood = ["安静", "夜晚", "居家"]
```

### 2. `assets/`

角色自己的资源包：

- 场景
- 服装
- 表情
- 音乐
- 可选桌宠立绘

### 3. `notes/`

主创补充的：

- 世界说明
- 场景说明
- 角色禁区
- 特殊事件说明

### 4. `overrides/`

用于接管自动生成的关键内容：

- 关键场景视觉观察
- 关键礼物描述
- 特殊命名
- 关键剧情资源解释

### 5. `policies/`

未来角色可配置策略入口：

- 礼物偏好策略
- 命名策略
- 集合归档策略
- 场景切换偏好
- 换装偏好

注意：

这里的 `policy` 应该是配置与规则，不是 Python 代码。

---

## 六、引擎层应包含什么

### 1. `Identity & Session`

负责：

- `profile_user_id`
- `session_id`
- 会话隔离
- 当前焦点礼物

这层永远不关心角色叫 Akane 还是别的名字。

### 2. `Memory Engine`

负责：

- 原始对话
- 摘要层
- 语义层
- 检索
- 记忆压缩

它只处理“如何记”，不处理“角色该用什么语气记得”。

### 3. `Prompt Pipeline`

负责：

- 路由
- 校验
- 最终上下文组装

它应该接收：

- 角色包给出的 persona 配置
- 当前会话上下文
- 当前视觉上下文
- 礼物与工具上下文

而不是在代码里写死某个角色的表达方式。

### 4. `Resource Protocol`

负责：

- 场景对象协议
- outfit 对象协议
- emotion 对象协议
- BGM 对象协议
- 运行时资源投影

它是“角色世界如何被系统理解”的底层规范。

### 5. `Gift Engine`

负责：

- 礼物状态机
- 礼物处理器
- 运行时投影
- 手边 / 库存 / 焦点逻辑

它不该知道“Akane 会不会把图叫窗边黄昏”，只该知道：

- 这是图片
- 当前状态是 `pending`
- `internalized` 会投影成场景候选

### 6. `Vision Engine`

负责：

- 场景观察
- 礼物观察
- 服装观察
- 缓存与 override

它不负责扮演角色，只负责提供稳定视觉证据。

### 7. `Tool Protocol`

负责：

- 工具 schema
- handler 生命周期
- 执行上下文

角色只决定“什么时候想用工具”，引擎决定“工具怎么执行”。

### 8. `Shell Layer`

负责：

- Web 壳
- 未来桌宠壳
- 设置面板
- 手边胶囊
- 资源拖放

Shell 层应该尽量做“可换皮”，不要把 Akane 写死在壳里。

---

## 七、真正要拆的不是代码文件，而是 4 类责任

未来所有新能力，都应该先问它属于哪一层。

### 1. `角色是什么`

属于作品层。

例子：

- 她的名字
- 她的口吻
- 她给图片起名的审美
- 她偏爱的收藏分类

### 2. `系统如何运行`

属于引擎层。

例子：

- 礼物状态机
- 视觉缓存
- session 隔离
- tool call 校验

### 3. `资源如何组织`

属于资源协议层。

例子：

- 背景有哪些字段
- outfit 如何挂 emotions
- 图片礼物如何投影成场景

### 4. `UI 如何呈现`

属于壳层。

例子：

- 手边胶囊
- 礼物库
- 桌宠悬浮层
- 舞台模式切换

这 4 类责任不分清，后面一定会重新缠成一团。

---

## 八、从现在到“零代码造角色”，最关键的 5 个阶段

### Phase 1：定义角色包协议

目标：

- 让角色有正式入口，不再默认绑定到单一 persona 文件。

要做的事：

- 把 `persona_profiles.toml` 抽成角色包协议的一部分
- 定义 `character.toml` 最小 schema
- 让引擎通过“加载角色包”而不是“加载 Akane 配置”启动

这是第一优先级。

### Phase 2：把 Akane 专属内容迁出引擎默认层

目标：

- 把 Akane 从“代码默认值”变成“第一个角色包”。

要做的事：

- 整理 Akane 的 persona
- 整理 Akane 的资源和 note
- 把关键 override 收进角色包
- 收敛前端中写死的名字和默认文案

### Phase 3：把策略变成配置，不再写死在服务层

目标：

- 命名偏好、礼物归档偏好、换装偏好，逐步从 Python if-else 抽成角色策略配置。

例子：

- 某角色更喜欢把图片归进“回忆”
- 某角色对音乐更倾向 `keep`
- 某角色更常在夜晚切居家服

### Phase 4：做作者工作台

目标：

- 非程序员也能造角色。

最低可用作者流程：

1. 选择角色模板
2. 填角色卡
3. 导入场景 / 服装 / 音乐
4. 填几条礼物偏好和世界观说明
5. 预览并导出

### Phase 5：角色打包与发布

目标：

- 角色可以独立分发。

未来理想形态：

- 一个角色包就是一个目录或压缩包
- 可导入
- 可校验
- 可预览
- 可分享

---

## 九、下一阶段最值得做的，不是“更多功能”，而是这三刀

### 1. `Character Package Schema`

最该先做。

如果没有角色包协议，后面一切平台化都会继续依附在 Akane 身上。

### 2. `Policy Extraction`

把目前这些逐步抽成角色可配置策略：

- 礼物命名偏好
- 图片归档偏好
- 默认收藏集合
- 换装偏好

### 3. `Shell De-branding`

把当前 UI 壳里的单角色默认词汇抽掉，换成来自角色包的：

- 角色名
- 用户称呼
- 默认问候语
- 某些界面文案

这一步一做，项目会立刻从“Akane 应用”更像“角色引擎 + 当前角色实例”。

---

## 十、反过来，哪些东西现在不要急着做

- 不要急着做复杂角色商店
- 不要急着做多角色 marketplace
- 不要急着做 GUI 作者平台
- 不要急着做任意脚本插件系统

现在真正值钱的是：

> 先把“一个角色包如何驱动整套引擎”定义干净。

如果这一步没做好，后面平台化只会放大混乱。

---

## 十一、最终判断

你的项目现在有两个身份：

- 表面上，它是 Akane 的陪伴项目
- 更深一层，它已经在长成一个角色存在引擎

真正的升级不是“再做一个新功能”，而是：

> 从现在开始，所有新功能都要问一句：它属于 Akane，还是属于任何未来角色都能继承的引擎能力？

只要这条守住，你离“以后几乎不用改代码，就能造出一个属于自己的角色”就会越来越近。

一句话收束：

> Akane 应该成为这套系统的第一个角色，而不是最后一个必须手写的角色。
