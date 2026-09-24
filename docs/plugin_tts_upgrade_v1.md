# TTS 插件首次升级与更换

TTS 实现由 `tts` v1 / `synthesize` 服务提供。第一方插件 id 是 `akane.tts`；
角色包中的 `provider.tts.edge` / `provider.tts.gpt_sovits.local` 是声线引擎标识，
不是插件 id。角色包、GPT endpoint、参考音频、情绪声线及 Edge 参数继续使用原配置。

## 首次安装

本次源码升级不会自动安装或授权插件。没有提供者时，发声入口返回不可用，文字仍保留；
宿主已不再构造备用 Edge/GPT 客户端。

1. 通过当前第一方市场供应发布 `plugins/akane_tts`，或在开发实例使用下面的源码 stage 入口。
   发布供应可用 `python scripts/build_plugin_market.py --output <发布目录>`；
   该工具沿现有 `plugins/market.toml` 构建制品，不修改活动实例。
2. 配置现有依赖供应，满足 `capcore-adapter-speech[edge]>=0.1,<0.2` 与公开 SDK。
   运行环境须能找到 FFmpeg；安装/健康检查不访问 Edge 或启动 GPT 模型。
3. 核对 stage 返回的权限，然后安装：`service.provide`、`resource.read`、
   `artifact.write`、`network.read`、`connection.tts.read`。安装许可与用户执行审批分别处理。

以下 PowerShell 示例使用隔离实例地址及占位路径；管理 token 从已有安全环境变量读取，
不要写进脚本或命令日志。地址、实例 id、目录应改成部署者自己核对的值。

```powershell
$ttsAdminBase = 'http://127.0.0.1:41000'
$ttsAdminHeaders = @{ 'X-Akane-Admin-Token' = $env:AKANE_ADMIN_TOKEN }
$ttsStage = Invoke-RestMethod "$ttsAdminBase/admin/plugins/stages/source" -Method Post -Headers $ttsAdminHeaders -ContentType 'application/json' -Body (@{ source_path = (Resolve-Path './plugins/akane_tts').Path } | ConvertTo-Json)
$ttsStage | Select-Object ok, stage_id, plugin_id, permissions, reason
```

核对上述声明后执行安装；不要忽略 `ok=false` 或 stage 的失败原因。

```powershell
$ttsInstalled = Invoke-RestMethod "$ttsAdminBase/admin/plugins/stages/$($ttsStage.stage_id)/install" -Method Post -Headers $ttsAdminHeaders -ContentType 'application/json' -Body (@{ approved_permissions = $ttsStage.permissions } | ConvertTo-Json)
$ttsInstalled | Select-Object ok, status, reason
Invoke-RestMethod "$ttsAdminBase/admin/plugins/status" -Headers $ttsAdminHeaders
```

市场制品用既有 `GET /admin/plugins/market` 获取摘要，再调用
`POST /admin/plugins/market/akane.tts/stage`，JSON 为 `{"digest":"已核对的 sha256"}`。
已有 wheel 也可通过 `POST /admin/plugins/stages`，JSON 为 `{"wheel_path":"本地 wheel 路径"}`。

首次安装失败时没有可用版本，会继续保留文字；已有版本升级失败时查看
`last_candidate_failure` / stage 原因，活动代次仍按现有 last-good 机制保留。
不要用旧宿主执行分支掩盖安装失败。

## 用户执行审批

新能力 id 为 `akane.tts.service.tts.v1.synthesize`。旧声线配置和旧宿主许可不会自动复制为
新插件许可。已有明确覆盖该能力的实例策略继续适用；其余调用生成当前审批系统中的请求。
可从现有审批页面或 `GET /capabilities/approval-requests` 查看对应用户的请求，按原审批流程决定。
HTTP、QQ 和实时后台发声不等待隐藏对话、不循环重试；批准后由新的请求发声。

QQ 可沿用指定所有者的声线配置，但执行审批、产物和会话仍属于当前 QQ 调用范围。
第三方插件不会自动继承第一方的安装权限、连接权限或用户许可。

## 保存绑定并激活

先安装并批准目标制品，核对准确 id、`tts` 服务版本和方法。实例选择文件位置为
该实例运行布局的 `config_dir/plugin-selections.json`；不要误改另一个实例。
下面例子中的 `demo-instance` 和路径只是占位值。安装管理链创建此文件后，先只读核对：

```powershell
python scripts/set_tts_service_binding.py --path './demo-instance/config/plugin-selections.json' --instance-id demo-instance
```

工具核对 schema 与实例 id，输出当前 TTS 绑定及文件 SHA-256，不输出其他配置。
保留这次核对的摘要，再保存目标绑定：

```powershell
python scripts/set_tts_service_binding.py --path './demo-instance/config/plugin-selections.json' --instance-id demo-instance --plugin-id akane.tts --expected-sha256 '上一步返回的64位摘要'
```

工具只改 `tts` v1，保留其他字段，使用临时文件替换，并在替换前再次核对原始字节。
`plugin_selection_conflict` 表示观察到了他人改动，应重新核对；不要盲目用新摘要覆盖。
它不是跨任意编辑器的事务锁，操作期间不要并发手工保存同一文件。
`saved` / `activated:false` 只表示配置保存成功，此时当前服务仍使用旧代次。

随后调用真实管理重载入口，不需要重启整个后端：

```powershell
$ttsReload = Invoke-RestMethod "$ttsAdminBase/admin/plugins/restart" -Method Post -Headers $ttsAdminHeaders
$ttsStatus = Invoke-RestMethod "$ttsAdminBase/admin/plugins/status" -Headers $ttsAdminHeaders
$ttsReload | Select-Object status, reason, generation, last_candidate_failure
$ttsStatus | Select-Object status, reason, generation, service_bindings, plugins
```

只有新 generation 发布成功后，尚未开始合成的请求才使用新绑定。正在合法执行的分段保持原代次；
排队中的分段在出队时取新绑定。取消或角色切换使旧命令失效，不会把它重新签给新角色。
停用、撤权则优先拒绝旧链的受保护操作。

选择文件的非敏感示例：

```json
{
  "schema_version": 1,
  "instance_id": "demo-instance",
  "overrides": {"akane.tts": true, "example.tts-tone": true},
  "service_bindings": [
    {"service_id": "tts", "version": 1, "plugin_id": "akane.tts"}
  ]
}
```

`example.tts-tone` 是第二个 SDK 示例，只输出可解码的测试音，不是可听懂的语音模型。
隔离验收已用它验证从实际 stage/install、绑定文件、管理重载和 HTTP 取音频的替换链。
生产中使用自己的 TTS 服务实现替换这个示例。

## 诊断与恢复

- 绑定非法：保存器拒绝；直接手改了非法 schema 时，重载失败，检查候选失败原因。
- 目标未安装、被停用或多个提供者没有明确绑定：服务不可用，不改用 Edge。
- 新插件待审批：查看新能力的审批请求，旧插件批准不能代替它。
- 配置或参考音频错误：修正原声线配置；新请求才重新读取。
- 合成终态未知：保留未知，不自动再次合成。同一 VoiceCore 命令只恢复可核验的原音频。
- 丢失播放完成回执：不凭空补“已播放”，不自动重播没有当前客户端归属的旧音频。

HTTP 成功响应分别提供声线引擎 `X-Akane-TTS-Provider` 和宿主签发的实际来源
`X-Akane-TTS-Service-Provider` / `X-Akane-TTS-Service-Generation`。错误响应的活动声线为空，
`X-Akane-TTS-Reason` 保留原因码。产物登记成功不表示客户端已经播放。

受控链测试、性能对照与真实桌面未验项分别见
[实施记录](plugin_tts_service_implementation_20260911.md)。
