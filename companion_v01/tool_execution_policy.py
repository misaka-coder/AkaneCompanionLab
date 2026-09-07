"""One startup configuration shared by batch execution and model guidance."""

import config


def tool_parallel_limit() -> int:
    return config.TOOL_MAX_PARALLEL


def tool_parallel_prompt() -> str:
    return (
        "【工具批执行并发】\n"
        f"每批最多同时执行 {tool_parallel_limit()} 个已声明为只读且彼此独立的工具调用。"
        "原生工具可以同轮提交更多调用，超额会排队，不会因超过并发数被丢弃；这不是工具轮数上限。"
        "写入、发送以及未声明只读的动作由宿主按顺序执行。"
        "依赖前一步结果的动作仍须等结果后再提交，不要猜测 handle 或状态。"
        "后台任务和子代理有各自的执行队列，本数字不代表它们的运行数量；任务受理也不代表完成。"
    )
