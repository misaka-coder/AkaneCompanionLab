# Character Workshop UI Redesign

Status: design baseline for the post-V1 visual pass
Updated: 2026-06-05

## Direction

The workshop should feel like a quiet, efficient character editor with a light anime sensibility. It is not a marketing page, not a generic settings page, and not a role-specific themed window.

Core feel:

- Comfortable on first sight: calm, readable, and naturally organized.
- Efficient for ordinary users: a user should see where to pick a character, edit it, upload assets, calibrate it, and test it without reading docs.
- Lightly characterful: use small visual details, preview states, and friendly microcopy, but never make the UI speak as Akane or any single character.
- Creator-facing: the UI should respect that users are building different characters, not only configuring one existing pet.

## Problems To Fix

The current workshop UI is functionally useful, but visually it still feels like an implementation surface:

- Too many independent card blocks make the page look scattered.
- The list tab risks becoming a stack of large rounded cards when more packs exist.
- The blue-heavy palette makes hierarchy and action priority harder to read.
- Typography is oversized or inconsistent in dense areas.
- Empty areas feel accidental instead of intentionally reserved for work.
- Help text and editing surfaces are visually separated in a way that adds noise.
- Horizontal overflow must be treated as a layout bug, not as acceptable polish debt.

## Design Principles

### 1. Character List First, Not Card First

The first screen should open on the character list, but the list should be compact and structured:

- Use row/list/table-like entries, not large rounded cards.
- Each row shows name, pack id, readiness, outfit/expression counts, and last-used or active status.
- Keep actions in a predictable command area: edit, apply, export/open.
- Use a selected-row state plus a detail/inspector panel instead of expanding each character into its own large block.
- Character thumbnails are allowed, but they should be small anchors, not the whole layout.

### 2. Clear Fixed Hierarchy

Avoid dropdown-driven primary structure. The workshop should use visible navigation and stable regions:

- Global shell: title, active character summary, and primary commands.
- Character index: always easy to return to.
- Work area: the current task, such as persona, portraits, calibration, or test chat.
- Inspector/status area: warnings, readiness, diagnostics, and contextual help.

Primary sections should remain visible as tabs or a side rail:

- 角色列表
- 角色设定
- 立绘管理
- 显示校准
- 测试对话

Within a section, use labeled groups and anchors instead of dropdown accordions. Collapsing core editing fields would make the flow feel harder to understand.

### 3. Fewer Cards, More Work Surfaces

Cards should be reserved for repeated items or genuinely framed tools. Page sections should be full-width bands, rows, lists, or editor panels.

Do:

- Use compact rows for character packs.
- Use slim grouped panels for form sections.
- Use a dedicated preview frame for calibration and portrait testing.
- Use inline status rows for readiness and validation.

Avoid:

- Big rounded cards for every character.
- Cards nested inside cards.
- Decorative card grids that do not map to a real task.
- Large empty side panels without a clear job.

### 4. Quiet Palette With Small Accents

The base palette should be neutral and soft, with accents used for meaning:

- Base: warm white / cool gray surfaces.
- Text: near-black blue-gray for main text, softer gray for secondary text.
- Primary accent: restrained blue for active selection and main actions.
- Secondary accent: teal or green for saved/ready state.
- Warning accent: amber for missing assets or validation.
- Small anime accent: coral or pink only in tiny highlights, not as a page theme.

Blue should no longer dominate the whole window. The workshop should not read as a single-hue UI.

### 5. Typography As Structure

Use type to make hierarchy obvious:

- Window title: compact, not hero-sized.
- Section titles: clear and modest.
- Labels: slightly stronger than help text, never oversized.
- Dense data rows: 12-14px range, with enough line height for Chinese text.
- Buttons: stable height, short labels, no wrapping surprises.

Suggested font stack:

```css
font-family:
  "Inter", "MiSans", "Microsoft YaHei UI", "Microsoft YaHei",
  "PingFang SC", "Source Han Sans SC", "Segoe UI", system-ui, sans-serif;
```

Do not scale type with viewport width. Use responsive layout changes instead.

## Information Architecture

### Recommended Shell

Use a three-zone editor layout on desktop:

1. Left rail/index: character rows and create/import entry points.
2. Center workbench: selected task content.
3. Right inspector: readiness, preview, warnings, and diagnostics.

The existing tab model can stay, but the visual shape should make the list/index feel persistent. A practical first implementation can keep the DOM tabs and restyle them into a quiet section rail or compact tab strip.

### First Screen

Primary goal: select, create, import, or apply a character.

Content:

- Compact character list with selected row.
- Empty/first-use band above the list only when needed.
- Inspector with selected character readiness:
  - pack id
  - schema/status
  - default outfit/emotion
  - expression count
  - calibration status
  - memory scope note
- Primary action: edit selected character.
- Secondary actions: apply, import, export, open folder, refresh.

### Persona Editor

Use visible groups rather than dropdowns:

- 身份: name, app name, user title, self-reference, relationship.
- 性格: keywords, speaking style.
- 语气素材: catchphrases, example lines.
- 边界: boundaries, proactive style.
- 补充设定: extra setting.

The help panel should become an inline assistant strip or right inspector summary, not a separate wall of long text. Help copy should be short and task-specific.

### Portraits

Design as an asset inventory:

- Outfit list on the left or top.
- Expression grid as small tiles.
- Preview and readiness on the right.
- Missing default outfit/expression should appear as actionable warning rows.

### Calibration

Design as a real editor:

- Large preview stage in the workbench.
- Numeric/range controls in the inspector.
- Save/auto/reset commands close to the controls.
- The bubble anchor marker should be precise, not decorative.

### Test Chat

Design as a testing console:

- Chat log and input in the workbench.
- Current visual preview and prompt-field summary in the inspector.
- Memory scope should be visible but calm.
- "应用到桌宠" is a real action and should remain prominent.

## Microcopy

Use friendly, neutral copy that helps users act. A little flavor is fine, but it must not bind the interface to one character.

Good:

- "这个角色还缺默认表情。"
- "先放一张 normal 表情，桌宠就能上岗。"
- "测试会话是隔离的，不会混进正式桌宠记忆。"

Avoid:

- UI pretending to be Akane or any selected character.
- Overly cute jokes on critical warnings.
- Long explanatory paragraphs in the main workflow.

## Implementation Slices

### Slice 1: Layout And Tokens

- Replace blue glass-heavy background with a neutral app surface.
- Fix horizontal overflow across desktop and mobile widths.
- Establish type scale, colors, spacing, border radius, and button styles.
- Keep existing functionality and DOM hooks intact.

Validation:

- `cd desktop_pet_next && npm run build`
- `python -m unittest tests.test_desktop_pet_frontend_contract`
- `git diff --check`
- Browser smoke on `workshop.html` desktop and narrow viewport.

### Slice 2: Character List Workbench

- Redesign pack list into compact rows.
- Add selected-row and readiness/status styling.
- Make first-use create/import band calmer and less card-like.
- Improve action placement without introducing fake actions.

### Slice 3: Persona Editing Flow

- Reduce form visual noise.
- Group fields into clear bands.
- Convert long help panel into concise contextual guidance.
- Preserve save boundary and local draft behavior.

### Slice 4: Portraits, Calibration, Test Chat Polish

- Make portraits feel like an asset inventory.
- Make calibration feel like a tool with a stable preview stage.
- Make test chat feel like a controlled diagnostics console.
- Keep all backend/Tauri action boundaries unchanged.

## Non-Goals

- Do not redesign the control center in this pass.
- Do not add fake backend/Tauri actions to make buttons feel complete.
- Do not change character-pack schema for visual polish.
- Do not depend on new character art assets before the user has them ready.
- Do not theme the whole workshop around Akane.

## Acceptance Checklist

- First screen clearly answers: "Which character am I editing?"
- Character packs are compact rows, not large card blocks.
- Primary navigation is visible and stable; no dropdown is required for core workflow.
- Ordinary users can find create/import/edit/apply/test without docs.
- Text hierarchy is calm and consistent.
- Colors are not dominated by one blue gradient theme.
- No horizontal scrollbar appears in normal desktop or mobile widths.
- Deferred or unavailable actions remain visibly honest and do not fake success.
- Browser smoke verifies list, persona, portraits, calibration, and test tabs still render.
