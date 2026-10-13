/** One lifetime for timers, DOM listeners and late native subscriptions. */
export function createLifecycle(window) {
  let disposed = false;
  const cleanups = new Set();
  const timeouts = new Set();
  const intervals = new Set();
  const frames = new Set();

  function track(cleanup) {
    if (typeof cleanup !== "function") return cleanup;
    if (disposed) cleanup();
    else cleanups.add(cleanup);
    return cleanup;
  }

  function on(target, event, handler, options) {
    const listener = (...args) => { if (!disposed) return handler(...args); };
    target.addEventListener(event, listener, options);
    return track(() => target.removeEventListener(event, listener, options));
  }

  function setTimeout(callback, delay, ...args) {
    if (disposed) return 0;
    const id = window.setTimeout(() => {
      timeouts.delete(id);
      if (!disposed) callback(...args);
    }, delay);
    timeouts.add(id);
    return id;
  }

  function clearTimeout(id) {
    timeouts.delete(id);
    window.clearTimeout(id);
  }

  function setInterval(callback, delay, ...args) {
    if (disposed) return 0;
    const id = window.setInterval(() => { if (!disposed) callback(...args); }, delay);
    intervals.add(id);
    return id;
  }

  function clearInterval(id) {
    intervals.delete(id);
    window.clearInterval(id);
  }

  function requestAnimationFrame(callback) {
    if (disposed) return 0;
    const id = window.requestAnimationFrame(time => {
      frames.delete(id);
      if (!disposed) callback(time);
    });
    frames.add(id);
    return id;
  }

  function cancelAnimationFrame(id) {
    frames.delete(id);
    window.cancelAnimationFrame(id);
  }

  function dispose() {
    if (disposed) return [];
    disposed = true;
    for (const id of timeouts) window.clearTimeout(id);
    for (const id of intervals) window.clearInterval(id);
    for (const id of frames) window.cancelAnimationFrame(id);
    timeouts.clear(); intervals.clear(); frames.clear();
    const errors = [];
    for (const cleanup of cleanups) {
      try { cleanup(); } catch (error) { errors.push(error); }
    }
    cleanups.clear();
    return errors;
  }

  return { track, on, setTimeout, clearTimeout, setInterval, clearInterval,
    requestAnimationFrame, cancelAnimationFrame, dispose,
    get disposed() { return disposed; } };
}
