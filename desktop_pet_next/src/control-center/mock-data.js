import { CONTROL_CENTER_ACTIONS } from "./action-router.js";

export const navItems = [
  { id: "overview", label: "总览", icon: "home" },
  { id: "model", label: "模型", icon: "sparkle" },
  { id: "character", label: "角色", icon: "user" },
  { id: "voice", label: "语音", icon: "mic" },
  { id: "music", label: "音乐", icon: "music" },
  { id: "context", label: "桌面感知", icon: "monitor" },
  { id: "abilities", label: "能力", icon: "sparkle" },
  { id: "advanced", label: "高级", icon: "settings" }
];

export const labMeta = {
  defaultPage: "overview",
  version: "v1.2.0",
  status: "Akane 在线",
  statusDetail: "陪伴中 · 连接正常",
  footer: "所有感知数据仅在本地处理，您的隐私由 Akane 全力守护"
};

export const controlCenterDataDomains = {
  shell: {
    meta: "app.runtime",
    navigation: "app.navigation",
    assets: "app.assets"
  },
  overview: {
    status: "companion.session",
    connection: "service.health",
    quickActions: "command.actions",
    pack: "character.package",
    emotion: "expression.runtime",
    voice: "voice.service",
    music: "music.playback",
    sense: "desktop.sensing",
    abilities: "capability.registry",
    health: "system.metrics"
  },
  model: {
    connection: "model.service",
    providers: "model.providers",
    discovery: "model.discovery"
  },
  character: {
    selectedPack: "character.package",
    outfits: "character.assets.outfits",
    expressions: "character.assets.expressions",
    missingResources: "character.package.validation",
    resourceCounts: "character.package.resources"
  },
  voice: {
    tts: "voice.tts",
    asr: "voice.asr",
    preview: "voice.preview",
    recognitionLog: "voice.recognition",
    synthesisQueue: "voice.synthesisQueue",
    processing: "voice.processing",
    diagnostics: "voice.diagnostics"
  },
  music: {
    nowPlaying: "music.playback",
    queue: "music.queue",
    lyrics: "music.lyrics",
    recommendations: "music.recommendations",
    output: "audio.output"
  },
  perception: {
    features: "desktop.sensing.features",
    permissions: "desktop.permissions",
    activeWindow: "desktop.foregroundWindow",
    clipboard: "desktop.clipboard",
    capture: "desktop.screenCapture",
    proactiveChat: "companion.proactiveChat",
    events: "desktop.sensing.events",
    suggestions: "companion.suggestions",
    diagnostics: "desktop.sensing.diagnostics"
  },
  abilities: {
    summary: "capability.registry",
    actions: "command.actions",
    modules: "capability.modules",
    workflows: "capability.workflows",
    history: "capability.invocations",
    safety: "security.policy",
    live2d: "live2d.runtime"
  },
  advanced: {
    system: "system.metrics",
    rendering: "rendering.runtime",
    operations: "app.operations",
    diagnostics: "app.diagnostics",
    logs: "app.logs",
    live2d: "live2d.runtime",
    abilities: "capability.registry",
    expert: "app.expertOptions"
  }
};

export const modelPage = {
  title: "模型服务",
  accent: "Model",
  subtitle: "选择服务商、检测可用模型并测试连接。保存后立即用于角色对话。",
  status: "missing_config",
  source: "environment",
  providerId: "deepseek",
  protocol: "openai",
  baseUrl: "https://api.deepseek.com/v1",
  hasApiKey: false,
  chatModel: "deepseek-chat",
  useForVision: true,
  visionModel: "",
  timeoutSeconds: 120,
  providers: [
    {
      id: "deepseek",
      label: "DeepSeek",
      protocol: "openai",
      baseUrl: "https://api.deepseek.com/v1",
      apiKeyRequired: true,
      description: "DeepSeek 官方 OpenAI 兼容接口。"
    },
    {
      id: "openai_compatible",
      label: "其他 OpenAI 兼容服务",
      protocol: "openai",
      baseUrl: "",
      apiKeyRequired: true,
      description: "填写服务商给你的 Base URL。"
    },
    {
      id: "ollama",
      label: "Ollama 本地模型",
      protocol: "ollama",
      baseUrl: "http://127.0.0.1:11434",
      apiKeyRequired: false,
      description: "本机 Ollama，无需 API Key。"
    }
  ]
};

export const overviewPage = {
  title: "总览",
  accent: "Overview",
  subtitle: "Akane 当前状态、连接诊断、常用操作和系统健康都在这里。",
  status: {
    title: "Akane 状态卡",
    badge: "在线 / Connected",
    image: "happy",
    hero: "akaneSkyWide",
    items: [
      { label: "连接状态", value: "已连接", icon: "monitor" },
      { label: "当前角色包", value: "Akane Default", icon: "folder" },
      { label: "当前表情", value: "微笑", icon: "smile" },
      { label: "今日能力摘要", value: "7 / 8 可用", icon: "settings" }
    ]
  },
  connection: {
    title: "连接与诊断",
    badge: "连接正常",
    rows: [
      { label: "服务状态", value: "正常运行", icon: "clock", tone: "good" },
      { label: "响应延迟", value: "42 ms", icon: "equalizer" },
      { label: "同步状态", value: "稳定", icon: "refresh", tone: "good" },
      { label: "会话时长", value: "02:17:36", icon: "clock" }
    ]
  },
  quickActions: [
    { label: "新对话", icon: "message", tone: "pink" },
    { label: "停止回复", icon: "stop", tone: "orange" },
    { label: "打开手边", icon: "folder", tone: "blue" }
  ],
  pack: {
    title: "当前角色包",
    name: "Akane Default",
    version: "1.0.0",
    publishedAt: "2024-05-20",
    action: "管理角色包"
  },
  emotion: {
    title: "当前表情预览",
    name: "微笑",
    image: "happy"
  },
  voice: {
    title: "语音状态",
    rows: [
      { label: "回复朗读（TTS）", enabled: true },
      { label: "语音输入（ASR）", enabled: true }
    ],
    status: "语音状态：正常"
  },
  music: {
    title: "音乐播放",
    song: "星途与你",
    artist: "Akane",
    cover: "akaneNightWindow",
    controls: ["上一首", "下一首", "暂停", "停止", "清空"]
  },
  sense: {
    title: "桌面感知",
    note: "我们仅在获得你的授权后启用感知功能，所有数据均在本地处理，保护你的隐私安全。",
    toggles: ["前台窗口感知", "剪贴板文本", "看屏幕", "主动搭话"]
  },
  abilities: ["文件处理", "文档交付", "媒体工具", "安全保护", "手边物品", "Live2D 预留状态"],
  health: [
    { label: "CPU 占用", value: "18%", detail: "spark-blue" },
    { label: "内存占用", value: "2.1 GB", detail: "spark-purple" },
    { label: "记忆容量", value: "0.5K 条记忆", progress: 8 },
    { label: "峰值内存", value: "峰值 412 MB", detail: "spark-orange" },
    { label: "错误数", value: "0", progress: 0 },
    { label: "活跃守护", value: "0", detail: "spark-orange" },
    { label: "协议版本", value: "desktop_pet", note: "已同步" },
    { label: "能力注册", value: "25 工具", note: "能力注册表已同步" }
  ],
  recentOutputs: [
    { title: "Q4 报告草案", subtitle: "DOCX · 做好的东西", format: "docx" },
    { title: "调研笔记整理", subtitle: "MD · 做好的东西", format: "md" },
    { title: "周报摘要", subtitle: "PDF · 做好的东西", format: "pdf" }
  ]
};

export const characterPage = {
  title: "角色与资源管理",
  accent: "Character",
  subtitle: "管理 Akane 的角色包、服装、表情等资源，自定义她在桌面上的形象与表现。",
  hero: "akaneSakuraWide",
  selectedPack: "Akane Default",
  selectedPackId: "akane_default",
  packInfo: [
    { label: "名称", value: "Akane Default" },
    { label: "版本", value: "1.0.0" },
    { label: "作者", value: "Akane Studio" },
    { label: "描述", value: "默认角色包" }
  ],
  completeness: 98,
  outfits: [
    { id: "default", name: "默认服装", badge: "当前", image: "happy", current: true },
    { id: "summer", name: "夏日制服", image: "normal" },
    { id: "home", name: "居家休闲", image: "shy" },
    { id: "dress", name: "礼服 · 星夜", image: "listening" }
  ],
  emotions: [
    { id: "smile", name: "微笑", image: "happy", current: true },
    { id: "confused", name: "困惑", image: "confused" },
    { id: "shy", name: "害羞", image: "shy" },
    { id: "pout", name: "生气", image: "pout" }
  ],
  warning: {
    title: "资源缺失提示",
    headline: "未检测到部分动作贴图",
    body: "2 个资源待补全",
    action: "前往修复"
  },
  resources: [
    { label: "动作资源", value: "120 / 122", tone: "blue" },
    { label: "表情资源", value: "32 / 32", tone: "green" },
    { label: "服装资源", value: "18 / 18", tone: "pink" },
    { label: "背景资源", value: "11 / 11", tone: "green" }
  ],
  tip: [
    "导入新的角色包后，建议刷新资源以确保所有内容正确加载。",
    "如遇资源缺失，可点击「前往修复」自动检测并补全。"
  ],
  actions: ["应用", "刷新", "恢复默认"]
};

export const voicePage = {
  title: "语音设置",
  accent: "Voice",
  subtitle: "与 Akane 通过自然语音对话，让陪伴更贴近你。",
  tts: {
    title: "TTS 回复朗读",
    subtitle: "让 Akane 用温柔的声音回应你",
    enabled: true,
    voice: "Akane Voice",
    volume: 80,
    speed: "1.00x"
  },
  asr: {
    title: "ASR 语音输入",
    subtitle: "识别你的声音，理解你的心意",
    enabled: true,
    device: "麦克风阵列 (Realtek(R) Audio)",
    language: "中文（简体）",
    sensitivity: 70
  },
  preview: {
    title: "Akane 声音预览",
    subtitle: "试听 Akane 的声音吧",
    text: ["你好呀~我是 Akane", "很高兴能和你聊天呢！", "有什么想和我分享的吗？"],
    duration: "00:06"
  },
  records: [
    { text: "Akane，今天天气怎么样？", time: "10:24:31", score: "95%" },
    { text: "帮我播放一首轻音乐吧", time: "10:22:18", score: "93%" },
    { text: "打开浏览器", time: "10:20:05", score: "98%" },
    { text: "提醒我喝水", time: "10:18:44", score: "90%" },
    { text: "晚安，Akane", time: "10:16:21", score: "96%" }
  ],
  queue: [
    { text: "今天的天气很好呢，记得带伞哦~", duration: "00:04" },
    { text: "为你播放一首温柔的轻音乐吧♪", duration: "00:06" },
    { text: "已为你打开浏览器。", duration: "00:02" },
    { text: "好的，我会在每小时提醒你喝水哦~", duration: "00:05" },
    { text: "晚安呀，愿你有个好梦~ 🌙", duration: "00:04" }
  ],
  processing: [
    { label: "降噪", detail: "减少背景噪音干扰", enabled: true },
    { label: "回声消除", detail: "消除回声，提升通话质量", enabled: true },
    { label: "唤醒词", detail: "说出唤醒词来唤醒 Akane", enabled: true }
  ],
  wakeWord: "Akane",
  wakeSensitivity: "中等",
  diagnostics: [
    { label: "整体状态", value: "正常运行", tone: "good" },
    { label: "ASR 延迟", value: "42 ms", tone: "good" },
    { label: "TTS 延迟", value: "58 ms", tone: "good" },
    { label: "语音引擎", value: "在线", tone: "good" },
    { label: "采样率", value: "48 kHz", tone: "good" },
    { label: "通道模式", value: "立体声", tone: "good" },
    { label: "网络状态", value: "良好", tone: "good" }
  ],
  footer: "语音连接已就绪，随时和 Akane 聊聊天吧～"
};

export const musicPage = {
  title: "音乐播放",
  accent: "Music",
  subtitle: "让音乐陪伴你，放松心情，专注每一刻",
  nowPlaying: {
    title: "星光与你",
    artist: "Akane",
    quality: "无损音质",
    elapsed: "01:24",
    duration: "04:38",
    progress: 32,
    volume: 68,
    playing: true,
    paused: false,
    albumTitle: "Starry Days",
    cover: "akaneNightWindow"
  },
  playlist: [
    { id: "track_clearSky", title: "晴空漫游", artist: "Akane", duration: "03:45", cover: "akaneSkyPaperPlane" },
    { id: "track_cloudSignal", title: "云端信号", artist: "Akane", duration: "04:12", cover: "cloudLetter" },
    { id: "track_summerTrail", title: "夏日轨迹", artist: "Akane", duration: "03:58", cover: "starryCloudCat" },
    { id: "track_starsWithYou", title: "星光与你", artist: "Akane", duration: "04:38", active: true, cover: "akaneNightWindow" },
    { id: "track_gentleFreq", title: "温柔频率", artist: "Akane", duration: "04:01", cover: "akaneSakuraClose" },
    { id: "track_nightBeat", title: "夜色心跳", artist: "Akane", duration: "03:36", cover: "moonBalcony" }
  ],
  lyrics: [
    "微风轻轻吹过窗台",
    "阳光洒在你的侧脸",
    "星光与你 都在我身边",
    "世界变得温柔又可爱",
    "每一个瞬间 都想收藏起来",
    "和你一起 看未来慢慢盛开",
    "星光与你 未来都精彩"
  ],
  activeLyric: 2,
  currentPlayMode: "列表循环",
  outputDevice: "扬声器",
  info: [
    { label: "时长", value: "04:38" },
    { label: "来源", value: "本地音乐" },
    { label: "音质", value: "FLAC 无损" }
  ],
  modes: ["放松", "专注", "治愈", "活力", "思念", "睡前"],
  recommendations: [
    { id: "rec_firefly", title: "萤火与晚风", artist: "Akane", duration: "03:52", cover: "moonBalcony" },
    { id: "rec_galaxy", title: "银河便利店", artist: "Akane", duration: "04:21", cover: "cloudLetter" },
    { id: "rec_gummy", title: "软糖星球", artist: "Akane", duration: "03:47", cover: "starryCloudCat" }
  ],
  bottomStatus: "正在为你播放最合适的音乐，愿每一首歌都能温暖你。"
};

export const perceptionPage = {
  title: "桌面感知",
  accent: "Desktop Sense",
  subtitle: "感知你的桌面环境，理解当前上下文，在恰当的时候主动提供帮助。",
  helpLabel: "如何保护我的隐私？",
  featureCards: [
    {
      id: "activeWindow",
      icon: "lock",
      title: "前台窗口感知",
      description: "识别当前正在使用的窗口",
      enabled: true,
      previewType: "window",
      label: "当前活动窗口",
      appName: "等待同步",
      appDetail: "同步后将在此显示状态",
      version: "桌面感知"
    },
    {
      id: "clipboard",
      icon: "clipboard",
      title: "剪贴板文本",
      description: "感知你复制的内容（仅保留近期文本）",
      enabled: true,
      previewType: "code",
      label: "最新剪贴板内容",
      code: ["剪贴板内容不在控制中心展示"],
      source: "仅显示能力状态 · 未读取正文"
    },
    {
      id: "screen",
      icon: "eye",
      title: "看屏幕",
      description: "定期截取屏幕，理解当前画面内容",
      enabled: true,
      previewType: "settings",
      label: "截图频率设置",
      frequency: "25 秒",
      frames: "4",
      hint: "帧（仅保留近期画面）",
      note: "仅本地处理，不上传任何截图"
    },
    {
      id: "proactive",
      icon: "chat",
      title: "主动搭话",
      description: "在合适的时间主动与你交流",
      enabled: true,
      previewType: "interval",
      label: "搭话间隔",
      options: ["3 分钟", "5 分钟", "10 分钟", "15 分钟", "30 分钟"],
      activeOption: "5 分钟",
      note: "会根据上下文智能判断是否打扰你"
    }
  ],
  privacy: [
    "所有感知数据均在本地处理，不会上传到任何云端。",
    "Akane 只在你授权的范围内工作，你可随时关闭或调整。",
    "数据仅用于提供更贴心的帮助，不会用于训练或分析。",
    "你可以随时查看、清除或管理历史记录。"
  ],
  permissions: [
    { id: "screen", label: "屏幕捕获", icon: "shield" },
    { id: "clipboard", label: "剪贴板", icon: "clipboard" },
    { label: "麦克风", icon: "mic", fixed: true },
    { label: "文件访问", icon: "folder", fixed: true }
  ],
  events: [],
  suggestion: {
    title: "Akane 的感知小记",
    body: ""
  },
  diagnostics: [
    { label: "屏幕捕获帧率", value: "等待同步", detail: "运行时数据就绪后将在此显示", tone: "info" },
    { label: "OCR 识别状态", value: "等待同步", detail: "视觉识别状态暂未接入控制中心", tone: "info" },
    { label: "最后更新时间", value: "--:--:--", detail: "等待同步", tone: "info" }
  ]
};

export const abilitiesPage = {
  title: "能力一览",
  accent: "Abilities",
  subtitle: "掌控 Akane 的能力模块，查看状态与使用记录",
  overview: {
    stats: [
      { label: "可用模块", value: "6" },
      { label: "已授权权限", value: "32" },
      { label: "需审批权限", value: "2" }
    ],
    availability: 98,
    note: "所有系统运行正常，能力服务稳定"
  },
  quickActions: [
    { label: "创建文档", icon: "doc", tone: "blue" },
    { label: "打开工作区", icon: "folder", tone: "purple", actionId: CONTROL_CENTER_ACTIONS.workspaceOpen },
    { label: "启动工具", icon: "folder", tone: "orange" },
    { label: "安全检查", icon: "shield", tone: "green" },
    { label: "查看日志", icon: "log", tone: "pink" },
    { label: "状态面板", icon: "panel", tone: "blue" }
  ],
  productization: [
    {
      title: "模型与核心聊天",
      status: "Ready",
      tone: "green",
      description: "可视模型配置、连接测试和对话主链路已经进入首体验。",
      configure: "模型页",
      verify: "保存配置后发送一条消息",
      dependency: "OpenAI 兼容服务、Ollama 或云端 API"
    },
    {
      title: "记忆系统",
      status: "Alpha",
      tone: "blue",
      description: "语义记忆和按时间读取原始对话已接入，仍需要更清楚的用户查看/清理入口。",
      configure: "自动启用，后续补记忆管理页",
      verify: "询问过去偏好或读取日期范围",
      dependency: "SQLite；向量检索可选"
    },
    {
      title: "角色包与知识文件",
      status: "Alpha",
      tone: "blue",
      description: "角色包、表情、关系/知识文件和本地存储已接入，仍需公开样例包打磨。",
      configure: "角色页 / 角色工坊",
      verify: "切换角色包并检查表情与人设文件",
      dependency: "本地角色包资源"
    },
    {
      title: "GPT-SoVITS 语音",
      status: "Productization Gap",
      tone: "orange",
      description: "已有外部端点、健康检查、试听、声线档案和角色绑定，仍需一条完整引导流。",
      configure: "能力页 · 本地能力环境",
      verify: "健康检查 → 短试听 → 绑定当前角色",
      dependency: "用户自备 GPT-SoVITS 兼容服务"
    },
    {
      title: "MCP 外部工具",
      status: "Productization Gap",
      tone: "orange",
      description: "已有保存配置和发现工具，仍需补成服务器管理器：模板、测试、工具列表和错误日志。",
      configure: "能力页 · 外部 MCP 工具",
      verify: "保存服务器后执行发现工具",
      dependency: "用户自备 MCP server"
    },
    {
      title: "音乐陪伴",
      status: "Productization Gap",
      tone: "orange",
      description: "本地拖拽播放、队列、歌词和 Windows 系统音乐感知存在，发布前要先稳住状态机。",
      configure: "音乐页 / 拖拽本地音频",
      verify: "拖入音频后测试播放、暂停、下一首、清空",
      dependency: "Windows 桌宠；系统媒体感知仅 Windows"
    },
    {
      title: "QQ / NapCat",
      status: "Productization Gap",
      tone: "orange",
      description: "OneBot/NapCat 适配存在，但用户仍需要外部服务部署和清晰自检路径。",
      configure: ".env / QQ 自检脚本",
      verify: "收发消息、附件、文件发送自检",
      dependency: "外部 NapCat / OneBot"
    },
    {
      title: "本地工作流",
      status: "Productization Gap",
      tone: "orange",
      description: "工作流绑定、导入、校验和任务接口存在，仍需至少一个端到端公开样例。",
      configure: "能力页 · 本地工作流",
      verify: "导入 JSON → 校验 → 从工坊执行",
      dependency: "用户自备 ComfyUI 等本地服务"
    }
  ],
  modules: [
    {
      title: "文件处理",
      description: "读取 / 整理 / 转换",
      permission: "读写文件",
      count: "6 项能力",
      tone: "blue",
      icon: "folder"
    },
    {
      title: "生成文件交付",
      description: "文档 / 报告 / 表格",
      permission: "生成与导出",
      count: "5 项能力",
      tone: "purple",
      icon: "file"
    },
    {
      title: "手边物品",
      description: "材料 / 成果 / 任务",
      permission: "剪贴板/临时存储",
      count: "4 项能力",
      tone: "orange",
      icon: "gift"
    },
    {
      title: "媒体工具",
      description: "转写 / 分离 / 转码",
      permission: "多媒体操作",
      count: "7 项能力",
      tone: "green",
      icon: "play"
    },
    {
      title: "安全边界",
      description: "权限 / 审批 / 保护",
      permission: "安全与隔离",
      count: "8 项能力",
      tone: "blue",
      icon: "shield"
    },
    {
      title: "Live2D 预留状态",
      description: "渲染 / 动画 / 物理",
      permission: "渲染与交互",
      count: "6 项能力",
      tone: "pink",
      icon: "sparkle"
    }
  ],
  workflows: [
    {
      steps: ["文件处理", "生成文档", "交付"],
      title: "读取文件 → 生成文档 → 交付",
      detail: "读取资料，生成报告并交付给你"
    },
    {
      steps: ["媒体工具", "转写", "摘要"],
      title: "录制音频 → 转写 → 生成摘要",
      detail: "录音并转写为文本，生成摘要"
    },
    {
      steps: ["手边物品", "保存文件", "归档"],
      title: "剪贴板 → 保存文件 → 归档",
      detail: "保存剪贴板内容并归档到本地"
    }
  ],
  calls: [
    {
      time: "12:35:21",
      module: "生成文件交付",
      description: "生成《项目周报_2024-05-12.docx》并交付",
      status: "成功",
      duration: "2.38s",
      method: "手动触发"
    },
    {
      time: "12:31:05",
      module: "文件处理",
      description: "读取《需求文档_v2.pdf》内容",
      status: "成功",
      duration: "1.12s",
      method: "自动触发"
    },
    {
      time: "12:28:47",
      module: "媒体工具",
      description: "录制 01:20 音频并导出为 mp3",
      status: "成功",
      duration: "3.61s",
      method: "手动触发"
    },
    {
      time: "12:27:12",
      module: "手边物品",
      description: "获取剪贴板内容并保存为文本文件",
      status: "成功",
      duration: "0.62s",
      method: "自动触发"
    },
    {
      time: "12:25:02",
      module: "安全边界",
      description: "拦截批量删除操作（>10 个文件）",
      status: "已拦截",
      duration: "0.15s",
      method: "策略拦截"
    }
  ],
  safety: {
    status: "已生效",
    approvalPolicy: {
      defaultMode: "ask_each_time",
      label: "请求批准",
      summary: "高风险能力在执行前创建审批请求，由用户允许或拒绝。",
      requiresConfirmationByDefault: true,
      trustedAutoAllowHighRisk: false,
      availableModes: [
        { id: "ask_each_time", label: "请求批准", summary: "高风险动作先进入审批队列。" },
        { id: "trusted_auto_allow", label: "完全访问", summary: "跳过高风险动作的逐次确认，但不跳过硬安全校验。" }
      ]
    },
    items: [
      { label: "当前审批模式", status: "请求批准" },
      { label: "系统设置修改", status: "需审批" },
      { label: "注册表与系统目录访问", status: "需审批" },
      { label: "网络请求（外部）", status: "需审批" },
      { label: "批量文件删除", status: "需审批" }
    ]
  },
  live2d: {
    status: "正常",
    items: [
      { label: "模型", value: "Akane_Default" },
      { label: "动作", value: "IDLE · 微笑" },
      { label: "渲染器", value: "GPU 硬件加速" },
      { label: "物理", value: "布料/头发 · 正常" }
    ]
  }
};

export const advancedPage = {
  title: "高级功能与调试",
  accent: "Advanced",
  subtitle: "配置高级选项，查看运行状态、渲染状态与安全诊断信息。",
  systemStrip: [
    { label: "运行中", value: "", icon: "checkCircle", tone: "green" },
    { label: "CPU", value: "12%", icon: "equalizer", tone: "blue" },
    { label: "内存", value: "38%", icon: "panel", tone: "blue" },
    { label: "网络", value: "良好", icon: "wifi", tone: "green" }
  ],
  coreSettings: [
    {
      id: "webgl",
      title: "WebGL",
      description: "启用硬件加速渲染，提升 Live2D 与特效表现。",
      enabled: true,
      icon: "panel",
      tone: "blue",
      actionId: "advanced.toggleWebgl"
    },
    {
      id: "hitTest",
      title: "Hit-Test",
      description: "启用点击检测，支持与桌宠进行交互。",
      enabled: true,
      icon: "target",
      tone: "blue",
      actionId: "advanced.setHitTestEnabled"
    },
    {
      id: "hitbox",
      title: "Hitbox",
      description: "显示与优化可交互范围，提高点击准确性。",
      enabled: true,
      icon: "focus",
      tone: "blue",
      actionId: "advanced.setHitboxOverlay"
    }
  ],
  operations: [
    {
      title: "窗口穿透 5s",
      description: "临时穿透桌面窗口，持续 5 秒。",
      action: "执行",
      icon: "window",
      tone: "blue",
      actionId: "advanced.probeClickThrough"
    },
    {
      title: "重置窗口",
      description: "将桌宠窗口恢复到默认位置与大小。",
      action: "重置",
      icon: "refresh",
      tone: "blue",
      actionId: "advanced.resetWindow"
    },
    {
      id: "exitPet",
      title: "退出桌宠",
      description: "关闭桌宠进程，停止所有运行服务。",
      action: "退出",
      icon: "alert",
      tone: "pink",
      actionId: "advanced.exitPet"
    }
  ],
  diagnostics: {
    metrics: [
      { label: "应用状态", value: "运行中", icon: "circle", tone: "green" },
      { label: "后端健康", value: "良好", icon: "heart", tone: "green" },
      { label: "帧率 (FPS)", value: "60", icon: "equalizer", tone: "blue", spark: true },
      { label: "内存占用", value: "412 MB", icon: "panel", tone: "blue", spark: true }
    ],
    logs: [
      { time: "10:26:31", level: "INFO", message: "[Render] WebGL context created" },
      { time: "10:26:31", level: "INFO", message: "[Live2D] Model \"akane.model3.json\" loaded" },
      { time: "10:26:32", level: "INFO", message: "[Audio] Audio session connected" },
      { time: "10:26:32", level: "INFO", message: "[Voice] TTS service ready" },
      { time: "10:26:33", level: "INFO", message: "[Desktop] Sensing service ready" },
      { time: "10:26:33", level: "INFO", message: "[Service] Core service connected" },
      { time: "10:26:34", level: "INFO", message: "[Update] Config loaded successfully" },
      { time: "10:26:34", level: "INFO", message: "[Security] Sandbox initialized" }
    ]
  },
  live2d: {
    image: "happy",
    rows: [
      { label: "模型", value: "正常" },
      { label: "动作", value: "正常" },
      { label: "渲染器", value: "正常" },
      { label: "物理", value: "正常" }
    ]
  },
  abilityOverview: [
    { id: "adv_abilit_file", label: "文件处理", icon: "folder", tone: "blue" },
    { id: "adv_abilit_deliver", label: "生成文件交付", icon: "file", tone: "green" },
    { id: "adv_abilit_items", label: "手边物品", icon: "gift", tone: "pink" },
    { id: "adv_abilit_media", label: "媒体工具", icon: "play", tone: "purple" },
    { id: "adv_abilit_safety", label: "安全边界", icon: "shield", tone: "orange" }
  ],
  expertOptions: [
    {
      id: "expert_devMode",
      title: "开发者模式",
      description: "启用调试与开发者工具，适用于开发与排查问题。",
      enabled: false,
      icon: "code"
    },
    {
      id: "expert_detailedLogs",
      title: "详细日志",
      description: "记录更详细的运行日志，可能影响性能与磁盘占用。",
      enabled: false,
      icon: "log"
    },
    {
      id: "expert_hardwareAccel",
      title: "硬件加速",
      description: "启用 GPU 硬件加速，提升渲染与解码性能。",
      enabled: true,
      icon: "cpu"
    },
    {
      id: "expert_lowLatency",
      title: "低延迟模式",
      description: "优化事件处理与队列，减少交互延迟。",
      enabled: true,
      icon: "zap"
    },
    {
      id: "expert_autoUpdate",
      title: "自动更新",
      description: "自动检查并安装更新，保持最佳体验与安全。",
      enabled: true,
      icon: "refresh"
    }
  ],
  expertNote: "更改设置后可能需要重启应用以生效。"
};
