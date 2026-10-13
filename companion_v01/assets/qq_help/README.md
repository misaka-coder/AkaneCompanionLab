# QQ 帮助图片素材

当前选用 `cecilia_stationery_background_v4.png`：奶白纸面、浅绿花叶和淡粉点缀。人物画风参考用户提供的原作插图；通过 Codex 内置 `image_gen` 生成并调整配色，去掉旧底图的黄褐色和做旧感。新版没有烘焙文字，帮助、养成、商店、背包、商品变更回执及通用图卡共用这张底图。

标题由程序绘制成莓粉渐变字，正文用深灰绿，说明和装饰用鼠尾草绿。动态文字优先使用 Windows 华文楷体，依次回退楷体、幼圆、微软雅黑等中文字体；完全缺失时返回明确的不可用状态，QQ 指令页仍可回退文字。

`qq_card_artwork.py` 保留 900px 宽、550px 页眉和 235px 页脚，中间使用源图缩至 900×1350 后的 y=780–820 纸面条带延展，人物、花叶保持比例。内容从 y=590 开始。商品确认图使用同一纸面和人物局部，保留紧凑回执布局。其他角色使用不含塞西莉亚人物的中性纸面。

`qq_commerce_cards.py` 按实时商品数据绘制列表，每页最多 5 种物品；保留用户显示名、售价、数量、介绍与效果。底图无需跟随商品修改重新生成。指令语义、昵称来源、养成数值和商店权限不因换图改变。

通用工具 `render_card` 使用 `text_cards.py` 排版：模型只写标题、正文及可选副标题、已有图片句柄。正文支持空行、小标题和引用；保持 34px 正文字号，整篇生成一张长图，中间纸面随正文延展，人物和页脚装饰保持比例，不分页、不绘制页码。正文最多 4000 字符；极端换行使图片超过 20000px 高时明确返回失败，不截断内容。PNG 保存完整文字元数据，可用 `inspect_generated_file` 回读。普通聊天通过现有能力目录按需加载工具，生成文件复用既有 QQ/桌宠交付链路，不增加模型输出字段或新的记忆管线。运行时排版没有生图、LLM 或 TTS 调用。

本地验收示例见 `tests/test_text_cards.py`；设计预览不写入真实聊天记忆或商品库。

## 历史素材

`cecilia_help_background_v2.png`（固定“指令手册”页眉）、`cecilia_status_background_v1.png`（固定“塞西莉亚”页眉）保留用于历史对照，当前渲染器不再使用。`cecilia_help_reference_v3.png` 是旧版完整参考；`cecilia_background_v1.png` 是最初的无字设计参考。旧版精确提示词保存在 `background_prompts_v4.json`。

初期字体探索同时使用 normal、shy、smile 三张角色图和无字底图；原提示词保存在 `art_direction_prompts_v2.json`、`art_direction_prompts_v3.json`。未选中的对比图保留在 Codex 的生成目录，不绑定运行时。

底图生成提示词：

```text
Use case: illustration-story. Asset type: reusable illustrated background for Cecilia's QQ command help card, portrait 1024x1536. The input image is an identity and visual style REFERENCE, not an edit target. Create an elegant anime stationery/menu background that feels like this Cecilia: long very pale ash-blonde hair with a soft sage tint, olive gray green eyes, delicate gentle face, white collared blouse and charcoal brown sleeveless dress, warm shy smile. Her small beautifully drawn bust appears ONLY in the upper right corner in the top 22 percent of the canvas, occupying at most 30 percent of the width, with subtle ornamental framing around her. The top left 65 percent is calm light ivory negative space for a title. The main middle area, from x=8% to x=92% and y=26% to y=87%, must be COMPLETELY empty, flat, near-white warm ivory for readable programmatically added command text; no character, hair, flowers, lines, shadows, boxes or decorations inside this text area. A discreet elegant thin border, small botanical sprigs, tiny white flowers, sage green ribbon accents and restrained warm gold details may decorate the OUTER EDGE only. Bottom center y=89% to 96% should also remain light and empty for a short footer. Soft pastel sage, cream and pale rose palette, crisp delicate linework, polished personal companion stationery with plenty of breathing room, appealing refined design, flat front facing illustration, no 3D mockup. Constraints: absolutely NO text, NO letters, NO numbers, NO logos, NO watermark, NO placeholder marks, NO pre-made rows or menu entries. Keep the vast main text region empty and high contrast; restrained decorations never intrude into it.
```
