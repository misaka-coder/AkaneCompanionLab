# Akane 单机 VPS 部署准备包（Phase 1）

这份文档按当前项目最适合的路线来写：

- 单机 VPS
- Ubuntu 22.04
- `systemd` 守护进程
- `nginx` 反向代理
- 先做小规模外放/限量体验

目标不是一步到位做大规模生产，而是先把 Akane 稳稳地跑起来，能给别人访问。

## 当前项目为什么适合单机 VPS

Akane 当前的后端结构更适合单机部署：

- `FastAPI` 应用入口在 [launch_akane_memory_v01.py](/launch_akane_memory_v01.py)
- 关系型存储使用本地 `SQLite`，见 [store.py](/companion_v01/store.py)
- 向量索引使用本地持久化 `ChromaDB`，见 [vector_store.py](/companion_v01/vector_store.py)
- 静态前端和资源也由同一应用提供，见 [app.py](/companion_v01/app.py)

这意味着它更像“单机状态服务”，而不是适合立刻做横向扩容的云原生服务。

## 推荐配置

### 最小可试配置

- 2 vCPU
- 4 GB RAM
- 40 GB SSD
- Ubuntu 22.04 LTS

### 更稳一点的配置

- 2 vCPU
- 8 GB RAM
- 60 GB SSD

## 关于 embedding 的建议

部署时分两种：

### 方案 A：省内存优先

- 只安装 [requirements.txt](/requirements.txt)
- 不安装 [requirements-ml.txt](/requirements-ml.txt)
- `EMBEDDING_PROVIDER=auto`

这样服务器上会自动回退到哈希 embedding，最省内存，最稳。

### 方案 B：体验优先

- 安装 [requirements.txt](/requirements.txt)
- 再安装 [requirements-ml.txt](/requirements-ml.txt)
- 使用本地 HuggingFace embedding

这条路更适合 8 GB RAM。

如果你第一次外放，只想先让服务稳稳跑起来，我更建议先走方案 A。

## 你需要准备什么

### 1. 一台 Linux VPS

建议：

- Ubuntu 22.04
- 能 SSH 登录
- 记住：
  - 公网 IP
  - root 密码或 SSH key

### 2. 一个域名（可选但推荐）

不买也能先用 IP 测，但以后上 HTTPS 更方便。

### 3. 一份干净的生产环境配置

不要直接把你本地 `.env` 整个搬上去。

原因：

- 本地配置里可能混了开发配置
- 也可能混了不该上传的密钥

推荐做法：

- 服务器上单独新建 `.env`
- 只填部署必需项

## 部署目录建议

假设服务器上的目录结构这样：

```text
/opt/akane/AkaneCompanionLab
```

应用运行用户建议叫：

```text
akane
```

## 第一次部署的整体顺序

1. 买 VPS，拿到 IP
2. SSH 登录服务器
3. 安装系统依赖
4. 创建运行用户
5. 拉代码到 `/opt/akane/AkaneCompanionLab`
6. 创建 Python 虚拟环境
7. 安装依赖
8. 在服务器上新建 `.env`
9. 用 `python launch_akane_memory_v01.py` 本地试跑
10. 配 `systemd`
11. 配 `nginx`
12. 打开浏览器访问
13. 最后再做 HTTPS 和限流

## 服务器上建议执行的命令

以下是第一轮最常用的命令。

### 更新系统

```bash
sudo apt update
sudo apt upgrade -y
```

### 安装基础依赖

```bash
sudo apt install -y python3 python3-venv python3-pip nginx git
```

### 创建运行用户

```bash
sudo useradd -m -s /bin/bash akane
```

### 创建目录

```bash
sudo mkdir -p /opt/akane
sudo chown -R akane:akane /opt/akane
```

### 拉代码

如果你是用 git：

```bash
cd /opt/akane
git clone <你的仓库地址> AkaneCompanionLab
```

如果你暂时不打算上 git，也可以先本地压缩后传上去再解压。

### 创建虚拟环境并安装依赖

```bash
cd /opt/akane/AkaneCompanionLab
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

如果你准备上本地 HuggingFace embedding，再额外：

```bash
pip install -r requirements-ml.txt
```

## 生产 `.env` 怎么处理

建议你在服务器上手工创建，不要直接上传你本地的完整 `.env`。

可以先复制模板：

```bash
cp deploy/env/.env.vps.example .env
```

然后编辑：

```bash
nano .env
```

如果你发现 VPS 上流式语音不稳定、只播放前半句，建议在服务器 `.env` 里显式设置：

```env
STREAMING_TTS_ENABLED=false
```

这样公网环境会改成“整句生成后一次性播报”，本地则仍可保留流式语音。

如果你准备公开体验，建议同时补上最小保护层：

```env
PUBLIC_GUARD_ENABLED=true
MAX_CONCURRENT_THINKS=2
DAILY_THINK_LIMIT=200
PUBLIC_BUSY_MESSAGE=当前体验人数较多，请稍后再试。
PUBLIC_DAILY_LIMIT_MESSAGE=今日体验名额已满，明天再来看看 Akane 吧。
```

这套保护只会拦截高成本的 `/think` 请求，不会影响用户打开页面、切设置或浏览历史。

## 本地试跑命令

第一次先别急着上 `systemd`，先手动试跑：

```bash
cd /opt/akane/AkaneCompanionLab
source .venv/bin/activate
python launch_akane_memory_v01.py
```

如果你看到：

- 服务启动成功
- `http://127.0.0.1:9999/` 可访问

那说明应用本身没问题。

## systemd 和 nginx 模板

我已经给你准备好了：

- `systemd` 模板：
  [akane.service.example](/deploy/systemd/akane.service.example)
- `nginx` 模板：
  [akane.nginx.conf.example](/deploy/nginx/akane.nginx.conf.example)

它们默认按这套假设写的：

- 项目目录：`/opt/akane/AkaneCompanionLab`
- 运行用户：`akane`
- FastAPI 只监听本机 `127.0.0.1:9999`
- `nginx` 对外提供访问

### 一个很重要的流式细节

Akane 的前端不是等整段回复一次性返回，而是依赖 `/think` 的 NDJSON 流式事件来实时更新：

- 角色对白
- BGM 切换
- 选项出现
- 场景/表情变化

如果 `nginx` 对 `/think` 做了默认缓冲，就可能出现：

- 文字能慢慢出来，但最终 UI 状态不及时更新
- 或者要刷新页面，才能看到新的 BGM / 选项 / 场景

所以模板里已经额外为 `/think` 关闭了：

- `proxy_buffering`
- `proxy_request_buffering`
- `proxy_cache`

同时后端也会返回：

- `X-Accel-Buffering: no`

这两边都加上，流式表现会稳很多。

## 第一轮不建议做的事

先别急着做这些：

- 多 worker
- Docker/Kubernetes
- 多实例部署
- 很复杂的自动扩缩容
- 真账号系统

当前阶段，先把“稳定跑起来 + 能给人访问”做好最重要。

## 第一轮外放前必须确认的点

### 1. API Key 不要暴露到前端

它们只能存在服务器 `.env`。

### 2. 本地 debug 不要裸露给公开用户

之后我们会继续收口这层。

### 3. 要准备最小限流和预算保护

公开外放前建议至少补：

- 单 IP / 单身份频率限制
- 总预算保护
- 优雅的“体验名额已满”提示

## 你接下来真正需要做的

如果你准备开始部署，我建议你的下一步就只有这 3 件事：

1. 买一台 Ubuntu 22.04 VPS
2. 把 SSH 登录方式准备好
3. 决定代码是：
   - 用 git 拉
   - 还是先手工上传

等你做到这一步，我们就可以进入下一轮：

**我带你一步一步把 Akane 部署上去。**
