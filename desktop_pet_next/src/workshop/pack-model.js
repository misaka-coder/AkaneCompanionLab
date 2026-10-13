
export function buildPackReadinessIssues(pack) {
  if (!pack) return [];
  const issues = [];
  if (!getPackName(pack)) issues.push({ key: "name", label: "名称" });
  if (!pack.schemaVersion) issues.push({ key: "schema", label: "结构版本" });
  if (!pack.defaultOutfit) issues.push({ key: "outfit", label: "默认服装" });
  if (!pack.defaultEmotion) issues.push({ key: "emotion", label: "默认表情" });
  if (!Number(pack.assetCount || 0)) issues.push({ key: "assets", label: "立绘资源" });
  return issues;
}

export function normalizePacks(value) {
  return (Array.isArray(value) ? value : [])
    .map((item) => {
      const pack = item && typeof item === "object" ? item : {};
      const profile = pack.profile && typeof pack.profile === "object" ? pack.profile : {};
      const identity = profile.identity && typeof profile.identity === "object" ? profile.identity : {};
      const appearance = profile.appearance && typeof profile.appearance === "object" ? profile.appearance : {};
      return {
        id: String(pack.id || pack.packId || "").trim(),
        name: String(pack.appName || pack.name || identity.app_name || identity.appName || identity.name || "").trim(),
        characterId: String(pack.characterId || identity.id || "").trim(),
        characterName: String(pack.characterName || identity.name || "").trim(),
        schemaVersion: String(pack.schemaVersion || profile.schema_version || profile.schemaVersion || "").trim(),
        defaultOutfit: String(pack.defaultOutfit || appearance.default_outfit || appearance.defaultOutfit || "").trim(),
        defaultEmotion: String(pack.defaultEmotion || appearance.default_emotion || appearance.defaultEmotion || "").trim(),
        assetCount: Number(pack.assetCount || 0),
        source: String(pack.source || "").trim(),
        selected: false,
        /* keep the raw profile so the persona form can read it */
        _rawProfile: profile,
      };
    })
    .filter((pack) => pack.id)
    .sort((left, right) => getPackName(left).localeCompare(getPackName(right), "zh-Hans-CN"));
}

export function mergeSnapshotPacks(currentPacks, snapshotPacks) {
  const merged = new Map();
  for (const pack of Array.isArray(currentPacks) ? currentPacks : []) {
    if (pack?.id) merged.set(pack.id, pack);
  }
  for (const pack of Array.isArray(snapshotPacks) ? snapshotPacks : []) {
    if (!pack?.id) continue;
    const existing = merged.get(pack.id);
    if (!existing) {
      merged.set(pack.id, pack);
      continue;
    }
    merged.set(pack.id, {
      ...existing,
      ...pack,
      selected: false,
      _rawProfile: hasProfilePayload(pack._rawProfile) ? pack._rawProfile : existing._rawProfile,
    });
  }
  return [...merged.values()]
    .filter((pack) => pack.id)
    .sort((left, right) => getPackName(left).localeCompare(getPackName(right), "zh-Hans-CN"));
}

export function hasProfilePayload(profile) {
  return Boolean(profile && typeof profile === "object" && !Array.isArray(profile) && Object.keys(profile).length);
}

export function buildPackRoleLabel(pack, { isActive = false, isEditing = false } = {}) {
  if (isActive && isEditing) return "桌宠正在使用 · 工坊正在编辑";
  if (isActive) return "桌宠正在使用";
  if (isEditing) return "工坊正在编辑";
  return getPackReadinessSummary(pack);
}

export function getPackName(pack) {
  return String(pack?.name || pack?.characterName || pack?.characterId || "").trim();
}

export function getPackReadinessSummary(pack) {
  if (!pack) return "-";
  const issues = buildPackReadinessIssues(pack).filter((item) => item.key !== "schema");
  if (!issues.length) return "素材已就绪";
  return `待补：${issues.slice(0, 2).map((item) => item.label).join("、")}`;
}

export function buildPackMeta(pack) {
  const parts = [
    pack.defaultOutfit ? `服装：${displayAssetName(pack.defaultOutfit)}` : "未设默认服装",
    pack.defaultEmotion ? `表情：${displayAssetName(pack.defaultEmotion)}` : "未设默认表情",
    pack.assetCount ? `素材：${pack.assetCount} 个` : "暂无立绘素材",
  ].filter(Boolean);
  return parts.join(" · ");
}

export function displayAssetName(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const key = text.toLowerCase();
  const aliases = {
    default: "默认",
    normal: "普通",
    neutral: "普通",
    thinking: "思考",
    happy: "开心",
    shy: "害羞",
    angry: "生气",
    pout: "气鼓鼓",
    confused: "困惑",
    sleepy: "困倦",
    tired: "疲惫",
    listening: "倾听",
    music: "听歌",
    touched: "被摸头",
  };
  return aliases[key] || text;
}
