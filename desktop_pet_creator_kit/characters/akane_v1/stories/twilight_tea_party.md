---
story_id: twilight_tea_party
title: 黄昏茶会
description: 与茜茜在落日余晖中品尝温热的玄米茶，以及她亲手烤制却稍微有些微焦的黄油饼干。
cover_image: scenes/家/白天客厅.png
initial_node_id: intro_1
---

## intro_1 [script]
角色: Akane
表情: normal
动作: nod
背景: scenes/家/白天客厅.png
下一幕: intro_2

你回来啦……今天外面的风稍微有点凉呢。我刚好泡了温热的玄米茶，快坐下歇歇吧。

## intro_2 [script]
角色: Akane
表情: 脸红
动作: shake
下一幕: choice_taste

其实……今天下午我试着烤了点黄油小饼干，不过火候好像没掌握好，稍微有点微焦……

## choice_taste [choice]
标题: 面对有些害羞的茜茜和盘中的饼干……
角色: Akane
表情: 卖萌
动作: idle
目标: 请选择你的回应：

那个……你真的要尝尝看吗？要是觉得不好吃可不许笑我哦！

? 你的选择:
- “闻起来很香呀，我就喜欢稍微焦一点的香脆感。” -> branch_sweet
- “先别急着尝饼干，我们一起先喝口热茶暖暖手吧。” -> branch_tea
- （自由对话）仔细端详饼干，笑着向她调侃或夸奖两句 -> ai_reaction

## branch_sweet [script]
角色: Akane
表情: 开心
动作: bounce
下一幕: ending_warmth

真的吗？！呼……太好了，你总是这么温柔包容我。那、那你多尝两块，要是觉得太甜了记得喝茶！

## branch_tea [script]
角色: Akane
表情: 被摸头
动作: nod
下一幕: ending_peace

嗯……双手捧着茶杯的时候，掌心一下子就暖和起来了。能像现在这样安静地和你坐在一起，就觉得很安心。

## ai_reaction [agent]
角色: Akane
表情: 思考中
动作: nod
目标: 茜茜有些害羞紧张，玩家正在自由与她交谈。根据玩家回复的态度生成回应并引导向结局。
下一幕: ending_warmth

你这副坏笑的表情是什么意思嘛……！到底好不好吃，你倒是快说呀！

## ending_warmth [ending]
角色: Akane
表情: 得意
动作: bounce
结局标题: 结局一：微焦甜香与心意
结局总结: 在夕阳染红的房间里，茜茜的烤饼干虽然有些微焦，但这份笨拙而真诚的心意让这个午后格外甜美。

谢谢你……今天这顿茶点，是我这段时间最开心的时候了。下次我一定会烤出最完美的饼干给你尝！

## ending_peace [ending]
角色: Akane
表情: 开心
动作: nod
结局标题: 结局二：茶香萦绕的静谧午后
结局总结: 玄米茶的微温在掌心散开，没有多余的喧嚣，只有彼此相伴的宁静与安详。

茶香很淡，但有你在身边，时间就过得好慢好舒服。要是每天的黄昏都能这样就好了。
