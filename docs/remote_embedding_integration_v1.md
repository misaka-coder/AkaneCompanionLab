# Remote Embedding Integration V1

Akane can use a symmetric OpenAI-compatible `/embeddings` endpoint through
`EMBEDDING_PROVIDER=remote`. The reusable HTTP transport, query/document
contract, batch indexing, and structured transport errors remain owned by
MemCore. Akane owns only provider selection and product configuration.

Example using SiliconFlow BGE-M3:

```env
EMBEDDING_PROVIDER=remote
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=replace-with-private-key
EMBEDDING_DIMENSION=1024
EMBEDDING_TIMEOUT_SECONDS=30
EMBEDDING_REINDEX_BATCH_SIZE=64
```

The API key is required when `remote` is selected. Missing credentials,
authentication errors, transport errors, and response-shape errors are
structured failures. Akane does not silently fall back to hashed vectors.

The startup probe sends one document batch and one query batch. Its safe report
is exposed through `snapshot_embedding_reindex_status().provider_health`.
Changing endpoint, model, or dimension changes the collection identity. SQLite
remains the truth source while each MemCore namespace rebuilds its in-memory
index in batches.
