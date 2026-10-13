# 桌宠透明立绘与待机状态表情

范围：`desktop_pet_next` 的静态立绘。沿用现有养成权威快照、音频播放状态和互动控制器，不增加模型请求或聊天提示词。

## 角色包配置

`character.json` 中的配置示例：

```json
{
  "appearance": {
    "default_emotion": "normal",
    "music_emotion": "music",
    "resting_emotions": {
      "hungry": { "emotion": "hungry", "enter_below": 25, "recover_at": 35 },
      "tired": { "emotion": "tired", "enter_below": 25, "recover_at": 35 }
    }
  }
}
```

不配置 `resting_emotions` 的包保留默认/听歌待机。字段可以映射到其它表情 ID，阈值也可以分别设置。阈值限定在 0–100，恢复阈值不低于进入阈值。

饥饿或精力严格低于 25 时进入低状态；达到 35 时恢复。在此区间保留当前低状态，避免反复切图。两者同时低时先显示困倦，然后是饥饿、听歌和默认。只读取当前角色、当前主人会话的现有 `care_runtime` 权威快照；未连接、关闭养成、缺少数值或切换所属对象时清除低状态，不使用旧本地养成副本。

回复显示、模型请求、TTS 队列、语音输入/通话、拖拽/抛掷、点击反馈、临时表情、预览和打开的输入框/菜单优先。互动结束后沿用现有恢复入口重新判断待机表情。待机换图不保存为手动表情偏好。自动选中的图片加载失败后回退默认；刷新资源可重新尝试。

## 点击轮廓

第一次显示某个图片 URL 时读取一份最长边 144 像素的 alpha 缩略图，按连续的不透明区间生成矩形，并合并相邻相同区间；内部头发间隙保留。以当前 `object-fit: contain` 的图片区域和变换后位置映射坐标，按设备 DPI 转为 Windows 物理坐标。

轮廓缓存最多保留 32 张，不保留原图像素。缩放、布局、气泡显隐和动画结束只重算已有矩形的位置；鼠标移动不解码图片。资源目录刷新会清理旧轮廓缓存。跨域图片优先通过已加载并校验的本地角色资源 URL 读取透明度；浏览器预览使用服务器 CORS。旧不透明立绘或读取失败保留原有多边形点击目标。

启动或刷新资源时，图片 URL 更新一次 `portrait_v`，避免 WebView 沿用同名旧图片；普通换表情保持该版本，继续复用图片和轮廓缓存。按表情 ID 精确选择优先于别名，防止旧普通表情的 `music` 别名遮住新增的同名表情。

## 本次素材

塞西莉亚原有 26 个表情 ID 不变，使用已接受的透明 RGBA PNG；新增 `music`，共 27 张，均为 1024×1536。听歌差分使用内置 image_gen，参考已接受的普通/微笑立绘，保留生成 alpha。生成提示、哈希和原作素材备份在本地 `output/imagegen/cecilia-music-v1/` 与 `output/backups/`；角色包内记录在 `expression_sources.json`、`artwork_provenance.json`。

角色包目录是项目既有忽略项，素材安装到用户包与当前实例，代码提交不会隐式包含私人角色包。桌面原有 26 张透明素材目录保留为参考版本。

## 验证入口

- `node --test --test-concurrency=1 scripts/resting-expression.test.mjs scripts/portrait-hit-regions.test.mjs scripts/pet-music-controller.test.mjs`
- `node --test scripts/portrait-idle-animation.test.mjs`
- `node scripts/pet-runtime-check.mjs`
- `node scripts/pet-runtime-smoke.mjs`
- `node scripts/visual-renderer-smoke.mjs`
- `node scripts/cecilia-portraits-browser-smoke.mjs`
- `node scripts/visual-layout-browser-smoke.mjs`
- `npm run build` / `npm run tauri -- build --no-bundle`

浏览器验收运行真实 DOM、样式与控制器，模型/设备 I/O 使用隔离夹具：透明素材、明暗背景、缩放、DPI 1.5、跨域轮廓、同名覆盖后刷新、旧别名冲突、回复结束恢复、权威快照更新、双击输入、慢图竞态及坏图回退。截图保存到本地生成记录目录，不写入仓库。

## 本机验收（2026-10-05）

34 项相关单元测试、运行时边界/行为检查、浏览器立绘回归、布局回归及 Tauri release 构建通过。当前安装实例的 27 张图片与素材源逐字节一致，均为 1024×1536 RGBA，具有透明区域。

实际 Windows 桌宠在 DPI 2 下验证普通、听歌、饥饿、困倦四张立绘；分别发送主窗口 `WM_NCHITTEST`，透明区域返回 `HTTRANSPARENT`、人物区域返回 `HTCLIENT`，双击输入通过。这项返回码检查尚未验证点击交给下层其他进程。真实角色工坊加载 27 张立绘，四种预览从 Blob 读取出透明像素，未产生页面错误。截图与探测记录见本地 `output/imagegen/cecilia-music-v1/native/`。只更新并重启桌宠 GUI，后端与独立桌面执行器保留运行。

范围之外的工坊全量浏览器脚本在测试对话预检夹具的 `fetches.length` 等待处超时；没有修改该链路。本次工坊图片读取与预览使用真实安装实例单独验证。音频设备和外部播放器状态组合由现有控制器测试覆盖，未为验收发起模型请求或打断用户播放。

## 待机眨眼与听歌轻摇（2026-10-05）

首个切片只给普通待机加眨眼，并给实际播放中的听歌待机加轻摇。不新增模型字段、提示或请求，不把动画帧加入可选择的表情列表。

角色包可以配置 `appearance.idle_blink`：

```json
{
  "emotion": "normal",
  "outfit": "default",
  "asset": "animations/default/normal-blink.png",
  "regions": [
    { "x": 0.348, "y": 0.265, "width": 0.18, "height": 0.091 },
    { "x": 0.553, "y": 0.249, "width": 0.163, "height": 0.087 }
  ]
}
```

`asset` 相对角色包的 `asset_root`，使用现有路径包含检查，只接受存在的图片；前端也拒绝绝对路径和目录逃逸。`regions` 是原 PNG 上的归一化眼睛区域，须位于画布内。不同姿势需要自己的差分；本次普通姿势的闭眼图不会套用到其他 26 张立绘。

内置 image_gen 基于原普通透明立绘生成同画布的闭眼差分。原始生成图在眼睛之外也有细微变化，因此运行时仅以柔边眼睛遮罩覆盖原立绘；身体、外轮廓和点击轮廓继续来自原图。遮罩按图片实际 contain 区域对齐，缩放与布局共用原立绘的变换。图片尺寸不匹配或缺失会禁用眨眼并保留原因，不切换整个人物。

普通待机随机间隔 4.5–8.5 秒闭眼约 135 毫秒，少量双眨。听歌轻摇使用独立 CSS 层，周期 5.8 秒，最大位移约 2 像素、转角约 0.25 度；只在本地音乐或系统播放器实际播放、当前显示听歌表情且允许待机时启用。聊天、语音、拖拽、表情预览、菜单和输入框沿用同一个待机允许判定；回复和低状态优先，结束后恢复。隐藏窗口、系统减少动效设置和销毁都会停下计时。

闭眼图片按 URL 预加载并缓存（上限 8 张）；慢加载完成时核验角色/表情版本，不覆盖新的选择。听歌时最多每 200 毫秒映射一次已有透明轮廓到当前位置，不重新读取 PNG 像素；停止轻摇即停止这段计时。

生成提示、模式、原图/生成图 SHA-256、安装备份见本地 `output/imagegen/cecilia-idle-motion-v1/`。原图 SHA-256 为 `90d9395069752420f5b8c63a4e075d6cc929b967f267151f0888209b50e1c63f`，闭眼差分为 `59b6aeff50387340118ac212325473f9ba77eca80e91a23ee7fa631479bc14a0`。源包与当前安装包复制同一 PNG，保留生成 RGBA，没有重编码。

38 项相关前端测试、10 项原生立绘测试、运行时/布局与窗口物理回归、真实控制器浏览器验收和 release 构建通过。浏览器验收含实际 WAV 播放、眨眼遮罩、低状态优先、输入/减少动效暂停恢复、慢图竞态、尺寸不匹配和销毁。动态预览录自当前实际 DOM 与控制器，见本地 `idle-and-music.gif` 与 `browser/idle-and-music.webm`；音乐为隔离的静音测试文件。Windows DPI 2 下四种立绘的主窗口命中返回码与双击输入再次通过；真实窗口自动眨眼、原生素材路径、输入框暂停、减少动效及恢复亦通过，记录见本地 `native/report.json`。只更新桌宠 GUI，后端与独立设备执行器未重启。未控制或切换用户正在使用的外部播放器。

## 轮廓与拖拽复核（2026-10-05）

主立绘缩放到 1.08 倍后，头顶与两侧可能伸出 `pet-hitbox`。原生 alpha 区域覆盖这些像素，但原来图片的 `pointer-events: none` 使外沿无法把鼠标事件送到父容器。主图 `#pet-image` 改为接收鼠标事件后，事件可冒泡到既有拖拽处理；眨眼覆盖图继续忽略鼠标。真实透明立绘的浏览器回归先复现外沿事件失败，修正后验证外沿按下建立拖拽状态并调用 `move_window_by`。5 项轮廓测试、完整立绘浏览器回归、前端构建通过；这次修正尚未安装到正在运行的桌宠。

Windows 穿透仍有未完成项：运行实例的三个透明点在主窗口返回 `HTTRANSPARENT`，但 `WindowFromPoint` 仍找到属于该主窗口的跨进程 WebView 子窗口，子窗口在同一点返回 `HTCLIENT`。因此不能将主窗口的返回码检查记录为其他软件收到实际点击。微软的 [WM_NCHITTEST 文档](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-nchittest)仅保证 `HTTRANSPARENT` 向同线程下层窗口继续查找。此次只读探测没有注入桌面鼠标点击，原生跨进程穿透尚未修复或通过实际点击验收。
