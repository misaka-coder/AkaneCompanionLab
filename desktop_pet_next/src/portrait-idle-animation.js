/** Optional, local animation textures stay separate from model-selectable expressions. */
export function normalizeIdleBlink(value) {
  if (!value || typeof value !== 'object') return null;
  const asset = String(value.asset || '').trim();
  if (!asset || /[\\:?%#\0]/.test(asset) || asset.startsWith('/') || asset.split('/').some(p => !p || p === '.' || p === '..')) return null;
  const regions = (Array.isArray(value.regions) ? value.regions : []).slice(0, 4).map(r => {
    const { x, y, width, height } = r || {};
    return { x: Number(x), y: Number(y), width: Number(width), height: Number(height) };
  }).filter(r => Object.values(r).every(Number.isFinite) && r.x >= 0 && r.y >= 0 && r.width > 0 && r.height > 0 && r.x + r.width <= 1 && r.y + r.height <= 1);
  // Eye patches are mandatory: the original silhouette and body remain untouched.
  if (!regions.length) return null;
  return { asset, regions, emotion: String(value.emotion || 'normal').trim(), outfit: String(value.outfit || '').trim() };
}

export function createPortraitIdleAnimation({ stage, image, canIdle = () => true, onError, onMotionUpdate, random = Math.random } = {}) {
  const document = image?.ownerDocument;
  const window = document?.defaultView;
  if (!stage?.dataset || !image?.parentElement || !window || !document?.createElement) return {
    setExpression() {}, setMusic() {}, sync() {}, setLayout() {}, dispose() {},
    getStatus: () => ({ blink: 'unavailable', music: false })
  };
  const overlay = document.createElement('img');
  overlay.className = 'portrait-blink-frame';
  overlay.alt = ''; overlay.setAttribute('aria-hidden', 'true'); overlay.draggable = false;
  image.parentElement.append(overlay);
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  const cache = new Map();
  let disposed = false, revision = 0, config = null, ready = false, failure = '';
  let canvasSize = {};
  let blinkTimer = 0, phaseTimer = 0, motionTimer = 0;
  let musicActive = false, musicEmotion = '';

  const clear = id => window.clearTimeout(id);
  const visible = () => !disposed && document.visibilityState !== 'hidden' && !reduced?.matches;
  const allowed = () => visible() && stage.dataset.motion === 'idle' && !stage.dataset.expressionPending && canIdle();
  const setClosed = closed => { stage.dataset.blink = closed ? 'closed' : 'open'; };
  function cancelBlink() {
    clear(blinkTimer); clear(phaseTimer); blinkTimer = phaseTimer = 0;
    setClosed(false);
  }
  function scheduleBlink(delay = 4500 + random() * 4000) {
    if (blinkTimer || !config || !ready || !visible()) return;
    blinkTimer = window.setTimeout(() => {
      blinkTimer = 0;
      if (!allowed()) { scheduleBlink(1600); return; }
      closeOnce(random() < 0.13);
    }, delay);
  }
  function closeOnce(double) {
    if (!allowed() || !ready) { sync(); return; }
    setClosed(true);
    phaseTimer = window.setTimeout(() => {
      phaseTimer = 0; setClosed(false);
      if (double && allowed()) phaseTimer = window.setTimeout(() => { phaseTimer = 0; closeOnce(false); }, 130);
      else scheduleBlink();
    }, 135);
  }
  function musicAllowed() {
    return musicActive && stage.dataset.expression === musicEmotion && allowed();
  }
  function syncMusic() {
    const active = musicAllowed();
    stage.dataset.passiveMotion = active ? 'music' : '';
    clear(motionTimer); motionTimer = 0;
    if (!active) return;
    // Only map the already-cached alpha geometry; never decode pixels here.
    const update = () => {
      motionTimer = 0;
      if (!musicAllowed()) { stage.dataset.passiveMotion = ''; return; }
      onMotionUpdate?.();
      motionTimer = window.setTimeout(update, 200);
    };
    motionTimer = window.setTimeout(update, 200);
  }
  function sync() {
    syncMusic();
    if (!allowed()) cancelBlink();
    scheduleBlink(allowed() ? undefined : 1600);
  }
  function load(url) {
    if (cache.has(url)) return cache.get(url);
    const promise = new Promise((resolve, reject) => {
      const probe = document.createElement('img');
      probe.onload = () => { probe.onload = probe.onerror = null; resolve(probe); };
      probe.onerror = () => { probe.onload = probe.onerror = null; reject(new Error('idle_blink_image_load_failed')); };
      probe.src = url;
    });
    cache.set(url, promise);
    if (cache.size > 8) cache.delete(cache.keys().next().value);
    return promise;
  }
  function setExpression(next, size = {}) {
    const token = ++revision;
    cancelBlink(); ready = false; failure = ''; config = next || null;
    canvasSize = size;
    overlay.removeAttribute('src');
    syncMusic();
    if (!config) return;
    if (!config.url) { failure = 'idle_blink_asset_unavailable'; onError?.(new Error(failure)); return; }
    const current = config;
    load(current.url).then(probe => {
      if (disposed || revision !== token) return;
      if (probe.naturalWidth !== size.width || probe.naturalHeight !== size.height) throw new Error('idle_blink_canvas_mismatch');
      overlay.src = current.url;
      overlay.style.maskImage = current.regions.map(r => `radial-gradient(ellipse ${r.width * 50}% ${r.height * 50}% at ${(r.x + r.width / 2) * 100}% ${(r.y + r.height / 2) * 100}%, #000 78%, transparent 100%)`).join(', ');
      updateMaskLayout();
      ready = true; sync();
    }).catch(error => {
      if (disposed || revision !== token) return;
      failure = error.message; ready = false; onError?.(error);
    });
  }
  function setLayout() {
    overlay.style.transform = image.style.transform;
    overlay.style.transformOrigin = image.style.transformOrigin;
    updateMaskLayout();
  }
  function updateMaskLayout() {
    if (!config || !canvasSize.width || !canvasSize.height || !image.clientWidth || !image.clientHeight) return;
    // Regions are measured on the PNG, not on any empty space added by object-fit: contain.
    const scale = Math.min(image.clientWidth / canvasSize.width, image.clientHeight / canvasSize.height);
    overlay.style.maskSize = `${canvasSize.width * scale}px ${canvasSize.height * scale}px`;
    overlay.style.maskPosition = 'center';
  }
  const resizeObserver = window.ResizeObserver ? new window.ResizeObserver(updateMaskLayout) : null;
  resizeObserver?.observe(image);
  document.addEventListener('visibilitychange', sync);
  reduced?.addEventListener('change', sync);
  return {
    setExpression, sync, setLayout,
    setMusic({ active = false, emotion = '' } = {}) { musicActive = Boolean(active); musicEmotion = emotion; sync(); },
    getStatus: () => ({ blink: failure || (ready ? 'ready' : config ? 'loading' : 'disabled'), music: stage.dataset.passiveMotion === 'music' }),
    dispose() {
      disposed = true; revision++; cancelBlink(); clear(motionTimer);
      document.removeEventListener('visibilitychange', sync); reduced?.removeEventListener('change', sync);
      resizeObserver?.disconnect();
      cache.clear(); overlay.remove(); delete stage.dataset.blink; delete stage.dataset.passiveMotion;
    }
  };
}
