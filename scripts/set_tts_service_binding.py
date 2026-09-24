"""Review/update only the TTS binding; never restart or change installation grants."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion_v01.extension_management import PluginSelectionStore
from companion_v01.plugin_service_dependencies import provider_bindings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--plugin-id")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    store = PluginSelectionStore(args.path, defaults=(), instance_id=args.instance_id)
    try:
        if args.plugin_id:
            if not args.expected_sha256:
                raise ValueError("expected_sha256_required")
            result = store.save_service_binding(service_id="tts", version=1, plugin_id=args.plugin_id,
                                                expected_sha256=args.expected_sha256)
        else:
            raw = args.path.read_bytes()
            payload = json.loads(raw)
            store._parse_payload(payload)
            bindings = provider_bindings(payload.get("service_bindings", ()))
            result = {"ok": True, "status": "review", "sha256": hashlib.sha256(raw).hexdigest(),
                      "bindings": [{"service_id": key[0], "version": key[1], "plugin_id": value}
                                   for key, value in bindings.items() if key[0] == "tts"]}
    except (OSError, ValueError) as exc:
        known = {"plugin_selection_conflict", "plugin_selection_state_schema_unsupported",
                 "plugin_selection_state_instance_mismatch", "service_provider_bindings_invalid", "expected_sha256_required"}
        print(json.dumps({"ok": False, "reason": str(exc) if str(exc) in known else "plugin_selection_state_invalid_or_unavailable"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
