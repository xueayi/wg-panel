"""Read-only metrics sampling; cached rates and persistent peer traffic history."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import defaultdict

WINDOWS = (60, 3600, 43200, 86400, 604800, 1296000)


class TrafficSampler:
    def __init__(self, store, executor_factory, interval: int = 5, context_factory=None):
        self.store = store
        self.executor_factory = executor_factory
        self.context_factory = context_factory or executor_factory
        self.interval = max(0, int(interval))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._identity = None
        self._node = ""
        self._iface = "wg0"
        self._last = []
        self._available = False
        self._sampled_at = None
        self._continuous = True

    @staticmethod
    def _connection(desc):
        # Do not put credentials or mutable read-only flags into the identity.
        fields = {key: str(desc.get(key) or "") for key in
                  ("driver", "target", "iface", "host", "port", "user")}
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    def start(self) -> None:
        if self.interval <= 0 or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wgpanel-traffic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_once()
            self._stop.wait(self.interval)

    def sample_once(self) -> bool:
        if not self.interval:
            return False
        with self._lock:
            try:
                executor = self.executor_factory()
                desc = executor.describe()
                identity = self._connection(desc)
                raw = executor.run_agent(["metrics"])
                if not isinstance(raw, dict) or not isinstance(raw.get("peers"), list):
                    raise ValueError("invalid metrics")
                server_key = str(raw.get("public_key") or raw.get("server_public_key") or "")
                if not server_key:
                    raise ValueError("metrics requires server public key")
                iface = str(raw.get("interface") or desc.get("iface") or "wg0")
                node = hashlib.sha256(f"{identity}:{iface}:{server_key}".encode()).hexdigest()
                now = int(time.time())
                peers = self.store.record_traffic(
                    node, iface, now, raw["peers"], max(2, self.interval * 1.5), self._continuous,
                )
                self.store.save_traffic_context(identity, node, iface, now)
                self.store.compact_traffic(now)
            except Exception:
                self._available = False
                self._continuous = False
                return False
            self._identity, self._node, self._iface = identity, node, iface
            self._sampled_at, self._last = now, peers
            self._available = self._continuous = True
            return True

    def _context_matches(self):
        try:
            identity = self._connection(self.context_factory().describe())
            if self._identity is None:
                saved = self.store.traffic_context(identity)
                if saved:
                    self._identity = identity
                    self._node, self._iface, self._sampled_at = saved
            return self._identity == identity
        except Exception:
            return False

    def snapshot(self) -> dict:
        with self._lock:
            matches = self._context_matches()
            current = self._last if matches else []
            available = bool(matches and self._available and self.interval)
            rx, tx = self.store.traffic_totals(self._node, self._iface) if matches else (0, 0)
            up = sum(peer["rx_rate"] or 0 for peer in current) if available else None
            down = sum(peer["tx_rate"] or 0 for peer in current) if available else None
            if current and available and all(peer["rx_rate"] is None for peer in current):
                up = down = None
            peers = [dict(peer) for peer in current]
            if not available:
                for peer in peers:
                    peer["rx_rate"] = peer["tx_rate"] = None
            return {"node": self._node if matches else "", "interface": self._iface,
                    "enabled": bool(self.interval), "available": available,
                    "sampled_at": self._sampled_at if matches else None,
                    "rx": rx, "tx": tx, "up": up, "down": down,
                    "up_rate": up, "down_rate": down, "peers": peers}

    def series(self, window: int = 3600, scope: str = "all") -> dict:
        if window not in WINDOWS:
            raise ValueError(f"unknown traffic window {window}; expected one of {WINDOWS}")
        with self._lock:
            now = int(time.time())
            resolution = 1 if window == 60 else 3600 if window >= 604800 else 60
            step = max(resolution, math.ceil(window / 60 / resolution) * resolution)
            start = (now - window) // step * step
            end = now // step * step
            rows = self.store.traffic_rows(self._node, self._iface, start, now) if self._context_matches() else []
            buckets = defaultdict(lambda: [0, 0, 0])
            for row in rows:
                if scope and scope != "all" and row["public_key"] != scope:
                    continue
                if row["resolution"] == 1 and row["coverage"]:
                    # Fine samples describe the observed interval before their timestamp.
                    # Fill it, so a normal five-second sampler does not invent four gaps.
                    left, right = row["ts"] - row["coverage"], row["ts"]
                    for bucket in range(int(left) // step * step, int(right) // step * step + 1, step):
                        overlap = max(0, min(right, bucket + step) - max(left, bucket))
                        fraction = overlap / row["coverage"]
                        buckets[bucket][0] += row["rx_delta"] * fraction
                        buckets[bucket][1] += row["tx_delta"] * fraction
                        buckets[bucket][2] += overlap
                else:
                    bucket = int(row["ts"]) // step * step
                    buckets[bucket][0] += row["rx_delta"]
                    buckets[bucket][1] += row["tx_delta"]
                    buckets[bucket][2] += row["coverage"]
            points = []
            for ts in range(start, end + 1, step):
                up, down, coverage = buckets.get(ts, [0, 0, 0])
                points.append([ts, round(up / step, 6) if coverage else None,
                               round(down / step, 6) if coverage else None])
            return {"window": window, "scope": scope, "step": step, "points": points}
