# manifest 字段说明

这份文档讲运行时资源清单长什么样。

## 1. 顶层结构

示例：

```json
{
  "schema_version": 2,
  "scenes": {
    "majors": []
  },
  "characters": {
    "outfits": []
  },
  "defaults": {
    "major": "default",
    "minor": "default",
    "background": "evening",
    "bgm": "",
    "outfit": "校服",
    "emotion": "normal"
  }
}
```

## 2. scenes.majors

每个 major 结构：

```json
{
  "id": "school",
  "name": "学校",
  "description": "",
  "minors": []
}
```

## 3. minors

每个 minor 结构：

```json
{
  "id": "classroom",
  "name": "教室",
  "description": "",
  "backgrounds": [],
  "bgm_tracks": []
}
```

## 4. backgrounds

每张背景图结构：

```json
{
  "id": "evening",
  "name": "黄昏",
  "description": "",
  "path": "/assets/scenes/school/classroom/evening.png"
}
```

## 5. outfits

每套服装结构：

```json
{
  "id": "校服",
  "name": "校服",
  "description": "",
  "allowed_emotions": ["normal", "shy", "smug"],
  "emotions": []
}
```

## 6. emotions

每个表情结构：

```json
{
  "id": "normal",
  "name": "平静",
  "description": "",
  "path": "/assets/characters/校服/normal.png"
}
```

## 7. defaults

`defaults` 用来定义启动时或 AI 输出无效时的兜底资源。

当前重点：

- 默认场景优先级要稳定
- 默认服装优先级要稳定
- 默认 emotion 必须属于当前 outfit

## 8. AI 应该输出什么

AI 输出资源 ID，不输出真实路径。

示例：

```json
{
  "emotion": "normal",
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
