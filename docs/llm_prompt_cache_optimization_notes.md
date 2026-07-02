# LLM Prompt Cache 优化备忘录

更新时间：2026-07-02

用途：把这轮关于提示词缓存、N.E.K.O 参考项目、Akane 当前缓存结构的判断固定下来，避免后续上下文压缩后丢失。本文是设计备忘录，不代表要立刻重写提示词链路。

## 核心结论

Akane 的缓存命中率还能优化，但后续收益大概率是小步、测量驱动的。主聊天回复链路想长期稳定到 90%+ 并不现实，除非动态尾部很小，或者长期语义记忆改成更多按需召回。

当前在恢复 raw timeline 对齐之后，约 70-75% 的命中率是健康区间。对一个桌宠伴侣系统来说，这个结果不低，因为最终 prompt 里本来就有当前视觉状态、当前用户消息、未总结原始对话、检索片段、当前时间、记忆变化等动态内容。

不要为了更高缓存命中牺牲记忆准确性和回复表现。缓存后的输入再便宜，如果模型拿不到该拿的上下文，产品体验还是会变差。

## Akane 当前结构

本地相关入口：

- `companion_v01/prompt_builder.py`
  - `build_final_generation_context()` 返回 `system_prompt`、`system_extra_blocks`、`history_turns`、`user_prompt`。
  - 目前 `system_extra_blocks` 只保留视觉资源清单这类半稳定内容。
  - 较长期语义记忆和最近阶段摘要已经移到 `user_prompt` 的动态尾部，位于未总结原始消息之前。
  - `user_prompt` 仍包含高动态内容：较长期语义记忆、最近阶段摘要、当前未总结原始消息、可用回忆片段、额外上下文、当前视觉状态、当前用户消息、当前时间。
- `companion_v01/llm_runtime.py`
  - Anthropic 协议会保留 `system_extra_blocks`，让 Anthropic client 拼成 content blocks。
  - 非 Anthropic / OpenAI-compatible 路径会把 `system_prompt + system_extra_blocks` 合并成一个 system 字符串；因此动态记忆不应放进 `system_extra_blocks`。
  - 最终回复可发送 `prompt_cache_key`；DeepSeek-like streaming 在支持时会发送 `stream_options={"include_usage": True}` 拿 usage。
  - 缓存指标同时兼容 Anthropic 风格的 `cache_read_input_tokens` / `cache_creation_input_tokens`，以及 DeepSeek 风格的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`。
- `services/llm_client.py`
  - Anthropic payload 会把 system prompt 和 extra blocks 转成 system text blocks。
  - 最多 4 个 system blocks 会加 `cache_control: {"type": "ephemeral"}`。
- `companion_v01/engine.py`
  - 主最终回复使用 `prompt_cache_key="chat:final"`。
  - raw timeline 对齐很关键：之前 structured-history 改法让命中率下降，恢复 raw timeline 后命中率回到原来的区间。

2026-07-02 更新：DeepSeek-like 自动硬盘缓存更依赖稳定前缀完整匹配。Akane 已把语义记忆和阶段摘要从 `system_extra_blocks` 移到 `user_prompt`，避免这些每轮可能变化的记忆层污染 system prefix。视觉资源清单仍留在 `system_extra_blocks`，因为它比当前轮记忆、检索片段和时间锚点更稳定。

本地已知缓存相关提交：

- `4f1e67a Add LLM cache usage observability`
- `bf3dbe3 feat: layer system prompt cache blocks for stable content`
- `95b6977 feat: structured history turns for prefix cache alignment`
- `013db57 Restore raw timeline cache alignment`

## N.E.K.O 可借鉴点

参考项目：`Project-N-E-K-O/N.E.K.O@3bc0886`。

N.E.K.O 确实是缓存意识比较强的项目，但它的价值主要不在某个神奇开关，而在稳定前缀、provider usage 观测、prompt budget/audit、会话结构这些工程习惯上。

值得借鉴：

1. 稳定增长的会话历史。
   - 主聊天路径初始化一个 `SystemMessage(instructions)`，之后按时间顺序追加 user/assistant turns。
   - 这种结构天然适合 prefix cache，因为旧 turn 在后续轮次中保持稳定。

2. 明确区分注入通道。
   - `prime_context`：只在 session 开始时追加到 system prompt。
   - `create_response`：持久化的 user message。
   - `prompt_ephemeral`：只参与本次推理，不写入长期对话历史。
   - 这个分层能避免临时舞台指令污染稳定缓存前缀。

3. 静态前缀在前，动态内容在后。
   - `/new_dialog` 路径把 persona / 长期记忆放在前面，再拼内心活动、近期历史、时间间隔提示等动态内容。
   - 代码注释里明确写了这是为了最大化 prefix cache。

4. provider 无关的缓存 usage 提取。
   - 它兼容多种字段：OpenAI 风格 `prompt_tokens_details.cached_tokens`，Anthropic 风格 `cache_read_input_tokens`，DeepSeek/Silicon/Kimi-like 的 `prompt_cache_hit_tokens`。
   - 这个思路值得 Akane 继续沿用：先从真实 usage 看结果，再决定怎么改结构。

5. prompt audit 和预算纪律。
   - N.E.K.O 有临时 prompt audit，可以记录每次调用各 role/message 的 token 数。
   - Akane 也需要类似能力，因为只看总命中率不够，还要知道到底哪个 section 在变大、哪个 section 在频繁 churn。

不要照搬：

- 不要以为 N.E.K.O 的 `enable_cache_control` flag 就等于 Anthropic content-block `cache_control` 已经真的注入了。审到的版本里，它更像配置/记录项，真正有效的部分还是稳定前缀结构和 usage 观测。
- 不要把别人 90%+ 命中率直接当 Akane 的目标线。它可能来自不同 provider、更小动态尾部、会话级缓存、或者不包含桌宠视觉/检索/记忆 churn 的测试负载。
- 不要因为某段内容语义重要，就把它放到 prompt 前面。缓存看的是精确前缀稳定，不看语义重要性。

## 为什么 Akane 的天然上限更低

Akane 的最终回复 prompt 天生动态。一次正常用户可见回复可能包含：

- 当前视觉或桌面状态；
- 当前用户消息；
- 当前时间；
- 未总结原始对话；
- 近期阶段摘要；
- 长期语义记忆召回；
- retrieval snippets；
- 表情、动作、TTS、工具格式约束；
- 当前模式和调试状态。

这些都是实际产品能力。它们会让桌宠更有临场感，但也会缩小稳定前缀占比。

所以 Akane 更合理的目标不是“命中率越高越好”，而是“在记忆和表现正确的前提下，命中率足够高”：

- 70-75% 左右对主聊天链路已经可以算健康；
- 安静、纯文本、记忆不频繁更新的会话里，80%+ 有可能；
- 90%+ 更像特殊 workload 或不同架构结果，不应作为当前 always-injected memory 结构的默认目标。

## 窗口调参原则

Akane 当前是无缺口记忆：原始对话、阶段摘要、长期语义记忆应该覆盖不同层级，不应该无意重叠。

不要简单把所有窗口一起放大。窗口变大可以降低压缩频率，但也可能增加 prompt 体积、延迟、attention 压力和动态 churn。真正要设计的是：哪一层承载哪类信息。

建议方向：

1. raw recent dialogue 负责最新、按时间排序的原始事实。
   - 保持 append order。
   - 避免同一条最新消息同时出现在 `history_turns` 和 `user_prompt` 的 raw 文本里。
   - raw 窗口扩大只有在它变成稳定前缀历史时才可能帮助缓存；如果每轮都作为动态文本重新渲染，反而可能拉低命中。

2. episodic summaries 保持有边界、低频变化。
   - 它应该覆盖已经离开 raw 窗口的较旧材料。
   - 如果覆盖范围没变，文本就不应该每轮重生成。

3. long-term semantic memory 保持精选。
   - 只把大多数回复都需要的稳定身份/关系事实常驻注入。
   - 稀疏事实更适合按需召回或 routed retrieval。
   - 不要因为 cached input 便宜，就把很多相似长期记忆每轮都塞进去。

4. DeepSeek V4 缓存命中价格低是机会，不是无上限扩 prompt 的理由。
   - 价格和 usage 字段以实际 API response / 账单为准。
   - 即使 cached input 很便宜，大 prompt 仍可能影响延迟、上下文预算和模型注意力质量。

## 后续优化路线

短期：

- 保持已经恢复的 raw timeline alignment。
- 继续记录 `cache_read_tokens` / `cache_creation_tokens` 或 provider 等价 hit/miss 字段。
- 给最终回复链路补 prompt audit：
  - provider / model；
  - prompt token total；
  - cached / hit tokens；
  - miss / creation tokens；
  - system、system extras、history、raw recent text、summary、semantic recall、retrieval snippets、current state、current user text 的 section token 估算。
- 用多轮真实会话比较，不要用单次调用判断。

中期：

- 增加稳定 section fingerprint，用来定位 churn：
  - base system prompt hash；
  - 每个 system extra block hash；
  - history prefix hash；
  - raw recent render hash；
  - summary / semantic block hash。
- 只有当某段内容真的跨轮稳定时，才考虑把它前移。
- 高动态内容继续放在尾部。
- 评估把长期语义记忆更多改成 tool / on-demand recall，避免主回复每轮注入过多稀疏事实。

长期：

- 做 provider-specific policy：
  - DeepSeek / OpenAI-compatible：主要依赖稳定 prefix 和真实 usage 观测。
  - Official OpenAI：只在支持的 official-compatible endpoint 发送 `prompt_cache_key` / retention hints，非官方 endpoint 默认不要强塞，除非显式 force。
  - Anthropic：显式 content-block `cache_control` 要保留并实测；不要假设 automatic caching 会在 growing-history 场景里自动把最有用的断点前移。
- 建一个可复跑的缓存 benchmark：
  - 纯文本安静聊天；
  - 视觉状态频繁变化；
  - 记忆持续写入；
  - 用户长文本粘贴；
  - retrieval-heavy；
  - 切角色 / 切会话。

## 改动护栏

以后任何 prompt 结构改动，都要一起比较：

- cache hit rate；
- total prompt tokens；
- input cost；
- latency；
- 回复质量；
- 记忆召回正确性；
- raw / summary / semantic 三层是否重叠或断档；
- 视觉、动作、气泡、TTS 等用户可见表现是否仍然进入真实链路。

应该做：

- 保持 raw timeline 的时间顺序；
- 稳定 persona / system 内容放在动态 per-turn 内容前；
- 当前时间、当前视觉状态、当前用户消息尽量留在尾部；
- 从真实 streaming response 里记录 provider usage；
- 先做 prompt audit，再调窗口大小。

不要做：

- 不经 A/B 测试就重复 structured-history 改写；
- 让 `recent_raw[-1]` 同时出现在 history 和当前 user 内容里；
- 把当前时间或当前视觉状态放到稳定 memory / persona 之前；
- 只看缓存命中率，不看总成本、延迟和回复质量；
- 假设 Anthropic automatic caching 或某个 provider flag 会自动维护正确的 growing-history 断点。

## 当前问题的直接答案

Akane 的缓存命中率还能优化，但主最终回复链路的安全收益大概率有限。最值得做的是观测、定位 section churn、减少不必要重复、以及把一部分长期语义记忆变成更按需的召回。

Akane 结构上支持“较高命中率”：现在已有 cache usage observability、prompt cache key、Anthropic system blocks、raw timeline alignment。但它的产品设计比 90%+ 示例项目有更大的动态尾部，所以不应按同一命中率目标验收。

原始对话、摘要和长期语义窗口可以调，但必须先 audit。扩大 raw 窗口可能降低压缩频率，也可能在稳定历史前缀里提高缓存；扩大 summary / semantic 常驻注入则更容易带来 attention 和 churn 成本。因为 Akane 是无缺口记忆，任何窗口调整都要明确边界：raw recent messages、episodic summaries、long-term semantic memory 各自覆盖不同范围或不同用途。

下一步最稳的工程动作不是继续大改 prompt，而是做一个小型 benchmark / audit pass：看清楚哪些 section 稳定、哪些 section 每轮变化、每个 section 对 cache miss 贡献多少。
