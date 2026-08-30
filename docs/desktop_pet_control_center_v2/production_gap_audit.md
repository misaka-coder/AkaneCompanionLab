# Control Center V2 Production Gap Audit

Status: cutover complete; experiential action audit in progress, updated 2026-08-30.

## Decision

V2 is the production implementation behind the sole settings authority, `control-center-lab.html`. `control-center-v2.html` and `settings.html` are compatibility redirects. The former monolithic renderer is reduced to a two-line stale-import adapter and its stylesheet is deleted; there is no second settings product.

## Current coverage

| Destination | V2 status | Real source/action boundary | Migration decision |
|---|---|---|---|
| Overview | implemented | unified control-center snapshot + Tauri live snapshot | keep and polish |
| Chat | implemented | scoped session history + `sendChatMessage` / stop / new-session commands | keep; add history paging later |
| Character & Appearance | implemented | character runtime/resource manifest + workshop/folder/switch actions | keep; workshop remains authoring authority |
| Abilities & Permissions | first production slice implemented | `abilitiesRuntime` from `/capabilities`; `abilities.approvalPolicy.save` | expand only behind existing provider/MCP/workflow actions |
| Voice & Wake | implemented | `voiceRuntime` + Tauri live snapshot + existing voice settings commands | keep; character workshop remains voice-profile authority |
| Model Service | implemented | `/control-center/model-service` + admin action bridge | keep; never return or log saved keys |
| System & Diagnostics | implemented | health/diagnostics/metrics + Tauri live state + existing recovery/debug commands | keep; add events only after a real event source exists |

Music stays in Overview and the quick card until its real feature density requires a separate V2 page. Desktop sensing belongs with Abilities & Permissions or System & Diagnostics; it should not become another top-level page by default.

## Post-cutover experiential repair

The cutover gates verify that enabled controls have executable boundaries, but they do not by themselves prove that the displayed state and the controlled runtime object are the same. The first live repair found that Overview could display Windows system media while its play/pause button still targeted the pet-local audio element.

- `music.togglePlayback` is now the explicit Overview action; the misleading `music.pause` toggle alias is removed from the control-center contract.
- The desktop host selects the active source: a loaded pet-local track controls the local audio element, otherwise a controllable Windows system-media session receives play/pause. A stopped but still-addressable session remains controllable; "fresh playback activity" and "controllable media session" are deliberately separate predicates.
- Runtime confirmation observes the corresponding local or system-media state instead of treating command delivery as success.
- Music confirmation has a dedicated four-second ceiling because the desktop gesture, Windows media-key acknowledgement and refreshed system-media snapshot can legitimately exceed the generic 1.8-second settings timeout; successful changes still confirm immediately.
- An empty playback state remains unavailable and does not present a fake successful play action.

The same acceptance rule applies to subsequent repairs: `visible state -> selected target -> host/backend execution -> observed state change -> user feedback`.

## Legacy surfaces that must not be copied

- Mock provider/device names, sample recognition records, sample queue rows, and UI-template mood modes.
- Deferred actions that have no host or backend execution boundary.
- Raw tool names, secret fields, internal paths, prompt text, or capability schema dumps.
- Live2D future placeholders presented as current capability.
- Large explanatory walls that displace the task the user opened the page to perform.

## Required replacement gates

1. Passed: every field comes from a real snapshot or an honest empty/failed state.
2. Passed: every enabled control has an existing action boundary and structured result.
3. Passed: button feedback covers pressed, pending, confirmed, failed, and execution-unknown where applicable.
4. Passed: Character Workshop remains the only full character-pack authoring authority.
5. Passed: the shell renders before backend hydration and preserves the last trustworthy state during refresh.
6. Passed: wide/narrow viewport smoke and visible browser/Tauri checks.
7. Passed: connection loss, repeated click, character confirmation contracts, slow action, and local-storage failure coverage.
8. Passed: canonical entry cutover and legacy renderer thinning are in the same work slice.

## Recommended order

1. Abilities & Permissions summary and approval mode.
2. Voice & Wake with real TTS/ASR toggles, volume/speed, wake settings, and observed short-test feedback. (complete)
3. System & Diagnostics with connection, metrics, visible failures, and recovery actions. (complete; no fabricated event timeline)
4. Tauri acceptance pass and snapshot/action parity report. (complete)
5. Production cutover plus legacy renderer deletion/thin-adapter conversion. (complete)
