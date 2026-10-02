"""Mock 驱动：Mac 本地开发与前端联调用，不碰任何真实资源。

返回结构与真实 agent 一一对应，前端不需要知道自己在跟谁说话。
"""

from __future__ import annotations

import time

from .base import ExecutorError

IFACE = {
    "address": "10.8.1.1/24",
    "listen_port": 10015,
    "post_up": "iptables -A FORWARD -i wg0 -j ACCEPT",
    "post_down": "iptables -D FORWARD -i wg0 -j ACCEPT",
}


def _conf(name: str, ip: str, endpoint: str, allowed: list[str]) -> str:
    return "\n".join([
        "[Interface]",
        "PrivateKey = <redacted>",
        f"Address = {ip}/24",
        "DNS = 192.168.1.1",
        "",
        "[Peer]",
        "PublicKey = MOCKPUB_SERVER_EXAMPLE",
        f"Endpoint = {endpoint}",
        f"AllowedIPs = {', '.join(allowed)}",
        "PersistentKeepalive = 25",
        "",
        f"# {name}",
    ])


class MockExecutor:
    """内存里维护一份假的中转节点，命令语义与 wgagent.py 保持一致。"""

    def __init__(self, settings=None):
        self.endpoint = "vpn.example.com:10015"
        self.lan = ["10.8.1.0/24", "192.168.1.0/24"]
        self.dns = "192.168.1.1"
        self.mtu = ""
        self.server_pub = "MOCKPUB_SERVER_EXAMPLE"
        # 纯示例数据：节点名一律中性，不引用任何真实设备
        self.clients: dict[str, dict] = {
            "laptop": {"ip": "10.8.1.12", "tunnel": "lan", "disabled": False,
                       "note": "示例：笔记本", "created": "2026-03-29",
                       "handshake": int(time.time()) - 42, "rx": 4_218_447_360, "tx": 981_206_528},
            "phone": {"ip": "10.8.1.15", "tunnel": "full", "disabled": False,
                      "note": "示例：手机（全局代理）", "created": "2026-04-02",
                      "handshake": int(time.time()) - 900, "rx": 318_444_032, "tx": 92_274_688},
            "home-gw": {"ip": "10.8.1.3", "tunnel": "lan", "disabled": False,
                        "note": "示例：家里的网关", "created": "2026-03-29",
                        "handshake": int(time.time()) - 120, "rx": 88_654_208, "tx": 12_884_992},
            "old-laptop": {"ip": "10.8.1.9", "tunnel": "lan", "disabled": True,
                           "note": "示例：已停用", "created": "2025-11-11",
                           "handshake": int(time.time()) - 86400 * 40, "rx": 1_048_576, "tx": 524_288},
        }
        self.backups = [
            {"name": "wg0.conf.20261002-081500.adopt", "size": 2412, "mtime": "2026-10-02 08:15:00"},
            {"name": "wg0.conf.20261001-193022.add-iphone15", "size": 2288, "mtime": "2026-10-01 19:30:22"},
        ]

    # ── 内部 ──
    def _row(self, name: str) -> dict:
        c = self.clients[name]
        state = "在线" if (not c["disabled"] and time.time() - c["handshake"] < 300) else \
                ("离线" if not c["disabled"] else "已停用")
        return {
            "name": name,
            "pubkey": f"MOCKPUB_{name.upper().replace('-', '_')[:16]}_EXAMPLE",
            "ip": c["ip"],
            "enabled": not c["disabled"],
            "disabled": c["disabled"],
            "allowed_ips": [f"{c['ip']}/32"],
            "client_allowed_ips": self._allowed_for(c),
            "endpoint": "198.51.100.7:43122" if state == "在线" else "",
            "state": state,
            "handshake": c["handshake"],
            "handshake_human": self._human(c["handshake"]),
            "rx": c["rx"], "tx": c["tx"],
            "tunnel": c["tunnel"],
            "note": c.get("note", ""),
            "created": c.get("created", ""),
            "in_registry": True, "in_conf": not c["disabled"],
            "in_live": not c["disabled"], "has_client_files": True,
        }

    def _allowed_for(self, c: dict) -> list[str]:
        if c["tunnel"] == "full":
            return ["0.0.0.0/0", "::/0"]
        if c["tunnel"] == "custom":
            return list(c.get("custom_allowed") or self.lan)
        return list(self.lan)

    @staticmethod
    def _human(ts: int) -> str:
        d = int(time.time()) - ts
        if d < 60:
            return f"{d}秒前"
        if d < 3600:
            return f"{d // 60}分钟前"
        if d < 86400:
            return f"{d // 3600}小时前"
        return f"{d // 86400}天前"

    def _next_ip(self) -> str:
        used = {c["ip"] for c in self.clients.values()}
        for i in range(2, 255):
            ip = f"10.8.1.{i}"
            if ip not in used:
                return ip
        raise ExecutorError("IP 池已耗尽")

    # ── 命令 ──
    def run_agent(self, args: list[str]) -> dict | list:
        if not args:
            raise ExecutorError("空命令")
        cmd, rest = args[0], args[1:]
        flags = {a for a in rest if a.startswith("--")}
        positional = [a for a in rest if not a.startswith("--")]

        if cmd == "list":
            rows = [self._row(n) for n in sorted(self.clients, key=lambda n: self.clients[n]["ip"])]
            return {"interface": IFACE, "rows": rows,
                    "registry": {"advertised_endpoint": self.endpoint,
                                 "client_lan_allowed_ips": self.lan}}
        if cmd == "status":
            rows = [self._row(n) for n in self.clients]
            return {"interface": IFACE, "rows": rows,
                    "online": sum(1 for r in rows if r["state"] == "在线")}
        if cmd == "show":
            name = positional[0]
            if name not in self.clients:
                raise ExecutorError(f"找不到客户端：{name}")
            row = self._row(name)
            row["conf_text"] = _conf(name, self.clients[name]["ip"], self.endpoint,
                                     self._allowed_for(self.clients[name]))
            return row
        if cmd == "ip-pool":
            used = [{"ip": c["ip"], "name": n} for n, c in
                    sorted(self.clients.items(), key=lambda kv: kv[1]["ip"])]
            free = [f"10.8.1.{i}" for i in range(2, 255)
                    if f"10.8.1.{i}" not in {c["ip"] for c in self.clients.values()}]
            return {"network": "10.8.1.0/24", "used": used, "used_count": len(used),
                    "free_count": len(free), "next": free[0] if free else None, "free": free[:50]}
        if cmd == "add":
            name = positional[0]
            if name in self.clients and "--force" not in flags:
                raise ExecutorError(f"客户端已存在：{name}")
            tunnel = "lan"
            if "--tunnel" in rest:
                idx = rest.index("--tunnel")
                tunnel = rest[idx + 1]
            ip = self._next_ip()
            self.clients[name] = {"ip": ip, "tunnel": tunnel, "disabled": False,
                                  "note": "", "created": "2026-10-02",
                                  "handshake": 0, "rx": 0, "tx": 0}
            out = {"name": name, "ip": ip, "pubkey": f"MOCKPUB_{name.upper()[:16]}_EXAMPLE",
                   "tunnel": tunnel, "endpoint": self.endpoint,
                   "client_allowed_ips": self._allowed_for(self.clients[name])}
            if "--include-private" in flags:
                out["conf_text"] = _conf(name, ip, self.endpoint,
                                         self._allowed_for(self.clients[name]))
            return out
        if cmd in ("disable", "enable"):
            name = positional[0]
            if name not in self.clients:
                raise ExecutorError(f"找不到客户端：{name}")
            self.clients[name]["disabled"] = cmd == "disable"
            return {"name": name, "disabled": cmd == "disable"}
        if cmd == "remove":
            name = positional[0]
            if name not in self.clients:
                raise ExecutorError(f"登记表里没有 {name}")
            if "--yes" not in flags:
                raise ExecutorError("需要 --yes 确认")
            ip = self.clients[name]["ip"]
            del self.clients[name]
            return {"name": name, "deleted": True, "ip": ip, "files_removed": []}
        if cmd == "set-endpoint":
            self.endpoint = positional[0]
            return {"advertised_endpoint": self.endpoint, "resigned": 0}
        if cmd == "set-lan":
            self.lan = [x.strip() for x in positional[0].split(",") if x.strip()]
            return {"client_lan_allowed_ips": self.lan, "resigned": 0}
        if cmd == "set-dns":
            self.dns = positional[0]
            return {"client_dns": self.dns, "resigned": 0}
        if cmd == "set-mtu":
            self.mtu = positional[0] if positional else ""
            return {"client_mtu": self.mtu, "resigned": 0}
        if cmd == "resign":
            return {"resigned": len(self.clients) if "--all" in flags else 1,
                    "endpoint": self.endpoint}
        if cmd == "backups":
            return self.backups
        if cmd == "doctor":
            return {"problems": [], "warnings": ["示例驱动：数据为模拟值，不反映真实节点"]}
        if cmd == "sync":
            return {"ok": True}
        if cmd == "adopt":
            return {"adopted": len(self.clients), "advertised_endpoint": self.endpoint,
                    "client_lan_allowed_ips": self.lan}
        raise ExecutorError(f"mock 驱动未实现命令：{cmd}")

    def describe(self) -> dict:
        return {"driver": "mock", "target": "内存模拟节点（开发用）", "iface": "wg0",
                "read_only": False}
