"""独立低频状态采集；页面轮询仅读取脱敏缓存。"""
from copy import deepcopy
import threading
import time

PEER_FIELDS = ("name", "pubkey", "ip", "state", "enabled", "disabled", "endpoint",
               "configured_endpoint", "keepalive", "client_keepalive", "handshake",
               "handshake_human", "rx", "tx", "tunnel", "site_routes", "note",
               "client_allowed_ips", "allowed_ips", "created", "in_registry", "in_conf",
               "in_live", "has_client_files", "exportable", "export_reason")


def peer_summary(row):
    return {key: row.get(key) for key in PEER_FIELDS if key in row}


class StateCache:
    def __init__(self, factory, interval=5):
        self.factory, self.interval = factory, interval
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._thread = None
        self._data = {"peers": [], "connected": False, "sampled_at": None}

    def refresh(self, executor=None):
        executor = executor or self.factory()
        try:
            raw = executor.run_agent(["list"])
            data = {"peers": [peer_summary(row) for row in raw.get("rows", [])],
                    "driver": executor.describe(), "connected": True, "sampled_at": time.time()}
        except Exception as exc:
            data = {"peers": [], "driver": executor.describe(), "connected": False,
                    "error": str(exc), "sampled_at": time.time()}
        with self._lock:
            self._data = data
        return deepcopy(data)

    def snapshot(self):
        current = self.factory().describe()
        with self._lock:
            cached = self._data.get("driver", {})
            if any(cached.get(k) != current.get(k) for k in ("driver", "target", "iface")):
                return {"peers": [], "connected": False, "sampled_at": None, "driver": current}
            return deepcopy(self._data)

    def start(self):
        if self._thread:
            return
        def run():
            while not self._stop.is_set():
                self.refresh()
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
