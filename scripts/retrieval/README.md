# Retrieval Scripts

This directory holds developer utilities for Akane retrieval evaluation.

Run from the repository root:

```bash
python scripts/retrieval/generate_retrieval_eval_set.py --count 50
python scripts/retrieval/run_retrieval_eval_benchmark.py documents/projects/retrieval_eval_candidates_20260410_212043.jsonl
```

Generated benchmark datasets and reports should live under `documents/projects/`
only when they are reviewed and safe to share. Local experiments, raw corpora,
and derived private notes belong in `local_research/`, which is ignored by Git.
