# Akane 云端 Bot 连续性部署 V1

状态：可执行首版流程（2026-07-16）

这条流程解决一个具体问题：把当前 `local-default` 的角色包和正式记忆迁到一个新的云端命名实例，让新 QQ Bot 延续同一位 Akane，而不是只启动一套空白基础服务。

## 1. 首版边界

- 云端是唯一正式 Akane 后端，持有会话、主记忆库、Memcore、Care、选定角色包和 QQ 通道状态。
- 一个 QQ Bot 只绑定一个 Akane 实例，不能让本地和云端同时消费同一个 Bot。
- 桌面端一次只绑定一个实例；云端模式下它不启动第二个本地后端。
- 当前 Desktop Satellite 已真实闭环的跨云本地动作是 `open_browser`。本地浏览器控制、GPT-SoVITS、Whisper、RVC 和媒体工作流的完整迁移仍属于 M66-E，不能把它们当成已经接通。
- 金融能力不是这次数据包的一部分。它继续作为独立 allowlist 插件开发和部署，不进入普通 Akane 的云端数据包或插件状态。

## 2. 搬什么，不搬什么

默认 `core-continuity` 数据包包含：

- `akane_memory_v01.db`
- `memcore_v01.db`（存在时）
- Care 状态、NPC 记忆和用户资产（存在时）
- `workspace/Inbox|Outputs|Archive`（存在时）
- Manifest 选中的默认角色，以及 allowlist 中可切换的角色包，但不含角色包 `_local`
- 新实例的 `instance.toml` 和 `instance-binding.json`

默认明确排除：

- `.env`、模型服务保存配置和所有密钥
- Chroma 派生索引（服务器启动后重建）
- 日志、缓存、运行锁
- 旧 QQ gateway 状态和旧 Bot 的去重/投递状态
- 本地插件状态，尤其不把金融插件状态混入普通 Bot
- 历史附件、生成文件和生成工作目录的大体积二进制

历史文件以后可以按需做第二批只读归档迁移；它们不应阻塞 Bot 先恢复角色和记忆。

## 3. 在 Windows 本机生成数据包

先停止当前 Akane 后端，确保 SQLite 没有 `-wal` 文件。金融插件源码开发可以继续，只有本地 Akane 进程需要在最终快照期间停下。

在仓库根目录执行：

```powershell
python scripts/prepare_akane_cloud_bundle.py `
  --source-root F:\Akane\AkaneData `
  --output-dir F:\Akane\CloudBundles\personal `
  --instance-id personal `
  --character-pack-id akane_v1 `
  --include-character-pack reimu `
  --include-character-pack uuz `
  --qq-profile-ref qq.personal `
  --port 10001 `
  --archive F:\Akane\CloudBundles\personal.tar.gz
```

输出目录和压缩包必须事先不存在。工具会拒绝运行中的源实例、不干净的 SQLite、缺失角色包、路径软链接、目标覆盖和实例绑定不一致。

生成后再次验证：

```powershell
python scripts/verify_akane_cloud_bundle.py `
  --bundle-dir F:\Akane\CloudBundles\personal
```

预期状态是 `valid`，并能看到聊天消息、会话、摘要和 Memcore 消息计数。不要只根据压缩包是否生成判断成功。

## 4. 上传与服务器安装

先上传以下三个文件：

- `personal.tar.gz`
- `personal.tar.gz.sha256`
- 与数据包相同提交版本的 Akane 源码

在服务器验证并解包：

```bash
sha256sum -c personal.tar.gz.sha256
mkdir -p /tmp/akane-personal-import
tar -xzf personal.tar.gz -C /tmp/akane-personal-import
cd /opt/akane/AkaneCompanionLab
.venv/bin/python scripts/verify_akane_cloud_bundle.py \
  --bundle-dir /tmp/akane-personal-import/personal
```

首次安装时，`/var/lib/akane/personal` 必须不存在：

```bash
sudo install -d -o akane -g akane /var/lib/akane
sudo mv /tmp/akane-personal-import/personal/data /var/lib/akane/personal
sudo chown -R akane:akane /var/lib/akane/personal
```

不要把数据包覆盖到正在运行的实例根。升级或回滚使用新目录、停机切换和备份，不做原地合并。

## 5. 配置命名实例

把数据包内的 `instance.env.example` 复制到：

```text
/etc/akane/instances/personal.env
```

权限设为 `root:akane`、`0640`，并只在服务器填写密钥。关键规则：

- `AKANE_INSTANCE_ID=personal`
- `AKANE_DATA_ROOT=/var/lib/akane/personal`
- `QQ_CHANNEL_PROFILE_REF=qq.personal`，必须逐字匹配 Manifest
- `QQ_BOT_QQ` 使用新 Bot 账号
- `MASTER_QQ` 使用原主人的 QQ 号；这样私聊仍映射为 `master`，能接上原主人的记忆身份
- `AKANE_ADMIN_TOKEN`、Satellite、Webhook、OneBot 和模型 token 各自独立
- 默认角色包由命名实例 Manifest 选择，不再在环境文件中重复写 `QQ_CHARACTER_PACK_ID`；本次数据包同时携带 `akane_v1`、`reimu`、`uuz` 三个 V0.2 角色包供会话切换

安装 `deploy/systemd/akane@.service.example` 为 `/etc/systemd/system/akane@.service` 后启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now akane@personal
sudo journalctl -u akane@personal -n 100 --no-pager
curl -fsS http://127.0.0.1:10001/health
```

健康响应必须精确确认实例身份：

```json
{"status":"ok","instance_id":"personal","root_binding":"valid"}
```

只有这个检查通过后，才把 NapCat webhook/OneBot 接到新实例。先私聊验证角色自称、最近记忆和一条新记忆写入，再开放群聊或外部入口。

## 6. 桌面端连接云端

命名实例的桌面端使用 Cloud Satellite 模式，并要求远端 HTTPS、精确实例 ID 和独立 Satellite token。示例：

```powershell
.\start_akane_next.ps1 `
  -CloudSatellite `
  -BackendUrl https://your-akane-host.example `
  -InstanceId personal
```

如果实际启动脚本要求通过环境变量提供 token，就只在本机安全环境中设置，不写入角色包、仓库、prompt 或公开日志。远端 `/health` 实例不匹配时不得继续连接。

## 7. 验收与回滚

上线验收至少覆盖：

1. `/health` 的三个字段和实例 ID 精确匹配。
2. QQ 新 Bot 的 `self_id` 与环境绑定一致，旧 Bot 不再投递到该实例。
3. 主人私聊进入 `master` 身份，能召回迁移前记忆。
4. 默认角色为 Manifest 中的 `akane_v1`，环境变量无法悄悄覆盖它；角色列表同时可见 `reimu` 和 `uuz`。
5. 新对话和新记忆能在云端数据库落盘，重启服务后仍存在。
6. 未连接 Satellite 时模型看不到 `open_browser`；连接后才出现并真实执行。
7. 普通 Akane 实例没有自动加载金融插件或金融插件状态。

回滚时先断开 QQ ingress，再停止新实例。保留原本机数据根不动，因此可以把 Bot 绑定切回原服务；不要把云端运行后产生的新数据库反向覆盖本地原库。需要双向合并时必须另做数据合并流程。
