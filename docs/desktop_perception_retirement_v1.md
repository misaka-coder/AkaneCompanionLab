# Desktop perception legacy retirement V1

## Authority and visible behavior

`desktop_pet_next` is the active desktop product. Screen observations use the
existing direct-frame path and user-authorized screen sharing. The perception
panel explains that screen sharing is separate from bound desktop tools.

New desktop turns do not implicitly read clipboard text or attach window titles.
Manual paste, clipboard image/file attachments, screen frames, music context,
and explicit desktop tool calls retain their existing paths.

The `desktop_context_snapshot` Satellite tool reads the current native window
only when invoked through the bound executor and existing capability permission
boundary. Turning off screen sharing stops screen frames, not that separate
tool. This change does not relax group identity, owner, binding, or approval
checks and does not introduce backend-side desktop execution.

## Removed implementation

- Frontend automatic window polling, per-turn collection, two-minute cache,
  focused-keyword wait budget, and `navigator.clipboard.readText()`.
- Hidden `desktopContextEnabled` / `clipboardContextEnabled` preferences,
  settings snapshots, action mappings and automatic-context capability flag.
- The unused direct Tauri command exposure for window context; Satellite keeps
  the single native reader.
- Native persistence of retired screen-summary mode and interval fields.

Older saved settings are migrated by dropping the retired fields. Stale settings
commands receive the existing structured `not-supported` acknowledgement and
cannot revive automatic collection. No new legacy toggles are added to the UI.

The cache bug is removed with the cache: no old window is relabelled as current
or assigned a new timestamp. On-demand results retain their observation time.
Self/absent/empty/unsupported foreground observations produce `ok: false` and a
failed Satellite result with a concrete reason, not a successful empty context.
The existing result schema and wire fingerprint are unchanged.

The native state now preserves direct-screen sampling interval, observation
window, maximum edge and packing preferences. The frontend's existing
`normalizeScreenObservationSettings` remains the authority for their defaults
and normalization; Rust only persists the values.

## Compatibility boundary

Frozen Electron clients still submit `desktop_context`; the backend formatter
in `desktop_context_engine.py` remains solely for that existing protocol. It is
not a new desktop observation owner and must not gain new consumers or features.
Remove this compatibility formatter and protocol flag with retirement of the
frozen Electron client, not independently while those entry points remain
supported. Its incoming clipboard/window data is not produced by next-gen turns.

The old screen-summary routes remain explicit HTTP 410 retirement responses;
no second vision job is reintroduced. `settings.html` remains a redirect.

## Verification

```powershell
node desktop_pet_next/scripts/desktop-perception-retirement-smoke.mjs
node desktop_pet_next/scripts/screen-observation-smoke.mjs
node desktop_pet_next/scripts/control-center-v2-smoke.mjs
python -m unittest tests.test_desktop_pet_frontend_contract tests.test_desktop_screen_vision tests.test_desktop_satellite_local_capabilities tests.test_package_reintegration_policy -q
cargo test --manifest-path desktop_pet_next/src-tauri/Cargo.toml
npm --prefix desktop_pet_next run build
git diff --check
```

The retirement smoke executes production state migration, save and request
builders with synthetic inputs; it fails if implicit clipboard/native reads
occur, verifies old commands cannot reactivate collection, and checks the
rendered boundary wording. Rust tests cover state round trips, success/failure
observations, idempotent results and the actual Satellite wire path.

Browser preview QA checks the actual perception panel and its disconnected
state without starting the pet or granting screen-sharing permission. The new
boundary note has a dedicated readable text style; existing global typography
and layout are unchanged. Real screen-sharing authorization remains a manual
check, not something these tests claim to have granted.

Known pre-existing test failure: the full M63 policy suite's
`test_dynamic_adapter_execution_uses_single_capcore_prepare_gate` rejects
`capcore_build_permission_request` in `tool_handlers/adapters.py`; both the
import and use already exist in the base revision. This slice does not change
that independent adapter permission path or weaken its guard test.
