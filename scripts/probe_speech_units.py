"""Opt-in live prompt probe. No Bot turn, history write, or message delivery.

Run with the repository interpreter and --model-config pointing at the existing
private model_service.json. Reports contain generated test speech and timings,
never provider credentials or URLs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--characters-dir", type=Path)
    parser.add_argument("--character-pack-id", default="cecilia")
    parser.add_argument("--mode", choices=("desktop_pet", "qq_text"), default="desktop_pet")
    parser.add_argument("--prompt", action="append")
    parser.add_argument("--attention-review", action="store_true", help="Append the production QQ attention guidance")
    args = parser.parse_args()
    if args.attention_review and args.mode != "qq_text":
        parser.error("--attention-review requires --mode qq_text")
    from types import SimpleNamespace

    import config
    from companion_v01.client_protocol import ClientMode
    from companion_v01.llm_runtime import LLMRuntime
    from companion_v01.model_service_config import ModelServiceConfigStore
    from companion_v01.prompt_profiles import PromptProfileRegistry
    from companion_v01.runtime_settings import BotSettingsView
    from companion_v01.speech_policy import character_speech_policy, speech_prompt_profile
    from companion_v01.speech_units import voice_segments

    saved = ModelServiceConfigStore(args.model_config).load()
    if saved is None:
        raise SystemExit("model_config_unavailable")
    settings = BotSettingsView.from_config(config).with_model_service(saved)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runtime = LLMRuntime(settings=settings, log_dir=args.output_dir / "logs", instance_id="speech-units-probe")
    policy = {"language": "日语", "default_style": "calm", "styles": {"calm": {"description": "平静、柔和"}}}
    if args.characters_dir:
        from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
        resources = DesktopPetCharacterResourceService(characters_dir=args.characters_dir)
        policy = character_speech_policy(SimpleNamespace(desktop_pet_character_resources=resources), args.character_pack_id)
        if not policy:
            raise SystemExit("character_speech_policy_unavailable")
    profile = speech_prompt_profile(PromptProfileRegistry().get(ClientMode(args.mode), care_enabled=False), policy)
    system = "你是塞西莉亚，和熟悉的朋友自然聊天。温柔但不夸张，不要说教。\n" + profile.system_prompt_override + "\n" + profile.fast_mode_prompt
    prompts = args.prompt or ["我回来啦，今天好累。", "用平静自然的语气说八句晚安小话，八句都要配音，语气不用换。"]
    reports = []
    for prompt in prompts:
        start = time.perf_counter()
        user_prompt = prompt
        if args.attention_review:
            from companion_v01.channels.qq.turns.attention import GROUP_ATTENTION_REVIEW_CONTEXT
            user_prompt += "\n\n" + GROUP_ATTENTION_REVIEW_CONTEXT
        iterator = runtime.stream_chat_json(system_prompt=system, user_prompt=user_prompt, fallback={"speech": ""})
        events = []
        while True:
            try:
                event = next(iterator)
                events.append({"ms": round((time.perf_counter() - start) * 1000), **event})
            except StopIteration as stop:
                result = stop.value
                break
        report = {"prompt": prompt, "model": settings.chat_model_name, "elapsed_ms": round((time.perf_counter() - start) * 1000),
                  "ok": not result.error and not result.fallback_used and isinstance(result.parsed.get("utterances"), list),
                  "payload": result.parsed, "events": events}
        reports.append(report)
        (args.output_dir / "prompt_probe.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "elapsed_ms": report["elapsed_ms"],
            "utterances": len(result.parsed.get("utterances") or []),
            "audio_sentences": len(voice_segments(result.parsed.get("utterances")))}, ensure_ascii=False), flush=True)
        if not report["ok"]:
            raise SystemExit("speech_units_probe_failed")


if __name__ == "__main__":
    main()
