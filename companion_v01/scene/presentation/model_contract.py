from dataclasses import replace

from pydantic import ValidationError

from ..contracts import ModelPresentation


SCENE_INSTRUCTIONS = """
当前客户端是 scene_presentation_v1 沉浸式房间。最终回复仍然是一个完整 JSON 对象。
用户可见正文的唯一来源是 beats，按自然语意分成适合逐句展示的片段，每段有自己的表情。
格式：{"beats":[{"speech":"嗯，回来了呀。","emotion_id":"normal","motion_id":"idle","advance":"click"},
{"speech":"给你留了一杯茶。","emotion_id":"shy","motion_id":"nod","advance":"click"}]}
不要再输出另一份 speech 或 speech_segments。tool_call、memory_metadata、state_request 等已有扩展字段按需保留。
emotion_id 从当前服装实际可用表情里选；motion_id 只能是 idle/nod/shake/bounce；advance 通常用 click。
每段应是完整短句或一个自然段，不为凑数量拆字。工具调用使用既有工具通道。
服装、位置、背景、购买与投喂是宿主确认的动作。只根据已确认事实回应，不用台词宣称自己改了未执行的状态。
历史 provider JSON 代表生成内容，不代表全部已经展示或朗读；scene.presentation/scene.delivery 事件记录实际交付。
未显示或被中断的后续片段不可当作用户已知，必要时自然补充。点击推进由客户端完成，不需要调用模型或工具。
""".strip()


def scene_prompt_profile(profile):
    from ...prompt_blocks import build_system_prompt
    blocks = tuple(b for b in profile.system_block_ids if b not in {
        "speech_streaming", "field_order", "desktop_pet_visual", "desktop_pet_activity",
    })
    return replace(profile, id="scene_presentation_v1", system_block_ids=blocks,
                   system_prompt_override=build_system_prompt(*blocks) + "\n\n" + SCENE_INSTRUCTIONS,
                   fast_mode_prompt="按 scene_presentation_v1 输出 beats。",
                   debug_mode_prompt="按 scene_presentation_v1 输出 beats；不要输出内部思考。")


def normalize_scene_model_output(value: dict) -> dict:
    result = dict(value)
    if not result.get("beats"):
        # Tool decisions may carry no final presentation. Missing final beats
        # stay visible as a protocol failure; never fabricate per-beat emotions.
        return result
    try:
        presentation = ModelPresentation.model_validate({"beats": result["beats"]})
    except ValidationError:
        result.pop("beats", None)
        result["_scene_contract_error"] = "invalid_model_beats"
        return result
    result["beats"] = [beat.model_dump() for beat in presentation.beats]
    result["speech"] = "\n".join(beat.speech for beat in presentation.beats)
    result["emotion"] = presentation.beats[0].emotion_id
    return result
