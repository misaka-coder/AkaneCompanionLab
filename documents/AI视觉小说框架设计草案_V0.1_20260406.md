# AI视觉小说框架设计草案 V0.1

## 1. 目标

本项目的目标不是只做一个会聊天的网页，而是做一个可扩展的、资源驱动的 AI 视觉小说框架。

最终期望效果：

- 有标准 galgame 式界面
- 有背景、立绘、表情、BGM、转场、对白框
- AI 负责说话和选择演出资源
- 开发者主要通过添加素材和配置扩展内容，而不是频繁改代码

本框架优先支持两种模式：

- 日常系模式
- 非日常剧情模式

两种模式共用同一套表现层和资源层，但角色脑和剧情控制层分开。

---

## 2. 核心原则

### 2.1 资源驱动，不硬编码

以后新增：

- 背景图
- 服装
- 表情
- BGM
- 子场景
- 剧情节点

应尽量通过“新增文件夹 + 新增素材 + 新增配置文件”完成，而不是修改代码。

### 2.2 分层场景，而不是一次给 AI 所有选项

场景采用层级结构：

- 大场景
- 子场景
- 背景变体

例如：

- `school`
  - `classroom`
  - `library`
  - `corridor`
- `home`
  - `bedroom`
  - `kitchen`
  - `living_room`

AI 不应每次从全体背景图里乱选，而是先在当前或允许切换的大场景里选，再进入子场景，再选该子场景下的背景变体。

### 2.3 emotion 只负责同服装差分

约定：

- `outfit` 表示服装大类
- `emotion` 表示同一服装下的表情差分

例如：

- `outfit = 校服`
- `emotion = normal / shy / smug / cry`

AI 不通过 `emotion` 换衣服。

### 2.4 表现层和剧情层解耦

表现层负责：

- 背景显示
- 立绘显示
- 表情切换
- BGM 播放
- 对话框显示
- 转场动画

剧情层负责：

- 当前章节
- 当前节点
- 当前关系状态
- 当前可进入场景
- 当前允许的选项

表现层不决定剧情，剧情层不直接碰素材路径。

---

## 3. 框架分层

### 3.1 资源层

资源层保存所有美术和音频素材。

包括：

- scenes
- characters
- bgm
- sfx
- cg

### 3.2 资源清单层

资源清单层在程序启动时自动扫描资源目录，生成 manifest。

manifest 负责描述：

- 有哪些大场景
- 每个大场景下有哪些子场景
- 每个子场景有哪些背景变体
- 有哪些服装
- 每套服装下有哪些表情
- 有哪些 BGM 和 SFX

### 3.3 状态层

状态层记录当前演出状态，例如：

- 当前大场景
- 当前子场景
- 当前背景变体
- 当前服装
- 当前表情
- 当前 BGM
- 当前转场方式

### 3.4 AI 决策层

AI 决策层不直接碰真实文件路径，只输出资源 ID。

例如：

- `scene.major = school`
- `scene.minor = classroom`
- `scene.background = evening`
- `character.outfit = 校服`
- `emotion = smug`
- `scene.bgm = school_evening`

### 3.5 前端渲染层

前端根据 AI 输出和 manifest，解析出真实资源路径并渲染。

前端负责：

- 资源切换
- 动画过场
- BGM 渐入渐出
- 打字机效果
- UI 交互

---

## 4. 资源目录规范

### 4.1 背景场景目录

推荐目录结构：

```text
web/assets/scenes/
  school/
    meta.json
    classroom/
      meta.json
      morning.png
      afternoon.png
      evening.png
      night.png
    library/
      meta.json
      morning.png
      evening.png
  home/
    meta.json
    bedroom/
      meta.json
      morning.png
      night.png
    kitchen/
      meta.json
      evening.png
```

说明：

- 一级目录是大场景
- 二级目录是子场景
- 图片文件是背景变体
- 图片文件名建议优先使用时间或固定变体名

### 4.2 角色目录

推荐目录结构：

```text
web/assets/characters/
  校服/
    meta.json
    normal.png
    shy.png
    smug.png
    cry.png
  睡衣/
    meta.json
    normal.png
    shy.png
    cry.png
```

说明：

- 一级目录是服装
- 同目录下不同 png 为表情差分
- 同一服装下的立绘必须：
  - 姿势一致
  - 站位一致
  - 画布尺寸一致

### 4.3 BGM 目录

推荐目录结构：

```text
web/assets/bgm/
  school/
    classroom/
      morning.ogg
      evening.ogg
    library/
      evening.ogg
  home/
    bedroom/
      night.ogg
    kitchen/
      evening.ogg
```

说明：

- BGM 与场景层级保持一致
- 同一子场景可按时间或变体区分音乐

### 4.4 预留目录

后续可加入：

```text
web/assets/sfx/
web/assets/cg/
```

---

## 5. meta.json 规范

### 5.1 大场景 meta.json

示例：

```json
{
  "id": "school",
  "name": "学校",
  "description": "白天活动的主要场所，包含教室、图书馆和走廊。"
}
```

### 5.2 子场景 meta.json

示例：

```json
{
  "id": "classroom",
  "name": "教室",
  "description": "上课、放学后闲聊、黄昏停留都适合出现的场景。"
}
```

### 5.3 服装 meta.json

示例：

```json
{
  "id": "校服",
  "name": "校服",
  "description": "在学校活动时最常见的穿着。",
  "allowed_emotions": ["normal", "shy", "smug", "cry"]
}
```

说明：

- `allowed_emotions` 用于帮助 AI 理解当前服装下能切哪些表情

---

## 6. 运行时资源清单 manifest

程序启动时自动扫描目录，生成运行时资源清单。

示意结构：

```json
{
  "scenes": {
    "majors": [
      {
        "id": "school",
        "name": "学校",
        "minors": [
          {
            "id": "classroom",
            "name": "教室",
            "backgrounds": [
              { "id": "morning", "path": "/assets/scenes/school/classroom/morning.png" },
              { "id": "evening", "path": "/assets/scenes/school/classroom/evening.png" }
            ],
            "bgm_tracks": [
              { "id": "morning", "path": "/assets/bgm/school/classroom/morning.ogg" },
              { "id": "evening", "path": "/assets/bgm/school/classroom/evening.ogg" }
            ]
          }
        ]
      }
    ]
  },
  "characters": {
    "outfits": [
      {
        "id": "校服",
        "emotions": [
          { "id": "normal", "path": "/assets/characters/校服/normal.png" },
          { "id": "shy", "path": "/assets/characters/校服/shy.png" }
        ]
      }
    ]
  }
}
```

manifest 的作用：

- 前端不再自己猜路径
- AI 不直接碰真实路径
- 只通过资源 ID 完成选择

---

## 7. AI 输出 JSON 协议

### 7.1 最小协议

```json
{
  "thought": "主人刚刚和我打招呼，我应该自然回应。",
  "memory_tags": "",
  "speech": "喵呜，主人，欢迎回来呀。",
  "status": "final",
  "emotion": "normal",
  "score": 0.0,
  "tool_call": null,
  "character": {
    "outfit": "校服"
  },
  "scene": {
    "major": "school",
    "minor": "classroom",
    "background": "evening",
    "bgm": "evening"
  }
}
```

### 7.2 字段职责

- `speech`
  - 最终显示在底部对话框中的台词
- `emotion`
  - 同一服装下的表情差分
- `character.outfit`
  - 服装大类
- `scene.major`
  - 场景大类
- `scene.minor`
  - 子场景
- `scene.background`
  - 当前子场景下的背景变体
- `scene.bgm`
  - 当前子场景下的 BGM 变体

### 7.3 AI 不能做的事

- 不能输出 manifest 中不存在的资源 ID
- 不能直接输出真实文件路径
- 不能用 `emotion` 代替 `outfit`

---

## 8. 动画和转场框架

动画和过场必须框架化，不能每次写死。

### 8.1 背景转场

建议支持：

- `cut`
- `fade`
- `crossfade`
- `slow_zoom`

### 8.2 立绘转场

建议支持：

- `sprite_cut`
- `sprite_fade`
- `sprite_slide_left`
- `sprite_slide_right`

### 8.3 UI 转场

建议支持：

- `typewriter`
- `instant`
- `dialogue_fade`

### 8.4 BGM 转场

建议支持：

- `bgm_keep`
- `bgm_fade`
- `bgm_restart`

### 8.5 转场协议示例

```json
{
  "scene": {
    "major": "school",
    "minor": "classroom",
    "background": "evening",
    "bgm": "evening",
    "transition": "fade"
  },
  "character": {
    "outfit": "校服",
    "transition": "sprite_fade"
  },
  "ui": {
    "dialogue_transition": "typewriter"
  }
}
```

---

## 9. galgame 标准元素

本框架后续应包含以下常见要素：

- 底部对话框
- 名字框
- 打字机效果
- 下一句
- 自动播放
- 快进/跳过
- 历史回看
- 选项分支
- 存档/读档
- BGM/SFX 控制
- 场景和角色转场

这些功能应该是框架层能力，不应和具体角色或具体剧情强绑定。

---

## 10. 日常系模式

日常系模式特点：

- AI 自由度较高
- 更像陪伴和闲聊
- 场景切换可以自然一些
- 重点是氛围、关系感和生活感

推荐：

- 使用记忆系统
- 使用时间感
- 使用背景/服装/表情/BGM 进行轻演出

---

## 11. 非日常剧情模式

非日常剧情模式特点：

- AI 自由度较低
- 更重剧情和设定一致性
- 场景切换要受剧情节点控制
- 适合历史感、冒险感、世界观强约束的作品

推荐增加：

- 章节系统
- 节点状态机
- 事件条件
- 分支选项
- 原作角色一致性校验

### 11.1 剧情节点示例

```json
{
  "id": "chapter1_arrival",
  "background": "evening",
  "major_scene": "school",
  "minor_scene": "classroom",
  "bgm": "evening",
  "default_outfit": "校服",
  "default_emotion": "normal",
  "choices": [
    {
      "text": "继续前进",
      "target": "chapter1_road"
    },
    {
      "text": "先停下来观察",
      "target": "chapter1_observe"
    }
  ]
}
```

---

## 12. 当前阶段实现目标

### 12.1 当前阶段应该先做成的

- 资源目录规范固定
- 启动时自动扫描资源
- 生成 manifest
- AI 输出资源 ID
- 前端根据 manifest 切背景、立绘、表情、BGM
- 保证资源不变时不重复加载

### 12.2 当前阶段先不追求的

- 复杂 Live2D
- 复杂战斗系统
- 复杂开放世界探索
- 全量自动剧情生成

---

## 13. 一句话原则

先做一个“可扩展的 AI 视觉小说框架”，再往里面不断填素材、剧情和角色，而不是每次为了新素材去改代码。

