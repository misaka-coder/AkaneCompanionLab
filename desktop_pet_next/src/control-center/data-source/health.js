import { buildBackendUrl, fetchJson } from "./http.js";

export function createBackendHealthVerifier({ fetchImpl, baseUrl, expectedInstanceId, setFallbackReason }) {
  let verifiedHealth = null;
  let verifiedHealthAt = 0;
  let healthVerificationPromise = null;

  async function ensureVerifiedBackend({ force = false } = {}) {
    if (!force && verifiedHealth && Date.now() - verifiedHealthAt < 2500) return verifiedHealth;
    if (healthVerificationPromise) return healthVerificationPromise;
    healthVerificationPromise = (async () => {
      const health = await fetchJson(fetchImpl, buildBackendUrl(baseUrl, "/health", { t: String(Date.now()) }));
      const payload = health?.data && typeof health.data === "object" ? health.data : {};
      const actualInstanceId = String(payload.instance_id || "").trim();
      if (
        !health.ok ||
        String(payload.status || "") !== "ok" ||
        String(payload.root_binding || "") !== "valid" ||
        !actualInstanceId ||
        (expectedInstanceId && actualInstanceId !== expectedInstanceId)
      ) {
        verifiedHealth = null;
        verifiedHealthAt = 0;
        setFallbackReason(expectedInstanceId && actualInstanceId && actualInstanceId !== expectedInstanceId
          ? "instance-id-mismatch"
          : "instance-health-unavailable");
        return null;
      }
      verifiedHealth = health;
      verifiedHealthAt = Date.now();
      return health;
    })();
    try {
      return await healthVerificationPromise;
    } finally {
      healthVerificationPromise = null;
    }
  }
  return ensureVerifiedBackend;
}
