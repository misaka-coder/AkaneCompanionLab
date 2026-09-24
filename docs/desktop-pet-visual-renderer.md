# Desktop Pet Visual Renderer

Akane Next currently renders static portrait images, but the desktop pet should not depend on static PNGs forever. The renderer boundary is intentionally thin:

- Dialogue, tools, music, voice, and desktop context continue to decide `emotion` and `motion`.
- `desktop_pet_next/src/visual-renderer.js` translates those decisions into the current visual implementation.
- Today the implementation is `static_portrait`: set the portrait image and CSS motion.
- A future Live2D implementation should keep the same public calls and swap the internals.

## Current Adapter Contract

The main window uses:

- `setCharacterLabel(name)`: update alt/title/accessibility labels.
- `setExpression(entry, { force })`: show the resolved expression entry.
- `setMotion(motion, { restart })`: apply `idle`, `thinking`, `speaking`, or `click`.
- `getStatus()`: expose the active renderer mode to settings snapshots and diagnostics.

The renderer status currently reports `live2dReady: false`. That is deliberate: the skeleton is present, but no SDK, model loading, or lip-sync path is active yet.

## Future Live2D Shape

When Live2D becomes a real target, prefer adding fields to the Creator Kit character metadata instead of hardcoding paths in the shell. A likely shape:

```json
{
  "appearance": {
    "renderer": {
      "mode": "live2d",
      "model3_path": "assets/live2d/model.model3.json",
      "expression_map": {
        "开心": "expressions/happy.exp3.json"
      },
      "motion_map": {
        "idle": "motions/idle.motion3.json",
        "speaking": "motions/talk.motion3.json"
      },
      "lip_sync": true
    }
  }
}
```

The important rule is that `/think` and resource prompts still talk in terms of Akane emotions and motions. The renderer decides whether those become PNG images, CSS motion, Live2D expressions, Live2D motions, or lip-sync parameters.
