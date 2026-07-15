const LOCAL_DEFAULT_INSTANCE_ID = "local-default";
const SAFE_INSTANCE_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const STORAGE_PREFIX = "akane.instance";

let activeInstanceId = "";

export function normalizeInstanceId(value) {
  const instanceId = String(value || "").trim();
  if (!SAFE_INSTANCE_ID.test(instanceId)) {
    throw new Error("invalid_instance_id");
  }
  return instanceId;
}

export function bindInstanceStorage(value) {
  activeInstanceId = normalizeInstanceId(value);
  return activeInstanceId;
}

export function getBoundInstanceId() {
  return activeInstanceId;
}

export function instanceStorageKey(logicalKey, instanceId = activeInstanceId) {
  const safeInstanceId = normalizeInstanceId(instanceId);
  const key = String(logicalKey || "").trim();
  if (!key) throw new Error("invalid_storage_key");
  return `${STORAGE_PREFIX}.${encodeURIComponent(safeInstanceId)}.${key}`;
}

export function getInstanceStorageItem(logicalKey, options = {}) {
  const storage = resolveStorage(options.storage);
  if (!storage || !activeInstanceId) return null;
  const key = instanceStorageKey(logicalKey);
  const scopedValue = storage.getItem(key);
  if (scopedValue !== null) return scopedValue;

  const legacyKey = String(options.legacyKey || "").trim();
  if (activeInstanceId !== LOCAL_DEFAULT_INSTANCE_ID || !legacyKey) return null;
  const legacyValue = storage.getItem(legacyKey);
  if (legacyValue === null) return null;
  storage.setItem(key, legacyValue);
  return legacyValue;
}

export function setInstanceStorageItem(logicalKey, value, options = {}) {
  const storage = resolveStorage(options.storage);
  if (!storage || !activeInstanceId) return false;
  storage.setItem(instanceStorageKey(logicalKey), String(value ?? ""));
  return true;
}

export function removeInstanceStorageItem(logicalKey, options = {}) {
  const storage = resolveStorage(options.storage);
  if (!storage || !activeInstanceId) return false;
  storage.removeItem(instanceStorageKey(logicalKey));
  return true;
}

function resolveStorage(candidate) {
  if (candidate && typeof candidate.getItem === "function") return candidate;
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}
