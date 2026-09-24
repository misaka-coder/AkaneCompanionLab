# Jina Embedding Integration V1

Akane uses MemCore's neutral query/document embedding contract. The host-owned
`JinaEmbeddingProvider` maps that contract to Jina's task names:

- indexed memory: `retrieval.passage`;
- retrieval query: `retrieval.query`.

MemCore does not contain Jina-specific strings. Batch reindexing remains a
single remote request per configured batch instead of one request per record.

## Configuration

```env
EMBEDDING_PROVIDER=jina
EMBEDDING_MODEL_NAME=jina-embeddings-v3
EMBEDDING_BASE_URL=https://api.jina.ai/v1
EMBEDDING_API_KEY=replace-with-private-key
EMBEDDING_DIMENSION=1024
EMBEDDING_TIMEOUT_SECONDS=30
EMBEDDING_REINDEX_BATCH_SIZE=64
```

The API key is required only when the provider is explicitly set to `jina`.
Missing credentials and provider errors are structured failures; this path
does not silently replace Jina with hashed vectors.

## Startup and rebuild

At provider construction Akane sends one document and one batched query probe.
The result appears in `snapshot_embedding_reindex_status().provider_health`.
Changing model, dimension, query task, document task, or normalization changes
the collection identity, so old and new vector spaces cannot be mixed.

MemCore SQLite remains the source of truth. Each hard namespace is warmed into
the new in-memory index in background with `reindex_all(batch_size=...)`.
Original messages, summaries, and semantic summaries are not rewritten.

## Failure behavior

- authentication or quota errors: `embedding_http_error:<status>`;
- transport failure: `embedding_transport_error`;
- invalid response: `embedding_invalid_response:*`;
- vector count/dimension mismatch: structured runtime error and affected
  records remain `pending`.

Errors never include the API key or the remote response body.
