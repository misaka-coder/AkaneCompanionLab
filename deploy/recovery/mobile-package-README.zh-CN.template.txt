Akane 手机 / 其他电脑应急重登
================================

有效期：__EXPIRY__（到期后服务器自动拒绝这把密钥）
服务器：__HOST__
用户名：akane-recovery
主机指纹：__FINGERPRINT__

一、手机使用（推荐 Termius，Android / iPhone 都可以）

1. 把本文件夹中的 akane-mobile-recovery 私钥安全导入 Termius。
   不要发到微信、QQ、邮箱或公共网盘；建议用数据线、局域网互传或系统的近距离传输。
2. 在 Termius 新建 Host：
   Address = __HOST__
   Port = 22
   Username = akane-recovery
   Key = akane-mobile-recovery
3. 第一次连接时核对主机指纹必须是：
   __FINGERPRINT__
4. 连接后会出现 1-6 菜单：
   1 只查状态；2 强制恢复个人号；3 强制恢复金融号；4 强制恢复两个号。
   5 临时给个人 Bot 开启财经能力；6 关闭个人 Bot 的财经能力。
   选 2/3/4 会重启对应 Bot，避免卡死会话被残留的账号信息误判为在线。
5. 执行结果会停留到你按回车关闭。如果显示 connected 或
   connected_after_restart，说明账号已经在线，此时不会生成二维码，
   SFTP 中的 qr 目录为空是正常的。
6. 如果显示 qr_ready，在 Termius 的 SFTP 中进入 qr 目录，下载 personal.png
   或 finance.png 到手机相册。
7. QQ 可能拒绝相册识别或长按识别登录二维码。请把二维码显示在另一台手机、
   平板或电脑上，再用需要登录的手机打开 QQ“扫一扫”，通过摄像头扫描。
   必须找朋友协助时，只发 10 分钟有效的二维码给可信的人，绝不能发私钥。
8. 二维码 10 分钟后自动删除。扫完重新连接，选 1 检查 connected。

二、金融 Bot 故障时由个人 Bot 临时接管

1. 连接后选 5，等到显示 personal_finance_enabled。
2. 把个人 Bot 拉进需要推送的群；用群主或管理员账号发送：
   /财经订阅 全部快讯
3. 金融 Bot 恢复后，建议先在群里对个人 Bot 发送：
   /财经取消 全部
   然后回到恢复菜单选 6。显示 personal_finance_disabled 后，个人 Bot 的财经
   提示和工具已经从运行时移除。
4. 5/6 都会重启统一 Akane Host，但不会删除记忆或财经数据库。正常状态始终是 6。

三、其他 Windows 电脑

1. 把整个文件夹通过数据线或加密 U 盘带走，不要只复制 bat。
2. 双击“启动应急重登.bat”，选择 status / personal / finance / both。
3. 新二维码会下载到本文件夹的“二维码”目录并自动打开。

四、安全边界

- 这把密钥没有服务器 Shell，不能端口转发、上传文件或执行任意命令。
- 它只能查询两个 Bot、重启固定 NapCat 容器、只读下载临时二维码，以及切换
  个人 Bot 内固定的 akane.finance 应急插件并重启固定的 Akane Host 服务。
- 它读不到 Akane 记忆、模型密钥、QQ token 或部署配置。
- 如果手机丢失，请立即撤销服务器上的 akane-recovery 密钥。
- 回来后即使密钥已经到期，也应删除手机和便携文件夹里的私钥。
