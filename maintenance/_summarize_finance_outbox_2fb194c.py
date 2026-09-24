from __future__ import annotations

import json
import sqlite3
from pathlib import Path


path = Path(
    "/var/lib/akane-host/bots/finance/instances/finance/plugins/akane.finance/finance_state.sqlite3"
)
connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row

counts = [
    {
        "status": str(row["status"]),
        "count": int(row["count"]),
        "min_attempts": int(row["min_attempts"]),
        "max_attempts": int(row["max_attempts"]),
    }
    for row in connection.execute(
        """
        SELECT status, COUNT(1) AS count,
               MIN(attempts) AS min_attempts, MAX(attempts) AS max_attempts
        FROM delivery_outbox
        GROUP BY status
        ORDER BY status
        """
    )
]
open_rows = [
    {
        "status": str(row["status"]),
        "attempts": int(row["attempts"]),
        "reason": str(row["last_reason"] or "")[:160],
        "analysis": "unanalyzed" if not str(row["text"] or "") else "analyzed",
        "due": bool(int(row["next_attempt_at"]) <= int(row["now_ts"])),
    }
    for row in connection.execute(
        """
        SELECT status, attempts, last_reason, text, next_attempt_at,
               CAST(strftime('%s', 'now') AS INTEGER) AS now_ts
        FROM delivery_outbox
        WHERE status IN ('pending', 'failed', 'dead')
        ORDER BY delivery_id
        LIMIT 50
        """
    )
]
connection.close()
print(json.dumps({"counts": counts, "open_rows": open_rows}, ensure_ascii=False, indent=2))
