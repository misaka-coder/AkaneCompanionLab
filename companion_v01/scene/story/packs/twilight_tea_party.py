"""Official built-in story pack: Twilight Tea Party (黄昏茶会)."""
from ...contracts import StoryChoiceOption, StoryNode, StoryPack


def get_twilight_tea_party_pack() -> StoryPack:
    nodes = {
        "intro_1": StoryNode(
            node_id="intro_1",
            kind="script",
            speaker="Akane",
            speech="你回来啦……今天外面的风稍微有点凉呢。我刚好泡了温热的玄米茶，快坐下歇歇吧。",
            emotion_id="normal",
            motion_id="nod",
            background_id="scenes/家/白天客厅.png",
            next_node_id="intro_2",
        ),
        "intro_2": StoryNode(
            node_id="intro_2",
            kind="script",
            speaker="Akane",
            speech="其实……今天下午我试着烤了点黄油小饼干，不过火候好像没掌握好，稍微有点微焦……",
            emotion_id="脸红",
            motion_id="shake",
            next_node_id="choice_taste",
        ),
        "choice_taste": StoryNode(
            node_id="choice_taste",
            kind="choice",
            title="面对有些害羞的茜茜和盘中的饼干……",
            speech="那个……你真的要尝尝看吗？要是觉得不好吃可不许笑我哦！",
            speaker="Akane",
            emotion_id="卖萌",
            motion_id="idle",
            prompt_objective="请选择你的回应：",
            options=[
                StoryChoiceOption(
                    id="opt_sweet",
                    label="“闻起来很香呀，我就喜欢稍微焦一点的香脆感。”",
                    next_node_id="branch_sweet",
                ),
                StoryChoiceOption(
                    id="opt_tea",
                    label="“先别急着尝饼干，我们一起先喝口热茶暖暖手吧。”",
                    next_node_id="branch_tea",
                ),
                StoryChoiceOption(
                    id="opt_agent",
                    label="（自由对话）仔细端详饼干，笑着向她调侃或夸奖两句",
                    next_node_id="ai_reaction",
                ),
            ],
        ),
        "branch_sweet": StoryNode(
            node_id="branch_sweet",
            kind="script",
            speaker="Akane",
            speech="真的吗？！呼……太好了，你总是这么温柔包容我。那、那你多尝两块，要是觉得太甜了记得喝茶！",
            emotion_id="开心",
            motion_id="bounce",
            next_node_id="ending_warmth",
        ),
        "branch_tea": StoryNode(
            node_id="branch_tea",
            kind="script",
            speaker="Akane",
            speech="嗯……双手捧着茶杯的时候，掌心一下子就暖和起来了。能像现在这样安静地和你坐在一起，就觉得很安心。",
            emotion_id="被摸头",
            motion_id="nod",
            next_node_id="ending_peace",
        ),
        "ai_reaction": StoryNode(
            node_id="ai_reaction",
            kind="agent",
            speaker="Akane",
            emotion_id="思考中",
            motion_id="nod",
            speech="你这副坏笑的表情是什么意思嘛……！到底好不好吃，你倒是快说呀！",
            prompt_objective="茜茜有些害羞紧张，玩家正在自由与她交谈。根据玩家回复的态度生成回应并引导向结局。",
            next_node_id="ending_warmth",
        ),
        "ending_warmth": StoryNode(
            node_id="ending_warmth",
            kind="ending",
            speaker="Akane",
            ending_title="结局一：微焦甜香与心意",
            ending_summary="在夕阳染红的房间里，茜茜的烤饼干虽然有些微焦，但这份笨拙而真诚的心意让这个午后格外甜美。",
            speech="谢谢你……今天这顿茶点，是我这段时间最开心的时候了。下次我一定会烤出最完美的饼干给你尝！",
            emotion_id="得意",
            motion_id="bounce",
        ),
        "ending_peace": StoryNode(
            node_id="ending_peace",
            kind="ending",
            speaker="Akane",
            ending_title="结局二：茶香萦绕的静谧午后",
            ending_summary="玄米茶的微温在掌心散开，没有多余的喧嚣，只有彼此相伴的宁静与安详。",
            speech="茶香很淡，但有你在身边，时间就过得好慢好舒服。要是每天的黄昏都能这样就好了。",
            emotion_id="开心",
            motion_id="nod",
        ),
    }

    return StoryPack(
        story_id="twilight_tea_party",
        title="黄昏茶会",
        description="夕阳斜照进房间，茜茜端着点心盒有些心事，想与你度过一段安静的午后时光。",
        cover_image="scenes/家/白天客厅.png",
        initial_node_id="intro_1",
        nodes=nodes,
    )
