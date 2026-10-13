"""Opt-in real local-model comparison; never reads conversations or loads two models."""

from __future__ import annotations

import argparse
import contextlib
import gc
import json
import math
import sys
import tempfile
import time
from pathlib import Path


CORPUS = (
    ("饮品", "我平时喜欢喝无糖拿铁，下午喝咖啡容易睡不着，所以只在上午喝。", "她喜欢什么咖啡，什么时候喝？"),
    ("饮食", "我对花生过敏，点外卖时需要提醒店家不要放花生和花生酱。", "点餐有什么食物需要避开？"),
    ("日程", "每周三晚上七点半有钢琴课，通常八点半结束，回来再聊天。", "钢琴课是星期几，几点开始？"),
    ("礼物", "妹妹下个月过生日，她喜欢蓝色，也一直想要一副降噪耳机。", "给妹妹准备什么生日礼物合适？"),
    ("旅行", "计划周末去杭州，坐高铁过去，第一天下午想在西湖附近散步。", "周末旅行去哪里，怎么过去？"),
    ("项目", "项目后端采用 Python 和 FastAPI，桌面客户端使用 Tauri，前端代码是 JavaScript。", "这个项目的后端和桌面端分别用什么技术？"),
    ("显存", "记忆检索使用 BGE-M3 向量模型，放在 CUDA 上运行；空闲时可以释放未使用的显存缓存。", "哪个记忆模型用了 GPU，空闲时能回收什么？"),
    ("提交", "修改代码后先运行相关测试并检查差异，每个完整的小改动单独提交，方便回滚。", "代码改完应该如何验证和提交？"),
    ("阅读", "最近在读一本关于天文学的书，尤其对行星的形成过程和木星的卫星感兴趣。", "最近读什么书，对哪些天文话题感兴趣？"),
    ("天气", "今天一直下雨，出门记得带伞，晚上可能降温，最好加一件外套。", "今天出门需要带什么？"),
    ("音乐", "工作时喜欢听轻音乐和钢琴曲，音量低一些，能帮助集中注意力。", "工作的时候喜欢播放哪种音乐？"),
    ("英文", "The backup runs every Friday at 9 pm. Keep the latest three successful snapshots.", "When does the backup run and how many snapshots should be retained?"),
)


def cosine(left, right):
    norm = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right)) / norm if norm else 0.0


def run_model(precision: str, args):
    from companion_v01.huggingface_provider import HuggingFaceEmbeddingProvider

    started = time.perf_counter()
    provider = HuggingFaceEmbeddingProvider(
        model_name=args.model, device=args.device, local_files_only=True,
        precision=precision, batch_size=args.batch_size, idle_seconds=1.0,
    )
    try:
        torch = sys.modules.get("torch")
        if args.device.startswith("cuda"):
            torch.cuda.reset_peak_memory_stats(args.device)
        documents = provider.embed_documents([item[1] for item in CORPUS])
        queries = provider.embed_queries([item[2] for item in CORPUS])
        matrix = [[cosine(query, document) for document in documents] for query in queries]
        rankings = [sorted(range(len(CORPUS)), key=lambda index: scores[index], reverse=True) for scores in matrix]
        # Varied, longer synthetic inputs exercise the real bounded batch path.
        provider.embed_documents([CORPUS[index][1] * (40 + index * 3) for index in range(8)])
        active = provider.snapshot_runtime()
        time.sleep(1.5)
        idle = provider.snapshot_runtime()
        report = {
            "precision": active["precision"], "seconds": round(time.perf_counter() - started, 3),
            "dimension": provider.dimension, "top1_correct": sum(row[0] == index for index, row in enumerate(rankings)),
            "query_count": len(CORPUS), "active": active, "idle": idle,
        }
        return documents, queries, matrix, rankings, report
    finally:
        provider.close()
        gc.collect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    results = []
    for precision in ("float32", "float16"):
        print(json.dumps({"phase": "loading_and_encoding", "precision": precision}), flush=True)
        # Dependency import output can contain environment/config details.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as sink, \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                result = run_model(precision, args)
            except Exception as exc:
                print(json.dumps({"phase": "failed", "error_type": type(exc).__name__}), file=sys.__stdout__, flush=True)
                return 1
        results.append(result)
        print(json.dumps({"phase": "measured", **result[-1]}), flush=True)
    baseline, candidate = results
    max_delta = max(abs(a - b) for left, right in zip(baseline[2], candidate[2]) for a, b in zip(left, right))
    min_alignment = min(cosine(a, b) for a, b in zip(baseline[0] + baseline[1], candidate[0] + candidate[1]))
    agreement = sum(left[0] == right[0] for left, right in zip(baseline[3], candidate[3]))
    passed = (
        baseline[-1]["precision"] == "float32" and candidate[-1]["precision"] == "float16"
        and baseline[-1]["dimension"] == candidate[-1]["dimension"]
        and agreement == len(CORPUS) and max_delta < 0.01 and min_alignment > 0.999
    )
    report = {"ok": passed, "corpus_kind": "synthetic_multilingual_memory_retrieval",
              "models": [baseline[-1], candidate[-1]], "top1_agreement": agreement,
              "query_count": len(CORPUS), "max_score_delta": max_delta, "min_vector_cosine": min_alignment}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "models"}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
