/** Local visual selection from the existing authoritative care snapshot. */
export function normalizeRestingEmotions(value) {
  const source = value && typeof value === 'object' ? value : {};
  const rule = (entry) => {
    if (!entry || typeof entry !== 'object' || !String(entry.emotion || '').trim()) return null;
    const bounded = (value, fallback) => Number.isFinite(Number(value)) && value !== null && value !== ''
      ? Math.max(0, Math.min(100, Number(value))) : fallback;
    const enterBelow = bounded(entry.enter_below, 25);
    return {
      emotion: String(entry.emotion).trim(), enterBelow,
      recoverAt: Math.max(enterBelow, bounded(entry.recover_at, 35))
    };
  };
  return { hungry: rule(source.hungry), tired: rule(source.tired) };
}

export function createRestingExpressionResolver() {
  let previousKey = '';
  let hungry = false;
  let tired = false;
  function resolve({ scopeKey, rules, care, defaultEmotion, musicEmotion = '' }) {
    const key = JSON.stringify([scopeKey, rules]);
    if (key !== previousKey) { hungry = false; tired = false; previousKey = key; }
    const isLow = (rule, value, active) => {
      if (!rule || value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return false;
      return active ? Number(value) < rule.recoverAt : Number(value) < rule.enterBelow;
    };
    hungry = isLow(rules?.hungry, care?.hunger, hungry);
    tired = isLow(rules?.tired, care?.energy, tired);
    return (tired && rules.tired.emotion) || (hungry && rules.hungry.emotion) || musicEmotion || defaultEmotion;
  }
  return { resolve };
}
