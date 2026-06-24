**Retrieval Eval Benchmark**

`scripts/retrieval/run_retrieval_eval_benchmark.py` runs Akane's full retrieval pipeline against a JSONL evaluation set and writes both a summary JSON report and a per-case JSONL report.

Example:

```bash
python scripts/retrieval/run_retrieval_eval_benchmark.py documents/projects/retrieval_eval_candidates_20260410_212043.jsonl
```

Useful flags:

- `--review-statuses approved,golden`: only benchmark rows with selected review states
- `--limit 20`: quick smoke run on the first 20 rows
- `--force-retrieval`: ignore router gating and force every sample through the retrieval chain
- `--embedding-provider hashed`: force hashed embeddings for A/B comparison
- `--embedding-provider huggingface --embedding-model-name BAAI/bge-m3`: force the recommended BGE-M3 semantic embeddings
- `--embedding-cache-size 0`: temporarily disable embedding cache
- `--output <path>` / `--details-output <path>`: custom report paths

What it measures:

- whether the router triggered retrieval
- whether the original router would have triggered retrieval, even in `--force-retrieval` mode
- final `top1` / `top4` hit rate
- context-aware `top1` / `top4` hit rate after raw-snippet expansion windows
- first-attempt `top4` hit rate
- ever-hit `top4` rate across retries
- verifier retry rate
- per-layer (`raw / summary / semantic_summary`) recall
- elapsed time and average router / verifier ready time

Notes:

- The benchmark disables drift shortcut randomness for deterministic runs.
- It uses the current vector collection for the chosen embedding provider.
- If the collection has fewer rows than SQLite can vectorize, the script prints a warning because the benchmark may be measuring an incomplete index.
- `--force-retrieval` is useful when you want to measure pure retrieval accuracy without letting router skip most samples.
- `EMBEDDING_LOCAL_FILES_ONLY=true` is the default. Cache or download `BAAI/bge-m3` first, or set `EMBEDDING_MODEL_NAME` to a local model directory, before expecting the HuggingFace provider to replace the hashed fallback.
