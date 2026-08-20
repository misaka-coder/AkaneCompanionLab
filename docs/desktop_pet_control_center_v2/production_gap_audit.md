# Control Center V2 Production Gap Audit

Status: active migration checklist, 2026-08-20.

## Decision

V2 remains the production-shaped candidate. `control-center-lab.html` is still the only default settings authority until the replacement gates below pass. New work goes into V2 components; the legacy renderer receives only regression fixes. Cutover must delete or reduce the legacy renderer to a documented thin adapter instead of keeping two long-lived settings products.

## Current coverage

| Destination | V2 status | Real source/action boundary | Migration decision |
|---|---|---|---|
| Overview | implemented | unified control-center snapshot + Tauri live snapshot | keep and polish |
| Chat | implemented | scoped session history + `sendChatMessage` / stop / new-session commands | keep; add history paging later |
| Character & Appearance | implemented | character runtime/resource manifest + workshop/folder/switch actions | keep; workshop remains authoring authority |
| Abilities & Permissions | first production slice implemented | `abilitiesRuntime` from `/capabilities`; `abilities.approvalPolicy.save` | expand only behind existing provider/MCP/workflow actions |
| Voice & Wake | implemented | `voiceRuntime` + Tauri live snapshot + existing voice settings commands | keep; character workshop remains voice-profile authority |
| System & Diagnostics | implemented | health/diagnostics/metrics + Tauri live state + existing recovery/debug commands | keep; add events only after a real event source exists |

Music stays in Overview and the quick card until its real feature density requires a separate V2 page. Desktop sensing belongs with Abilities & Permissions or System & Diagnostics; it should not become another top-level page by default.

## Legacy surfaces that must not be copied

- Mock provider/device names, sample recognition records, sample queue rows, and UI-template mood modes.
- Deferred actions that have no host or backend execution boundary.
- Raw tool names, secret fields, internal paths, prompt text, or capability schema dumps.
- Live2D future placeholders presented as current capability.
- Large explanatory walls that displace the task the user opened the page to perform.

## Required replacement gates

1. Every V2 field comes from a real snapshot or an honest empty/failed state.
2. Every enabled control has an existing action boundary and structured result.
3. Button feedback covers pressed, pending, confirmed, failed, and execution-unknown where applicable.
4. Character workshop remains the only full character-pack authoring authority.
5. The V2 shell renders before backend hydration and preserves the last trustworthy state during refresh.
6. Overview, Chat, Appearance, Abilities, Voice, and Diagnostics pass wide/narrow viewport checks.
7. Real Tauri tests cover connection loss, repeated click, character switch, slow action, and local storage failure.
8. Default entry changes only in a dedicated cutover commit that also removes or thins the old renderer.

## Recommended order

1. Abilities & Permissions summary and approval mode.
2. Voice & Wake with real TTS/ASR toggles, volume/speed, wake settings, and observed short-test feedback. (complete)
3. System & Diagnostics with connection, metrics, visible failures, and recovery actions. (complete; no fabricated event timeline)
4. Tauri acceptance pass and snapshot/action parity report.
5. Production cutover plus legacy renderer deletion/thin-adapter conversion.
