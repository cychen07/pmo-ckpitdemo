from __future__ import annotations

import json
from pathlib import Path
from typing import Any


try:  # pragma: no cover - optional dependency
    import psycopg
except Exception:  # noqa: BLE001 - optional dependency
    psycopg = None  # type: ignore[assignment]


class JsonSnapshotStore:
    def save(self, snapshot_path: Path, snapshot: dict[str, Any]) -> None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, snapshot_path: Path) -> dict[str, Any] | None:
        if not snapshot_path.exists():
            return None
        return json.loads(snapshot_path.read_text(encoding="utf-8"))


class PostgresSnapshotStore:
    def __init__(self, dsn: str) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg is required for postgres state store")
        self.dsn = dsn

    def _ensure_schema(self, conn: Any) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS newera_snapshots (
                    snapshot_key TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()

    def save(self, snapshot: dict[str, Any], snapshot_key: str = "default") -> None:
        with psycopg.connect(self.dsn) as conn:  # type: ignore[union-attr]
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO newera_snapshots (snapshot_key, payload, saved_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (snapshot_key)
                    DO UPDATE SET payload = EXCLUDED.payload, saved_at = NOW()
                    """,
                    (snapshot_key, json.dumps(snapshot, ensure_ascii=False)),
                )
            conn.commit()

    def load(self, snapshot_key: str = "default") -> dict[str, Any] | None:
        with psycopg.connect(self.dsn) as conn:  # type: ignore[union-attr]
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM newera_snapshots WHERE snapshot_key = %s",
                    (snapshot_key,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                payload = row[0]
                return payload if isinstance(payload, dict) else json.loads(payload)

    def health(self) -> dict[str, Any]:
        try:
            with psycopg.connect(self.dsn) as conn:  # type: ignore[union-attr]
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return {"provider": "postgres", "ok": True}
        except Exception as exc:
            return {"provider": "postgres", "ok": False, "error": str(exc)}
