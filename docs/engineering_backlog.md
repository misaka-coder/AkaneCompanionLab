# Engineering Backlog

## P0 Stability

- Stream lifecycle and graceful degradation
  - Done:
    - `stream_start / stream_end / stream_error` events
    - frontend preserves partial speech/UI on stream failure
  - Remaining:
    - enrich `stream_error` with more partial fields when useful

- Observability
  - Done:
    - JSON-style structured logs for think/think_once/tts
    - `/metrics` endpoint with basic runtime, LLM, and vector-store counters
  - Remaining:
    - add main-model first-token timing to debug/log output
    - decide whether to adopt `structlog` later

- Config consolidation
  - Done:
    - summary/retrieval drift thresholds moved into `config.py`
  - Remaining:
    - continue moving other module-level thresholds into settings

- Tests
  - Done:
    - resource manifest / store / summary queue / vn extension tests already existed
    - added text utils, vector fusion, and stream tap tests
  - Remaining:
    - add retrieval-chain tests with mocked stores/vector hits

## P1 Architecture

- Tool abstraction
  - Done:
    - `call_npc` extracted into a registered tool handler
    - prompt now injects available tools dynamically
    - `set_reminder` added as the first non-NPC tool
    - minimal `/reminders/due` polling endpoint and frontend polling wired in
    - `list_reminders` / `cancel_reminder` added for reminder management
  - Next:
    - consider a third tool only after reminder semantics stabilize
    - decide whether to expose a dedicated reminder management UI in the frontend

- Layered long-term memory
  - Next:
    - add semantic/high-level memory layer above episodic summaries
    - retrieval should optionally query semantic memory first

- Gift flow system
  - Next:
    - migrate the current audio-gift MVP into a dedicated `gift_system` module
    - implement repository / service / processor / projection split
    - add `pending` hand-side capsule, top-3 working-memory injection, and inventory tool support
    - keep all future gift work aligned with [gift_flow_constitution_v1.md](/f:/Akane/AkaneCompanionLab/docs/gift_flow_constitution_v1.md)

## P2 Experience

- Proactive turn
  - blocked until reminder-like memory facts are more explicit

- Multimodal input
  - image understanding first
  - ASR later

- Emotion state model
  - add a lightweight persistent affect baseline before attempting more elaborate animation logic

## P3 Later

- Multi-NPC scene management
- Branch graph / rollback UI
- Docker and deployment bundle
- Per-user vector collections for scale
- Remote resource manifests / hot-loaded skins
