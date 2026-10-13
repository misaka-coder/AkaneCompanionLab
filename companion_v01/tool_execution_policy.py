"""One startup configuration shared by batch execution and model guidance."""

import config


def tool_parallel_limit() -> int:
    return config.TOOL_MAX_PARALLEL


def tool_parallel_prompt() -> str:
    return (
        "【工具批执行并发】\n"
        f"每批最多同时执行 {tool_parallel_limit()} 个命令或只读工具调用。"
        "原生工具可以同轮提交更多调用，超额会排队，不会因超过并发数被丢弃；这不是工具轮数上限。"
        "你根据任务依赖安排批次：独立命令和读取可同轮提交，需要前一步结果的操作分轮提交。其它动作按调用顺序执行。"
        "后台受理后可继续独立工作；需要其结果时再等待或查询，不要把受理当成完成。"
        "后台任务和子代理有各自的执行队列，本数字不代表它们的运行数量。"
        "相关完成结果在下次请求前补入；按需查询进度或续读正文，交付前处理仍影响结果的任务。"
    )
