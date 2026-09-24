# 最小静态插件市场 V1

市场只负责分发，不参与插件运行。实际暂存、权限审批、原子激活、last-good、停用和卸载都由已有扩展管理服务负责。

## 首个真实发行目录

仓库维护 `plugins/market.toml` 中的源包及人类可读说明。先在提供当前公开 SDK 和 setuptools 的开发环境构建：

```powershell
python scripts/build_plugin_market.py
```

构建脚本复制源码到临时目录，以 `pip wheel --no-deps --no-index --no-build-isolation` 构建真实 wheel，再写入 `.plugin-market/<sha256>/<wheel-name>`，最后原子更新 `.plugin-market/index.json`。wheel 和生成索引不提交 Git；源码目录不产生 build/egg-info。构建失败保留旧索引，不覆盖不同内容的已发行 hash 路径。

默认 Bot 从仓库 `.plugin-market/index.json` 读取。这是可浏览、选择、校验后进入安装流程的本地发行目录，不是“输入一个源码路径”的安装入口。未构建时返回 `market_index_unavailable`，UI 显示具体缺失状态，不生成假条目。

也可以把完整输出目录作为静态文件发布到受信 HTTPS 服务器，再设置 `AKANE_PLUGIN_MARKET_INDEX` 为那个真实 `index.json` 地址，重启宿主。当前任务未发布公网目录、未部署云端；没有默认虚构下载地址。UI 明确区分“本地发行目录”和“HTTPS 目录”。

管理员也可以设置索引的本地完整路径。这个配置是启动级可信来源，不允许模型临时提交下载 URL 来替换源。HTTPS 目录与相对 wheel 地址同源，不使用 URL 凭据、查询参数或重定向；需要 CDN 时配置实际最终目录地址。市场客户端不读取运行秘密。

## 用户与模型的安装流程

1. 控制中心唯一设置实现 `control-center-lab.html` 的“能力 → 服务与扩展 → 可选插件市场”浏览条目，查看版本、体积、运行依赖、声明权限与 SHA-256。
2. 点击“获取并检查”：请求携带浏览到的 plugin_id 与精确 hash；索引已变则要求刷新。字节大小与 hash 均校验通过后才进入既有隔离探测。
3. 待确认候选在共享区域显示实际贡献与权限。市场声明的 id、版本、权限、hash 必须与真实探测一致，否则丢弃候选。
4. “确认权限并安装”复用现有安装操作；启用、停用、回滚、卸载仍在同一已安装插件卡片中进行。

模型使用同一 `manage_extension`：`market` 返回目录；`stage_market` 传 `plugin_id` 和 `digest`（条目 `sha256`）；`install` 传暂存 `stage_id` 与完整 `approved_permissions`。没有新的市场执行工具或转换代理。现有主人/桌宠授权与操作审批仍生效，浏览本身不触发修改审批。

条目即使存在也不代表已安装或运行依赖满足。市场代码是管理员选择信任的 Python 代码，哈希只是完整性及选择绑定，不是安全沙箱或作者认证。暂存探测会运行插件，用户应先信任来源，再获取检查。

## 依赖交付

第一项 `akane.media-convert` 需要 Python 3.11+、宿主公共 SDK、FFmpeg 和 FFprobe。wheel 不捆绑外部二进制，不联网补齐 Python 包、不代装系统软件。插件健康检查实际执行二进制，缺少时给出 `ffmpeg_not_found` / `ffprobe_not_found`；编码器能力在转换中真实验证。不为尚未交付的 GPU/模型依赖预留运行时状态。

## 验证与边界

- 真实构建目录 → 浏览 → 校验 → 既有暂存 → 权限确认 → 隔离运行转换 → 停用/启用 → 卸载：`python -m unittest tests.test_plugin_market tests.test_media_convert_plugin -q`。
- 篡改、大小不符、路径逃逸、失效选择、权限声明不符、缺失依赖与取消后的候选清理均有测试。HTTPS 请求路径用替身验证，不等同已验收公网托管。
- 前端 smoke 覆盖生产动作路由、目录空态/失败/等待、重复触发、慢目录不阻塞主视图、切换来源后迟到目录不污染新 Bot、停止后不发布结果。
- 浏览器在临时隔离实例上查看真实 API 与生产视图的市场条目、待确认候选和权限区；不是对真实用户实例执行安装。
- 原始输出登记和渠道交付仍走宿主生成文件链；本市场切片不声称实际 QQ 投递已验证。
