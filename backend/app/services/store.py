"""面板自有元数据存储（SQLite）。

只存两样：管理员凭据、审计日志。peer 的真相永远在节点上的 registry.json，
这里一份都不复制——避免「面板显示的和实际生效的」两套数据。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    action  TEXT NOT NULL,
    target  TEXT,
    result  TEXT,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA)

    # ── 管理员 ──
    def get_admin(self) -> tuple[str, str] | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT username, password_hash FROM admin WHERE id = 1").fetchone()
        return tuple(row) if row else None

    def set_admin(self, username: str, password_hash: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO admin (id, username, password_hash, created) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username = excluded.username, "
                "password_hash = excluded.password_hash",
                (username, password_hash, int(time.time())),
            )

    # ── 设置（连接参数在 Web 上改，落这里覆盖 env 默认值）──
    def get_settings(self) -> dict:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {k: v for k, v in rows}

    def put_settings(self, data: dict) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, str(v)) for k, v in data.items()],
            )

    # ── 审计 ──
    def audit(self, action: str, target: str = "", result: str = "ok", detail: str = "") -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO audit (ts, action, target, result, detail) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), action, target, result, detail[:2000]),
            )

    def list_audit(self, limit: int = 100) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT ts, action, target, result, detail FROM audit ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"ts": r[0], "action": r[1], "target": r[2], "result": r[3], "detail": r[4]}
                for r in rows]
