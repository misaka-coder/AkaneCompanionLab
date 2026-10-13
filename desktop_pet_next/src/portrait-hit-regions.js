/** Small, cached alpha masks; native mouse handling only sees precomputed rectangles. */
export function buildAlphaHitMask({ data, width, height }, alphaThreshold = 32) {
  const rects = [];
  let previous = new Map();
  let opaque = true;
  for (let y = 0; y < height; y++) {
    const next = new Map();
    for (let x = 0; x < width;) {
      if (data[(y * width + x) * 4 + 3] <= alphaThreshold) { opaque = false; x++; continue; }
      const start = x;
      while (x < width && data[(y * width + x) * 4 + 3] > alphaThreshold) x++;
      const key = `${start}:${x}`;
      const run = previous.get(key) || { x: start, y, width: x - start, height: 0 };
      if (!previous.has(key)) rects.push(run);
      run.height++;
      next.set(key, run);
    }
    previous = next;
  }
  return {
    opaque,
    rects: rects.map(rect => ({ x: rect.x / width, y: rect.y / height, width: rect.width / width, height: rect.height / height }))
  };
}

export function createPortraitHitMaskCache({ document, maxEntries = 32, onError, onUpdate, resolveSource } = {}) {
  const entries = new Map();
  const pending = new Map();
  function read(image) {
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 144 / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('canvas_context_unavailable');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return buildAlphaHitMask(context.getImageData(0, 0, canvas.width, canvas.height));
  }
  function readWithCors(key) {
    // Existing portraits remain visible even if their server does not support CORS.
    const probe = document.createElement('img');
    pending.set(key, probe);
    probe.crossOrigin = 'anonymous';
    const finish = () => { probe.onload = null; probe.onerror = null; pending.delete(key); };
    probe.onload = () => {
      try { if (entries.has(key)) { entries.set(key, read(probe)); onUpdate?.(); } }
      catch (error) { onError?.(error); }
      finally { finish(); }
    };
    probe.onerror = () => { finish(); onError?.(new Error('portrait_alpha_cors_unavailable')); };
    probe.src = resolveSource?.(key) || key;
  }
  function get(image) {
    const key = image?.currentSrc || image?.src;
    if (!key || !image.complete || !image.naturalWidth || !image.naturalHeight) return null;
    if (entries.has(key)) {
      const mask = entries.get(key);
      entries.delete(key); entries.set(key, mask);
      return mask;
    }
    let mask = null;
    try {
      mask = read(image);
    } catch (error) {
      if (error.name === 'SecurityError') {
        entries.set(key, null);
        readWithCors(key);
      } else onError?.(error);
    }
    entries.set(key, mask);
    if (entries.size > maxEntries) entries.delete(entries.keys().next().value);
    return mask;
  }
  return { get, clear() {
    entries.clear();
    for (const probe of pending.values()) { probe.onload = null; probe.onerror = null; probe.removeAttribute('src'); }
    pending.clear();
  } };
}

export function containedPortraitRect(rect, naturalWidth, naturalHeight) {
  const scale = Math.min(rect.width / naturalWidth, rect.height / naturalHeight);
  const width = naturalWidth * scale, height = naturalHeight * scale;
  return {
    x: Number(rect.left ?? rect.x) + (rect.width - width) / 2,
    y: Number(rect.top ?? rect.y) + (rect.height - height) / 2,
    width, height
  };
}

export function alphaMaskHitRegions(mask, rect, ratio = 1) {
  return mask.rects.map(run => {
    const x = Math.floor((rect.x + run.x * rect.width) * ratio);
    const y = Math.floor((rect.y + run.y * rect.height) * ratio);
    return { kind: 'pet', polygon: [], rect: {
      x, y,
      width: Math.ceil((rect.x + (run.x + run.width) * rect.width) * ratio) - x,
      height: Math.ceil((rect.y + (run.y + run.height) * rect.height) * ratio) - y
    } };
  });
}
