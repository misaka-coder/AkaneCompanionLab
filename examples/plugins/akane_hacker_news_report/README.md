# Hacker News 报告插件

这是 Akane 的真实只读工具型插件样例。它从 Hacker News 官方公共 API 读取榜单条目，返回
结构化事实，并生成一份由宿主管理和投递的 Markdown 报告。

## 能力

- 插件 ID：`akane.sample.hacker-news-report`
- 工具 ID：`akane.sample.hacker-news-report.fetch.v1`
- 榜单：`top`、`new`、`best`、`ask`、`show`、`job`
- 条目数：1–10，默认 5
- 权限：`capability.prompt.invoke`、`network.read`、`artifact.write`
- 固定数据源：`https://hacker-news.firebaseio.com/v0/`

接口字段和榜单端点以 [Hacker News 官方 API 文档](https://github.com/HackerNews/API) 为准。

工具没有任意 URL 参数、Token 或本地路径。网络读取和报告生成都经过正式的 CapCore 描述、
PluginHost、Engine 工具桥、MemCore 工具轨迹和宿主托管产物链。插件只返回报告字节；文件落点、
公开句柄和客户端投递由宿主决定。

网络读取采用公开边界：单响应最多 1 MiB、单请求 10 秒、连接/超时最多尝试 2 次、一次最多
读取 10 个条目。这些是外部 I/O 和产物资源边界，不是 Agent 工具轮数或任务时长限制。HTTP
错误与非法响应不重试。部分条目失败时返回已经取得的事实并明确列出遗漏；全部失败时返回
结构化错误，不生成假报告。

## 本地构建和启用

```powershell
python -m build --wheel examples/plugins/akane_hacker_news_report
python -m pip install --no-deps examples/plugins/akane_hacker_news_report/dist/akane_hacker_news_report-0.1.0-py3-none-any.whl
```

```toml
[[plugins]]
id = "akane.sample.hacker-news-report"
enabled = true
```

通过插件管理入口暂存并校验 wheel。候选代健康后原子接替当前代；候选失败时继续使用
last-good。启用、停用、更新和移除都不需要重启 Bot 进程。
