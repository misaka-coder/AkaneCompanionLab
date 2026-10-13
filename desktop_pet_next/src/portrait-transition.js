const DURATION_MS = 180;

/** A decoded portrait replaces the current one without fading its silhouette. */
export function createPortraitTransition({ image } = {}) {
  const view = image?.ownerDocument?.defaultView;
  const reduced = view?.matchMedia?.('(prefers-reduced-motion: reduce)');
  const supported = view?.CSS?.supports?.('mix-blend-mode', 'plus-lighter');
  let running = null;
  let disposed = false;

  function finish(transition = running) {
    if (!transition || running !== transition) return;
    running = null;
    transition.animations.forEach(animation => animation.cancel());
    transition.previous.remove();
    image.classList.remove('portrait-transition-layer');
    transition.parent.classList.remove('is-portrait-transitioning');
  }

  function setImage(url) {
    if (!image || disposed) return;
    finish();
    const parent = image.parentElement;
    const canFade = supported && !reduced?.matches && parent?.classList.contains('portrait-motion') &&
      typeof image.animate === 'function' && image.complete && image.naturalWidth > 0 &&
      image.dataset.imageState === 'ready' && image.getAttribute('src') !== url;
    if (!canFade) {
      image.src = url;
      return;
    }

    const previous = image.cloneNode(false);
    previous.removeAttribute('id');
    previous.removeAttribute('title');
    previous.alt = '';
    previous.setAttribute('aria-hidden', 'true');
    previous.draggable = false;
    previous.classList.add('portrait-transition-layer', 'portrait-transition-frame');
    parent.insertBefore(previous, image);
    parent.classList.add('is-portrait-transitioning');
    image.classList.add('portrait-transition-layer');
    image.src = url;

    const transition = running = { previous, parent, animations: [] };
    try {
      const options = { duration: DURATION_MS, easing: 'ease-in-out', fill: 'both' };
      transition.animations.push(previous.animate([{ opacity: 1 }, { opacity: 0 }], options));
      transition.animations.push(image.animate([{ opacity: 0 }, { opacity: 1 }], options));
      // Both layers use the same clock, so their opacity always adds up to one.
      const start = image.ownerDocument.timeline.currentTime;
      transition.animations.forEach(animation => { animation.startTime = start; });
      Promise.all(transition.animations.map(animation => animation.finished))
        .then(() => finish(transition), () => finish(transition));
    } catch {
      finish(transition);
    }
  }

  function handleReducedMotion() {
    if (reduced.matches) finish();
  }
  reduced?.addEventListener('change', handleReducedMotion);

  return {
    setImage,
    finish,
    getStatus: () => ({ active: Boolean(running), durationMs: DURATION_MS }),
    dispose() {
      disposed = true;
      finish();
      reduced?.removeEventListener('change', handleReducedMotion);
    }
  };
}
