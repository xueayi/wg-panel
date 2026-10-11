"""面板自有元数据存储（SQLite）。

只存两样：管理员凭据、审计日志。peer 的真相永远在节点上的 registry.json，
这里一份都不复制——避免「面板显示的和实际生效的」两套数据。
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
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
CREATE TABLE IF NOT EXISTS traffic_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_key    TEXT NOT NULL,
    iface       TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    public_key  TEXT NOT NULL,
    handshake   INTEGER NOT NULL DEFAULT 0,
    rx          INTEGER NOT NULL DEFAULT 0,
    tx          INTEGER NOT NULL DEFAULT 0,
    rx_delta    INTEGER NOT NULL DEFAULT 0,
    tx_delta    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_traffic_lookup
    ON traffic_samples(node_key, iface, ts);
CREATE TABLE IF NOT EXISTS traffic_contexts (
    connection_key TEXT PRIMARY KEY, node_key TEXT NOT NULL,
    iface TEXT NOT NULL, ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS traffic_totals (
    node_key TEXT NOT NULL, iface TEXT NOT NULL, public_key TEXT NOT NULL,
    ts INTEGER NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL,
    total_rx INTEGER NOT NULL, total_tx INTEGER NOT NULL,
    PRIMARY KEY(node_key, iface, public_key)
);
CREATE TABLE IF NOT EXISTS traffic_rollups (
    node_key TEXT NOT NULL, iface TEXT NOT NULL, public_key TEXT NOT NULL,
    resolution INTEGER NOT NULL, ts INTEGER NOT NULL,
    rx_delta INTEGER NOT NULL, tx_delta INTEGER NOT NULL, coverage REAL NOT NULL,
    PRIMARY KEY(node_key, iface, public_key, resolution, ts)
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(traffic_samples)")}
            if "coverage" not in columns:
                conn.execute("ALTER TABLE traffic_samples ADD COLUMN coverage REAL NOT NULL DEFAULT 0")

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

    @contextmanager
    def _traffic_connection(self):
        with sqlite3.connect(self.path, timeout=30) as conn:
            yield conn

    def record_traffic(self, node_key: str, iface: str, ts: int,
                       peers: list[dict], max_gap: float, continuous: bool) -> list[dict]:
        """Save counters and lifetime totals atomically; outages contribute no invented rate."""
        result = []
        with self._traffic_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for peer in peers:
                public_key = str(peer.get("public_key") or "")
                if not public_key:
                    continue
                rx, tx = max(0, int(peer.get("rx") or 0)), max(0, int(peer.get("tx") or 0))
                old = conn.execute(
                    "SELECT ts, rx, tx, total_rx, total_tx FROM traffic_totals "
                    "WHERE node_key=? AND iface=? AND public_key=?",
                    (node_key, iface, public_key),
                ).fetchone()
                elapsed = ts - old[0] if old else 0
                rx_delta = max(0, rx - old[1]) if old else 0
                tx_delta = max(0, tx - old[2]) if old else 0
                total_rx = old[3] + rx_delta if old else rx
                total_tx = old[4] + tx_delta if old else tx
                valid = bool(old and continuous and 0 < elapsed <= max_gap)
                coverage = elapsed if valid else 0
                graph_rx, graph_tx = (rx_delta, tx_delta) if valid else (0, 0)
                conn.execute(
                    "INSERT INTO traffic_totals VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(node_key, iface, public_key) DO UPDATE SET "
                    "ts=excluded.ts, rx=excluded.rx, tx=excluded.tx, "
                    "total_rx=excluded.total_rx, total_tx=excluded.total_tx",
                    (node_key, iface, public_key, ts, rx, tx, total_rx, total_tx),
                )
                conn.execute(
                    "INSERT INTO traffic_samples "
                    "(node_key, iface, ts, public_key, handshake, rx, tx, rx_delta, tx_delta, coverage) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (node_key, iface, ts, public_key, int(peer.get("handshake") or 0),
                     rx, tx, graph_rx, graph_tx, coverage),
                )
                result.append({
                    "public_key": public_key, "handshake": int(peer.get("handshake") or 0),
                    "rx": total_rx, "tx": total_tx,
                    "rx_rate": round(graph_rx / coverage, 1) if coverage else None,
                    "tx_rate": round(graph_tx / coverage, 1) if coverage else None,
                })
        return result

    def save_traffic_context(self, connection_key: str, node_key: str, iface: str, ts: int) -> None:
        with self._traffic_connection() as conn:
            conn.execute(
                "INSERT INTO traffic_contexts VALUES (?, ?, ?, ?) "
                "ON CONFLICT(connection_key) DO UPDATE SET "
                "node_key=excluded.node_key, iface=excluded.iface, ts=excluded.ts",
                (connection_key, node_key, iface, ts),
            )

    def traffic_context(self, connection_key: str):
        with self._traffic_connection() as conn:
            return conn.execute(
                "SELECT node_key, iface, ts FROM traffic_contexts WHERE connection_key=?",
                (connection_key,),
            ).fetchone()

    def traffic_totals(self, node_key: str, iface: str) -> tuple[int, int]:
        with self._traffic_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(total_rx), 0), COALESCE(SUM(total_tx), 0) "
                "FROM traffic_totals WHERE node_key=? AND iface=?", (node_key, iface),
            ).fetchone()
        return row[0], row[1]

    def traffic_rows(self, node_key: str, iface: str, start: int, end: int) -> list[dict]:
        with self._traffic_connection() as conn:
            rows = conn.execute(
                "SELECT ts, public_key, rx_delta, tx_delta, coverage, 1 FROM traffic_samples "
                "WHERE node_key=? AND iface=? AND ts>=? AND ts<=? UNION ALL "
                "SELECT ts, public_key, rx_delta, tx_delta, coverage, resolution FROM traffic_rollups "
                "WHERE node_key=? AND iface=? AND ts>=? AND ts<=? ORDER BY ts",
                (node_key, iface, start, end, node_key, iface, start, end),
            ).fetchall()
        return [{"ts": r[0], "public_key": r[1], "rx_delta": r[2],
                 "tx_delta": r[3], "coverage": r[4], "resolution": r[5]} for r in rows]

    def compact_traffic(self, now: int) -> None:
        """Each sample lives at one resolution: 3 min fine, 26 h minute, 16 d hour."""
        with self._traffic_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for source, resolution, cutoff in (
                ("traffic_samples", 60, now - 180),
                ("traffic_rollups", 3600, now - 26 * 3600),
            ):
                where = "ts < ?" if source == "traffic_samples" else "resolution=60 AND ts < ?"
                conn.execute(
                    f"INSERT INTO traffic_rollups "
                    f"SELECT node_key, iface, public_key, {resolution}, "
                    f"CAST(ts / {resolution} AS INTEGER) * {resolution}, "
                    f"SUM(rx_delta), SUM(tx_delta), SUM(coverage) FROM {source} "
                    f"WHERE {where} GROUP BY node_key, iface, public_key, "
                    f"CAST(ts / {resolution} AS INTEGER) "
                    "ON CONFLICT(node_key, iface, public_key, resolution, ts) DO UPDATE SET "
                    "rx_delta=traffic_rollups.rx_delta+excluded.rx_delta, "
                    "tx_delta=traffic_rollups.tx_delta+excluded.tx_delta, "
                    "coverage=traffic_rollups.coverage+excluded.coverage", (cutoff,),
                )
                conn.execute(f"DELETE FROM {source} WHERE {where}", (cutoff,))
            conn.execute("DELETE FROM traffic_rollups WHERE ts < ?", (now - 16 * 86400,))
