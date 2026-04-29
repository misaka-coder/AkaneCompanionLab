const REQUIRED_EMOTIONS = ["正常"];
const RECOMMENDED_EMOTIONS = [
  "思考中",
  "侧耳听",
  "听歌中",
  "困惑",
  "开心",
  "得意",
  "求摸摸",
  "被摸头",
  "困困",
  "打哈欠",
  "卖萌",
  "气鼓鼓",
  "无语",
  "偷吃被抓",
];

class HealthCheckService {
  constructor({ backendClient, getIdentity, getManifest, getOutfit, getVoiceEnabled, getVoiceInputEnabled } = {}) {
    this._backendClient = backendClient;
    this._getIdentity = getIdentity;
    this._getManifest = getManifest;
    this._getOutfit = getOutfit;
    this._getVoiceEnabled = getVoiceEnabled;
    this._getVoiceInputEnabled = getVoiceInputEnabled;
  }

  async run() {
    const items = [];
    const backend = await this._checkBackend(items);
    const appConfig = await this._checkAppConfig(items, backend.ok);
    const resource = this._checkResources(items);
    this._checkVoiceSettings(items, appConfig);

    const status = this._overallStatus(items);
    return {
      status,
      summary: this._buildSummary(status, items),
      backend,
      appConfig,
      resource,
      items,
      checkedAt: Date.now(),
    };
  }

  async _checkBackend(items) {
    try {
      const payload = await this._backendClient.fetchHealth();
      const ok = String(payload?.status || "").toLowerCase() === "ok";
      items.push({
        id: "backend",
        label: "后端连接",
        status: ok ? "ok" : "warning",
        message: ok ? "后端在线。" : "后端有响应，但状态不是 ok。",
      });
      return { ok, payload, error: "" };
    } catch (error) {
      const message = String(error?.message || error || "后端不可用");
      items.push({
        id: "backend",
        label: "后端连接",
        status: "error",
        message: "后端暂时连不上，请先启动 Python 服务。",
        detail: message,
      });
      return { ok: false, payload: null, error: message };
    }
  }

  async _checkAppConfig(items, backendOk) {
    if (!backendOk) {
      items.push({
        id: "app_config",
        label: "后端配置",
        status: "warning",
        message: "后端未连接，暂时无法读取配置。",
      });
      return null;
    }

    try {
      const config = await this._backendClient.fetchAppConfig();
      items.push({
        id: "app_config",
        label: "后端配置",
        status: "ok",
        message: "已读取桌宠需要的基础配置。",
      });
      return config || {};
    } catch (error) {
      items.push({
        id: "app_config",
        label: "后端配置",
        status: "warning",
        message: "后端在线，但配置接口不可读。",
        detail: String(error?.message || error || ""),
      });
      return null;
    }
  }

  _checkResources(items) {
    const manifest = this._getManifest?.();
    const outfitName = String(this._getOutfit?.() || "").trim();
    const outfit = findOutfit(manifest, outfitName);
    const emotions = listEmotions(outfit);
    const availableKeys = new Set(emotions.flatMap((item) => [item.id, item.name, ...item.aliases].map(normalizeKey)));
    const missingRequired = REQUIRED_EMOTIONS.filter((id) => !availableKeys.has(normalizeKey(id)));
    const missingRecommended = RECOMMENDED_EMOTIONS.filter((id) => !availableKeys.has(normalizeKey(id)));

    if (!manifest) {
      items.push({
        id: "resource_manifest",
        label: "资源单",
        status: "error",
        message: "没有拿到资源单，立绘可能只能走兜底路径。",
      });
    } else {
      items.push({
        id: "resource_manifest",
        label: "资源单",
        status: "ok",
        message: "资源单已加载。",
      });
    }

    if (!outfit) {
      items.push({
        id: "outfit",
        label: "当前角色",
        status: "error",
        message: `找不到当前服装/角色：${outfitName || "未设置"}。`,
      });
    } else {
      items.push({
        id: "outfit",
        label: "当前角色",
        status: "ok",
        message: `${outfit.id || outfit.name || outfitName} 已就绪，包含 ${emotions.length} 个表情。`,
      });
    }

    if (missingRequired.length > 0) {
      items.push({
        id: "required_emotions",
        label: "必需表情",
        status: "error",
        message: `缺少必需表情：${missingRequired.join("、")}。`,
      });
    } else {
      items.push({
        id: "required_emotions",
        label: "必需表情",
        status: "ok",
        message: "基础表情齐全。",
      });
    }

    if (missingRecommended.length > 0) {
      items.push({
        id: "recommended_emotions",
        label: "氛围表情",
        status: "warning",
        message: `有 ${missingRecommended.length} 个推荐表情缺失，表现层会自动降级。`,
        detail: missingRecommended.join("、"),
      });
    } else {
      items.push({
        id: "recommended_emotions",
        label: "氛围表情",
        status: "ok",
        message: "推荐氛围表情已齐全。",
      });
    }

    return {
      outfit: outfit?.id || outfitName || "",
      count: emotions.length,
      available: emotions.map((item) => item.id),
      missingRequired,
      missingRecommended,
    };
  }

  _checkVoiceSettings(items, appConfig) {
    const voiceEnabled = this._getVoiceEnabled?.() !== false;
    const voiceInputEnabled = this._getVoiceInputEnabled?.() === true;
    const streamingTtsEnabled = appConfig ? appConfig.streaming_tts_enabled !== false : null;

    items.push({
      id: "tts_setting",
      label: "语音播放",
      status: voiceEnabled && streamingTtsEnabled !== false ? "ok" : "warning",
      message: voiceEnabled
        ? streamingTtsEnabled === false
          ? "前端已开启，但后端配置显示 streaming TTS 关闭。"
          : "前端语音播放已开启。"
        : "前端语音播放关闭，需要时可从菜单打开。",
    });

    items.push({
      id: "asr_setting",
      label: "语音输入",
      status: voiceInputEnabled ? "ok" : "warning",
      message: voiceInputEnabled
        ? "语音输入入口已开启，识别能力会在录音时验证。"
        : "语音输入入口关闭，需要时可从菜单打开。",
    });
  }

  _overallStatus(items) {
    if (items.some((item) => item.status === "error")) return "error";
    if (items.some((item) => item.status === "warning")) return "warning";
    return "ok";
  }

  _buildSummary(status, items) {
    if (status === "ok") return "启动自检通过，桌宠表现层已就绪。";
    const errors = items.filter((item) => item.status === "error").length;
    const warnings = items.filter((item) => item.status === "warning").length;
    if (errors) return `启动自检发现 ${errors} 个错误、${warnings} 个注意项。`;
    return `启动自检发现 ${warnings} 个注意项。`;
  }
}

function findOutfit(manifest, outfitName) {
  const outfits = Array.isArray(manifest?.characters?.outfits) ? manifest.characters.outfits : [];
  if (!outfits.length) return null;
  const target = String(outfitName || manifest?.defaults?.outfit || "").trim();
  if (!target) return outfits[0] || null;
  const targetKey = normalizeKey(target);
  return (
    outfits.find((item) => {
      const values = [item?.id, item?.name, ...(Array.isArray(item?.aliases) ? item.aliases : [])]
        .map((value) => String(value || "").trim())
        .filter(Boolean);
      return values.some((value) => value === target || normalizeKey(value) === targetKey);
    }) ||
    outfits.find((item) => normalizeKey(item?.id) === normalizeKey(manifest?.defaults?.outfit)) ||
    outfits[0] ||
    null
  );
}

function listEmotions(outfit) {
  return (Array.isArray(outfit?.emotions) ? outfit.emotions : [])
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      id: String(item.id || "").trim(),
      name: String(item.name || item.id || "").trim(),
      aliases: (Array.isArray(item.aliases) ? item.aliases : [])
        .map((alias) => String(alias || "").trim())
        .filter(Boolean),
    }))
    .filter((item) => item.id);
}

function normalizeKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}

export { HealthCheckService };
