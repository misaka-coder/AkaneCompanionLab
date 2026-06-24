# Artifact Container System V1

## 1. 一句话目标

先把“Akane 拥有什么、这些东西待在哪里、以后怎么被拿出来”做成稳定语义层。

这份方案的重点不是复杂可视化，而是：

- 让音频 / 图片 / 文字类资产有明确容器归属
- 让容器成为角色世界的一部分，而不是单纯文件列表
- 保证未来能扩到房间摆件、桌宠抽屉、相框墙、Live2D 小挂件，而不推翻底层

---

## 2. 当前阶段结论

当前最适合先做的不是：

- 虚拟礼物形象化
- 房间自由摆件
- 场景变体生成

而是先做：

- `曲库`
- `相册`
- `便签盒`
- `收藏盒`

也就是一套稳定的 **Artifact Container System**。

这一步的本质是：

**先做“存在方式”，再做“表现方式”。**

---

## 3. 设计原则

### 3.1 容器是语义层，不是页面名

底层保存的是：

- `music_box`
- `album`
- `note_box`
- `keepsake_box`

而不是：

- `music_tab`
- `photo_page`
- `settings_album_panel`

这样以后前端怎么换都不影响底层。

### 3.2 容器不等于投影

同一个 artifact 可以：

- 属于 `album`
- 同时具备 `scene` 投影能力

例如：

- 一张图被 Akane 收进相册
- 同时又被她内化成自己的场景候选

所以：

- `container` 负责回答“它待在哪里”
- `projection_role` 负责回答“它能干什么”

### 3.3 先复用现有礼物底座

V1 不建议立刻新建一套完全独立的 artifact 表。

当前已有：

- `user_media_assets` 表
- `gift_system`
- `projection`
- `vision_service`

这些已经足够支撑 V1。

所以更稳的路线是：

- 先把 `user_media_assets` 扩成 artifact 的当前真相层
- 通过新字段和 service facade 提供“容器视角”
- 以后如果真的需要独立 artifact repository，再平滑拆分

### 3.4 先做归档与读取，不做复杂陈列

V1 只解决：

- 归属
- 检索
- 展示入口
- 容器内排序
- 与对话 / 资源投影的关系

V1 不解决：

- 房间摆放
- 自动生图
- 抠图
- 空间槽位
- 复杂拖拽交互

---

## 4. V1 范围

### 4.1 支持的容器

- `music_box`
  - 用于音频礼物
- `album`
  - 用于图片礼物
- `note_box`
  - 预留给文字便签 / 短句 / 信件类
- `keepsake_box`
  - 兜底收藏盒，放暂时不细分但不该消失的资产

### 4.2 V1 实际落地的资产类型

当前优先接入：

- `audio`
- `image`

预留但暂不完全落地：

- `text`
- `note`
- `virtual`

### 4.3 V1 非目标

以下内容暂不纳入本期：

- 虚拟礼物可视化
- 房间成长
- 场景变体生成
- 礼物挂墙 / 摆桌 / 挂窗
- 文本礼物富文本编辑器

---

## 5. 核心概念

### 5.1 Artifact

指进入 Akane 世界、可被保留和再次取出的对象。

在 V1 中，artifact 暂时直接复用现有 gift asset 记录。

### 5.2 Container

指 artifact 当前所属的语义容器。

V1 只定义一级容器，不做嵌套层级。

### 5.3 Projection

指 artifact 在运行时资源层的额外能力。

例如：

- 音频 artifact 的 `bgm` 投影
- 图片 artifact 的 `scene` 投影

---

## 6. 数据模型建议

## 6.1 现有表复用策略

继续复用：

- `user_media_assets`

建议新增字段：

- `container_type`
  - `music_box | album | note_box | keepsake_box`
- `container_key`
  - 容器内集合或子分类的稳定 key
- `container_name`
  - 给前端展示的容器分组名
- `artifact_flags_json`
  - 预留扩展标志位

### 6.2 字段职责

- `status`
  - 礼物生命周期真相
- `container_type`
  - 语义归属
- `payload_json`
  - 类型相关原始与业务元数据
- `projection_role`
  - 继续放在 `payload_json` 内即可，V1 不强制提列

### 6.3 为什么不新建独立 artifact 表

因为 V1 的目标不是重构资产底座，而是先把容器语义做成立。

直接新开表会带来：

- 双份同步逻辑
- 迁移复杂度
- 不必要的实现成本

当前复用现有表更稳。

---

## 7. 容器分配规则

### 7.1 音频

- `pending`
  - 暂不进入容器主视图
- `kept`
  - 可选仍归 `music_box`
- `internalized`
  - 进入 `music_box`

V1 建议：

- 音频以 `internalized` 作为正式进入曲库的条件
- 如果后续想做“收下但不吃掉的歌匣”，再扩 `kept -> music_box` 的展示

### 7.2 图片

- `kept`
  - 进入 `album`
- `internalized`
  - 仍进入 `album`
  - 同时继续保留 `scene` 投影能力

也就是说：

- 相册是归属
- 场景候选是能力

### 7.3 文字 / 便签

V1 先预留：

- `kept` -> `note_box`

但本期不强行实现上传与编辑链路。

### 7.4 兜底规则

无法明确分流的资产，统一先进入：

- `keepsake_box`

---

## 8. Service 设计

建议新增一层 facade：

- `companion_v01/artifact_system/service.py`

它不取代 `gift_system`，而是站在容器视角上读取和组织资产。

### 8.1 建议职责

- 查询指定容器的 artifact 列表
- 生成容器概览摘要
- 统一处理容器排序与过滤
- 供前端直接请求

### 8.2 与 `gift_system` 的关系

- `gift_system`
  - 负责礼物进入、状态迁移、投影
- `artifact_system`
  - 负责归档容器视图

两者关系可以理解为：

- `gift_system` 是生命周期服务
- `artifact_system` 是世界拥有感服务

---

## 9. 后端接入点

### 9.1 `store.py`

需要做的事：

- 给 `user_media_assets` 增加容器字段
- 给已有音频 / 图片资产补默认容器迁移规则
- 增加按容器查询接口

建议新增方法：

- `list_artifacts_by_container(...)`
- `count_artifacts_by_container(...)`
- `summarize_artifact_containers(...)`

### 9.2 `gift_system/service.py`

需要做的事：

- 在上传 / keep / internalize 后同步刷新容器字段
- 统一容器决策逻辑

建议新增内部方法：

- `_resolve_container_assignment(asset, action)`
- `_sync_container_metadata(...)`

### 9.3 `engine.py`

需要做的事：

- 暴露容器查询接口
- 为后续 prompt 注入预留“她拥有哪些容器内资产”的轻量入口

### 9.4 `app.py`

建议新增接口：

- `GET /artifacts/containers`
  - 返回容器概览
- `GET /artifacts/container`
  - 返回某个容器的具体列表

---

## 10. 前端 V1 方案

### 10.1 目标

前端 V1 的目标不是替换礼物库，而是新增一层“她的世界”入口。

### 10.2 推荐展示

设置栏内新增容器区块：

- 曲库
- 相册
- 便签盒
- 收藏盒

每个容器先支持：

- 容器名
- 总数
- 最近几项
- 点开查看完整列表

### 10.3 为什么不立刻替换礼物库

因为礼物库和容器不是一个东西：

- 礼物库强调“最近收到与处理”
- 容器强调“她已经拥有并归档”

两者应该并存。

---

## 11. Prompt 与角色体验

V1 不要求把完整容器列表塞进 prompt。

建议只在这些场景下注入：

- 用户明确问“你曲库里有什么”
- 用户提到某张旧图、某首旧歌
- Akane 主动回忆她拥有的某类东西

建议注入形式保持轻量：

- `她的曲库里最近有 3 首主人送她的歌`
- `她的相册里保留着几张被她命名过的图`

不要把容器系统又做成新的长列表噪音源。

---

## 12. 与未来方向的兼容性

### 12.1 对房间成长兼容

以后如果要做房间状态：

- 不是从 gift 表直接推房间
- 而是从 artifact container / artifact metadata 再投影一层

### 12.2 对虚拟礼物兼容

以后虚拟礼物可以直接进入：

- `keepsake_box`
- `note_box`
- `wardrobe_candidate`
- `room_candidate`

无需推翻 V1 容器语义。

### 12.3 对桌宠 / Live2D 兼容

未来这些容器既可以显示在设置栏，也可以显示成：

- 桌边抽屉
- 床头相册
- 曲库按钮
- 角色收藏栏

容器语义不需要变化。

---

## 13. 实施顺序

### Phase 1

- 扩 `user_media_assets` 容器字段
- 做默认迁移
- 在 gift action 后同步容器归属

### Phase 2

- 实现 `artifact_system/service.py`
- 提供容器概览与列表接口

### Phase 3

- 前端加入容器入口
- 先做曲库与相册

### Phase 4

- 视情况再开便签盒与收藏盒

---

## 14. 当前建议

如果只做第一批最值的内容，建议顺序是：

1. `music_box`
2. `album`
3. 容器概览接口
4. 前端入口

等这条跑顺之后，再开：

- 音频移除能力
- 便签盒
- 虚拟礼物语义入场

---

## 15. 一句收束

Artifact Container System V1 的目标不是把 Akane 做成文件管理器，而是：

**让她开始拥有自己的曲库、相册和收藏方式。**

这一步一旦做稳，后面的房间、桌宠、相框墙、场景成长，才会真的有根。

