# 公网资源读取的 Fake-IP 兼容修复

## 问题与范围

本机 DOVE 虚拟网卡使用 `198.18.0.2` DNS，多个公网域名被解析为 `198.18.0.0/15`
内的虚拟地址。原有公开 URL 策略按非公网地址拒绝，因此卡片标题可读，链接内容读取失败。

这次修改仅作用于 Akane 的共享附件/媒体读取链路，不修改 DOVE、全机 DNS、代理路由、
浏览器或账号凭据，也没有 B 站专属的 DNS 放行表。

## 实现

- 系统 DNS 正常返回公网地址时维持原结果；只在出现 Fake-IP 且没有混入真实私网地址时，
  使用 Cloudflare DoH 重新解析域名。直接输入虚拟 IP 的 URL 不触发回退。
- DoH 使用 TLS 验证的数字地址引导，不依赖被接管的系统 DNS；只传域名和记录类型，
  不传链接路径、查询参数、Cookie、对话或账号密钥。禁止重定向和环境代理。
- A/AAAA 都检查；验证问题记录及 CNAME 链，有界读取、有超时和最多 128 个域名的 TTL 缓存。
  DoH 返回的私网或混合私网地址仍被原策略拒绝。失败结构化返回，不把虚拟地址当公网。
- `PUBLIC_URL_DNS_FALLBACK_ENABLED` 默认开启；设为 false 可禁用这项 DoH 兼容，
  返回明确的 `remote_url_synthetic_dns_address`，并不会允许访问私网。
- 公共请求使用请求级 IP 固定连接，同时保留原始 URL、Host、TLS SNI、证书域名校验。
  HTTP 请求发送前验证实际连接地址；重定向重新验证新目标，不修改进程级 DNS 函数。
- 对连接提前关闭的跳转，在连接建立时保存已验证的地址证明；没有证明或目标不匹配仍拒绝。
- 直链下载、公开 JSON 获取、短链跳转共用这一实现，原手写 socket 跳转逻辑已删除。
  既有 yt-dlp 平台/提取器限制不变，其内部网络栈没有替换成新的 HTTP 适配器。
- 修正相关失败说明：公网解析兼容失败属于访问环境问题，不要求用户把正常链接换成“公开链接”。

## 验证

- 相关回归共 244 项通过；新增网络兼容测试 21 项。Ruff、py_compile、git diff --check 通过。
- 测试覆盖多域名 Fake-IP、普通 DNS、混合私网、字面量、IPv6、CNAME、TTL、错误应答、
  禁用开关、TLS/SNI/Host、错误目标、代理、关闭证书验证、连接地址不匹配、连接关闭后的验证、
  公网重定向下载、私网跳转拒绝和有界网络重试。未用真实内网服务进行试探。
- 多站点真实验证：Example、Python 官网均返回 HTTP 200，连接地址验证通过，
  并通过实际附件下载方法原子写入 559/537 字节，未残留 `.part` 文件。
- 用户的 B 站短链返回 HTTP 302，连接地址验证通过；后续 B 站元数据接口返回 HTTP 412。
  这说明 Fake-IP 误拦已解除，但不代表该视频内容已读取；没有绕过平台访问限制。
- 本轮没有下载整段视频、发送 QQ 消息或调用模型分析视频。
- 已重启本地 Akane 后端，健康检查正常、QQ 自检连接成功；未重启桌宠或 NapCat，
  未修改云端部署、DOVE 配置或全机网络设置。

## 仍有的边界

兼容识别限于已确认的 IPv4 Fake-IP 段，不把其他私有地址范围猜成代理。
DoH 服务不可达、站点鉴权、限流、地域限制或防爬响应仍会真实失败。
DNS 兼容不等于所有平台都可下载，也不会取消既有 provider allowlist。

## 参考

- [Mihomo DNS/Fake-IP 配置](https://wiki.metacubex.one/config/dns/)
- [Cloudflare DoH JSON 契约](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)
- [urllib3 自定义 SNI 与证书主机名](https://urllib3.readthedocs.io/en/stable/advanced-usage.html)
