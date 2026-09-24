# 图片礼物投影方案 V1

## 目标

图片礼物进入系统后，不直接等价于“文件上传成功”，而是分成两层：

- 物理层：原始文件统一存放在 `user_assets/<profile>/images/`
- 语义层：Akane 只管理图片的命名、归档与投影用途

这样可以避免让大模型直接操作真实目录结构，同时保留角色感。

## 核心元数据

图片礼物的 `payload` 当前预留这些字段：

- `display_name_source`
- `collection_key`
- `collection_name`
- `projection_role`
- `keep_projection_role`
- `internalize_projection_role`

V1 默认值：

- `collection_key = memories`
- `collection_name = 回忆`
- `keep_projection_role = photo`
- `internalize_projection_role = scene`

这意味着：

- 用户说“先留着”时，这张图默认被归入回忆相册
- 用户说“吃掉”时，这张图默认被投影成 Akane 自己可切换的场景

## 投影角色

V1 只正式启用两个角色：

- `photo`
  代表留在相册/回忆盒里，不进入场景池
- `scene`
  代表被内化成可用背景，进入运行时场景池

后续可扩展：

- `widget`
- `card`
- `prop`

## 运行时场景池

当图片礼物被 `internalize` 且 `projection_role = scene` 时，
它会被投影进一个运行时场景组：

- `major.id = gift_gallery`
- `major.name = 私人收藏`

并按 `collection_key` 分配到不同的 minor 下。

V1 的默认 minor 是：

- `minor.id = memories`
- `minor.name = 回忆`

这样 Akane 未来可以自然地把图片归到不同的“集合”里，而不需要真的创建磁盘文件夹。

## 与视觉系统的关系

图片礼物上传后，会优先进入“手边”状态，并异步触发礼物视觉观察。

V1 现在已经接上第一层自动整理：

1. 礼物观察卡 ready 后，系统会提取 `vision_summary / vision_entities / vision_mood_tags`
2. 如果图片当前还保持默认文件名风格，Akane 会自动给它起一个更像“相册标题”的显示名
3. 如果图片当前还在默认集合里，Akane 会自动给它分配一个更合适的 `collection_key / collection_name`

自动命名和归档的结果，会回写到礼物自身的元数据里，但不会改动原始文件名。

这意味着：

- 底层文件依然稳定
- 表层显示名和相册归类开始拥有 Akane 自己的语气和判断

## 当前边界

V1 已实现：

- 图片礼物上传
- `keep / internalize` 对应不同投影语义
- 内化图片进入运行时场景池
- 前端礼物库支持图片预览

V1 暂未实现：

- Akane 自动重命名图片
- Akane 自动新建集合
- 图片的 `widget/card/prop` 投影
- 服装视觉观察卡

一句话总结：

V1 的图片礼物不是“把文件塞进场景目录”，而是让 Akane 开始拥有自己的相册与私人场景库。
