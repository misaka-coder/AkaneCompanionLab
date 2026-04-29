/**
 * Abstract renderer interface for Akane desktop pet.
 * StaticSpriteRenderer implements this with <img> elements.
 * Live2DRenderer (future) will implement the same contract.
 */
class RendererInterface {
  /**
   * Initialize the renderer and attach to a container element.
   * @param {HTMLElement} container
   */
  async init(container) {
    throw new Error("Not implemented");
  }

  /**
   * Show a specific emotion for a given outfit.
   * @param {string} emotion - canonical emotion id (e.g. "normal", "shy")
   * @param {string} outfit - outfit id (e.g. "猫娘")
   * @param {string} backendUrl - base URL for asset loading
   */
  async showEmotion(emotion, outfit, backendUrl) {
    throw new Error("Not implemented");
  }

  /**
   * Return the root DOM element managed by this renderer.
   * @returns {HTMLElement}
   */
  getElement() {
    throw new Error("Not implemented");
  }

  /**
   * Clean up resources.
   */
  destroy() {
    throw new Error("Not implemented");
  }
}

export { RendererInterface };
