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
4. 连接后会出现 1-4 菜单：
   1 查状态；2 个人号；3 金融号；4 两个号。
5. 如果显示 qr_ready，在 Termius 的 SFTP 中进入 qr 目录，下载 personal.png
   或 finance.png 到手机相册。
6. 打开 QQ 的“扫一扫”，选择“相册”，识别刚下载的二维码。
7. 二维码 10 分钟后自动删除。扫完重新连接，选 1 检查 connected。

二、其他 Windows 电脑

1. 把整个文件夹通过数据线或加密 U 盘带走，不要只复制 bat。
2. 双击“启动应急重登.bat”，选择 status / personal / finance / both。
3. 新二维码会下载到本文件夹的“二维码”目录并自动打开。

三、安全边界

- 这把密钥没有服务器 Shell，不能端口转发、上传文件或执行任意命令。
- 它只能查询两个 Bot、重启固定 NapCat 容器并只读下载临时二维码。
- 它读不到 Akane 记忆、模型密钥、QQ token 或部署配置。
- 如果手机丢失，请立即撤销服务器上的 akane-recovery 密钥。
- 回来后即使密钥已经到期，也应删除手机和便携文件夹里的私钥。
