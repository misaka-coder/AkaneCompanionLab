**Retrieval Eval Dataset**

`scripts/retrieval/generate_retrieval_eval_set.py` can build a review-first retrieval evaluation candidate set from Akane's SQLite memory store.

Example:

```bash
python scripts/retrieval/generate_retrieval_eval_set.py --count 50
```

Useful flags:

- `--profile-user-id <id>`: only sample one user's memory
- `--skip-llm`: do not call the auxiliary model; use fallback queries only
- `--no-raw` / `--no-summary` / `--no-semantic`: restrict sampled layers
- `--raw-window 2`: include more neighboring turns for raw-message prompts
- `--target-raw / --target-summary / --target-semantic`: request explicit per-layer counts instead of balanced auto-sampling
- `--allow-repeat-scarce`: allow `summary` / `semantic_summary` to reuse the same source when quota exceeds inventory
- `--output <path>`: custom JSONL output path

Output rows include:

- `query`
- `target_source_id`
- `entry_type`
- `source_preview`
- `review_memory`
- `generation_mode`
- `review_status`

Every generated row starts with `review_status = "pending"`. The intended workflow is:

1. Generate a candidate JSONL file.
2. Quickly remove unnatural or non-unique questions.
3. Keep the reviewed file as the golden retrieval benchmark set.
