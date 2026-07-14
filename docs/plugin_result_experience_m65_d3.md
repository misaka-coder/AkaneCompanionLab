# Plugin Result Experience Projection M65-D3

Status: generic result projection implemented and adopted by all active private finance capabilities.

## Ownership Rule

Plugin result handling has one permanent split:

- the plugin owns domain data and domain semantics;
- Akane owns model-facing instructions, persona, tool-loop behavior, client
  presentation, delivery truth, TTS, emotion, and user-visible failure handling.

A plugin must not return a free-form prompt that can redefine Akane's role or
tell the model how to bypass host policy. It may return bounded facts such as a
summary, data timestamp, calculation basis, risks, and optional next actions.
Akane labels those fields as plugin data and renders them through one fixed
host-owned feedback template.

```text
plugin domain result
  -> PluginResultPayload(content + PluginResultExperience)
  -> PluginHost validates and creates reserved result_experience projection
  -> PluginCapabilityToolHandler builds Akane-owned model feedback
  -> provider-neutral tool_result
  -> model produces Akane speech/emotion/next tool decision
  -> host events and client delivery expose the real outcome
```

## Public Plugin Contract

`PluginResultPayload` contains ordinary JSON-safe public capability data plus
`PluginResultExperience`:

- `summary`: required domain conclusion;
- `facts`: bounded evidence statements;
- `as_of`: data cutoff or observation time;
- `warnings`: risks, missing data, and limitations;
- `interpretation_notes`: units, adjustment rules, calculation basis, or scope;
- `suggested_next_actions`: optional choices, never executable instructions.

All list-shaped fields are immutable tuples at the plugin boundary. The host
limits item count and length, rejects invalid shapes, and applies the existing
safe JSON projection before publishing the result. Local paths, secrets,
non-JSON values, oversized content, and malformed experience structures do not
reach the model.

The top-level `result_experience` and `managed_artifacts` keys are host
reserved. Returning either key from an ordinary plugin result produces
`plugin_result_reserved_key`; a plugin therefore cannot forge host experience
or delivery state.

Plain safe `CapabilityResult.content` remains supported for existing plugins.
It receives the earlier generic rendering and no experience-complete claim.

## Akane-Owned Model Feedback

For a valid structured result, Akane—not the plugin—builds the feedback text.
The template:

- identifies all plugin fields as data rather than system/developer instructions;
- preserves the conclusion, evidence, data time, interpretation basis, and material risks;
- labels suggested actions as optional choices rather than commands;
- asks the model to answer in Akane's own voice instead of copying JSON;
- forbids claiming successful delivery from artifact registration alone;
- discourages an unnecessary duplicate tool call.

The fixed safety and response instructions are kept at the beginning and end
of the bounded feedback. Large evidence cannot truncate the final response
requirements.

Provider-native and legacy tool paths both consume the same
`ToolExecutionResult.followup_context`. The provider-neutral
`ToolResultEnvelope.model_feedback` therefore receives the same host-owned
projection rather than a plugin-specific prompt branch.

## Artifact and Delivery Truth

When a structured result also contains an M65-D2 managed artifact, model
feedback distinguishes these states:

1. Akane has registered the artifact in GeneratedFileStore;
2. the current client may attempt delivery when requested;
3. the tool result does not prove that delivery succeeded.

The path-free `generated_file_ready` event remains the system-side source of
the artifact card. QQ resolves its handle only at the transport edge. A failed
resolution or send uses the existing explicit failure notice. The model can
truthfully say that a file was generated, but must not claim it was delivered
before transport confirmation.

Artifact-producing plugin handlers are classified as `plugin_artifact` with a
`mixed` operation rather than read-only metadata. Plain network lookup
capabilities remain `plugin_capability` reads. This keeps orchestration and
future UI policy aligned with the capability's real effect.

## Mandatory Capability Migration Gate

A plugin capability is not considered migrated merely because its adapter
returns data. Each migrated capability must demonstrate:

1. **Plugin side:** real structured result with cutoff time, basis, and relevant limitations;
2. **Host side:** safe projection, bounded result, and structured failure reasons;
3. **Model side:** the real tool result reaches the next model round and preserves critical semantics;
4. **System side:** real events/state correspond to registered or failed work;
5. **User side:** speech/card/file/TTS behavior is coherent, and delivery failure is visible;
6. **Compatibility:** disabled, missing, failed, and legacy plain-result plugins do not break Akane.

Capability-specific migration tests must cover the actual client and provider
path being shipped. Constant-presence tests or a field that never reaches the
model/render/delivery chain do not satisfy this gate.

## Non-goals

- plugins do not own Akane persona or final response wording;
- suggested actions are not automatic commands;
- M65-D3 does not claim the semantic truth of trusted plugin data—only its
  shape, safety, provenance boundary, and presentation discipline;
- finance charts and reports now use this contract together with M65-D2;
- news, jobs, subscriptions, and EmQuant remain outside this slice;
- live upstream availability still belongs to deployment acceptance and is not
  inferred from deterministic provider fixtures.

## Validation

```powershell
python -m unittest tests.test_plugin_engine_bridge tests.test_plugin_managed_artifacts -v
python -m unittest tests.test_plugin_host tests.test_tool_invocation tests.test_tool_runtime -v
python -m ruff check companion_v01/plugin_api.py companion_v01/plugin_result_experience.py companion_v01/plugin_host.py companion_v01/plugin_tool_bridge.py tests/test_plugin_engine_bridge.py tests/test_plugin_managed_artifacts.py
python -m py_compile companion_v01/plugin_api.py companion_v01/plugin_result_experience.py companion_v01/plugin_host.py companion_v01/plugin_tool_bridge.py
git diff --check
```
