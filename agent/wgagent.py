#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wgagent.py — wg-panel 的服务端内核（在 WireGuard 中转节点上以 root 运行）

这是**整个系统里唯一实现 WireGuard 语义的地方**。面板后端不重复实现任何一条规则，
只能通过本文件的 CLI 与之交互（全部子命令支持 --json）。
由面板在首次连接时下发到中转节点（sha256 比对，经 SSH stdin 传输）。

设计原则（继承自一套已在生产环境长期验证的运维约定）
------------------------------------------------
1. **登记表 + 配置文件同源**：`registry.json` 是客户端的权威元数据（名字/公钥/IP/隧道模式），
   `wg0.conf` 由登记表**渲染**生成，永不手工追加。二者可随时用 `render --check` 比对。
2. **SaveConfig = false**：不让 wg-quick 把运行时状态写回配置文件。SaveConfig 会把
   `wg show` 的运行时快照（含临时学到的 Endpoint、丢注释、丢顺序）覆盖到 wg0.conf，
   是「配置漂移」的根源。
3. **变更走 `wg syncconf`**：原子、可增可删。不用 `wg addconf`（只会加不会删），
   不用 sed 删块（公钥含 `/` 会直接把 sed 表达式打崩）。
4. **不可逆操作先备份**：每次写配置前自动落一份带时间戳的备份，保留最近 N 份。
5. **非交互**：全部走命令行参数，不做 `read -p`；`--json` 便于自动化消费。
6. **私钥不外泄**：默认输出永不含私钥；只有 `--private` / `--include-private` 才输出明文，
   且任何对外输出都过 `mask_secrets()`。

与上游 wgserver.py 的差异（面板增强）
------------------------------------
- `disabled` 字段：停用的 peer 保留在登记表但不渲染进 `wg0.conf`（可秒级恢复，非删除）
- `set-endpoint` / `set-lan` / `set-dns` / `set-mtu`：中转节点参数与客户端模板
- `resign`：Endpoint 变更后批量重签客户端配置
- `ip-pool`：虚拟 IP 占用与空闲视图
- `WG_BIN` / `WG_QUICK_BIN` / `WG_SKIP_ROOT`：便于在无 wireguard-tools 的环境跑单测

用法
----
    python3 wgagent.py list --json
    python3 wgagent.py add iphone15 --tunnel lan --json
    python3 wgagent.py disable iphone15            # 保留条目，移出配置
    python3 wgagent.py enable iphone15
    python3 wgagent.py set-endpoint vpn.example.com:10015 --resign-all
    python3 wgagent.py ip-pool --json
    python3 wgagent.py doctor --json

退出码：0 正常；1 参数或状态问题；2 环境/权限错误；3 需要 --yes 确认
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 面板面向中文用户，时间一律按东八区显示（中国无夏令时，用固定偏移即可，
# 不依赖容器里的 tzdata，slim 镜像也稳）
TZ8 = timezone(timedelta(hours=8))


def now_local() -> datetime:
    return datetime.now(TZ8)

# ─────────────────────────── 路径与常量 ───────────────────────────

IFACE = os.environ.get("WG_IFACE", "wg0")
WG_DIR = Path(os.environ.get("WG_DIR", "/etc/wireguard"))
CONF = WG_DIR / f"{IFACE}.conf"
BASE = Path(os.environ.get("WG_BASE", "/root/wireguard"))
CLIENTS = BASE / "clients"
BACKUP_DIR = BASE / "backup"
REGISTRY = BASE / "registry.json"

# 二进制路径可注入：单测环境（无 wireguard-tools / 非 root）用 fake 脚本顶替
WG_BIN = os.environ.get("WG_BIN", "wg")
WG_QUICK_BIN = os.environ.get("WG_QUICK_BIN", "wg-quick")
SKIP_ROOT = os.environ.get("WG_SKIP_ROOT", "") == "1"

BACKUP_KEEP = 20
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
FULL_ALLOWED = ["0.0.0.0/0", "::/0"]

C_OK, C_WARN, C_ERR, C_DIM, C_OFF = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def color(s: str, c: str) -> str:
    return s if not sys.stdout.isatty() else f"{c}{s}{C_OFF}"


def eprint(*a):
    print(*a, file=sys.stderr)


def die(msg: str, code: int = 1):
    eprint(f"✖ {msg}")
    sys.exit(code)


def sh(cmd, check=True, capture=True, text=True, input_=None):
    """执行命令。cmd 为 list[str]，绝不经过 shell，避免注入与引号地狱。"""
    kw = dict(capture_output=capture, text=text)
    if input_ is not None:
        kw["input"] = input_
    p = subprocess.run(cmd, **kw)
    if check and p.returncode != 0:
        die(f"命令失败 ({p.returncode}): {' '.join(cmd)}\n{p.stderr or ''}".strip())
    return p


def need_root():
    if SKIP_ROOT:
        return
    if os.geteuid() != 0:
        die(f"需要 root 权限（当前 uid={os.geteuid()}）", 2)
    if not shutil.which(WG_BIN):
        die(f"找不到 {WG_BIN} 命令，请先安装 wireguard-tools", 2)


# ─────────────────────────── 解析 / 渲染 ───────────────────────────

def parse_conf(path: Path):
    """解析 wg 配置文件 → (interface_pairs, peers)

    peers 每项是 dict，含可选 '__comment'（紧跟其上的 `# name` 注释）。
    顺序严格保留，注释保留，便于 adopt 时恢复客户端名字。
    """
    iface: list[tuple[str, str]] = []
    peers: list[dict] = []
    cur = None
    pending = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            pending = line.lstrip("#").strip()
            continue
        if line.startswith("["):
            sec = line.strip("[]").strip().lower()
            if sec == "interface":
                cur = {"__sec": "interface"}
            else:
                cur = {"__sec": "peer"}
                if pending:
                    cur["__comment"] = pending
                peers.append(cur)
            pending = None
            continue
        if "=" in line and cur is not None:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if cur["__sec"] == "interface":
                iface.append((k, v))
            else:
                cur[k] = v
    return iface, peers


def iface_map(pairs) -> dict:
    return {k: v for k, v in pairs}


def render_conf(iface: dict, clients: dict, private_key: str) -> str:
    """由登记表渲染完整 wg0.conf。确定性强：键序固定、客户端按 IP 排序。"""
    out = ["[Interface]", f"Address = {iface['address']}", "SaveConfig = false"]
    if iface.get("mtu"):
        out.append(f"MTU = {iface['mtu']}")
    out.append(f"ListenPort = {iface['listen_port']}")
    out.append(f"PrivateKey = {private_key}")
    if iface.get("post_up"):
        out.append(f"PostUp = {iface['post_up']}")
    if iface.get("post_down"):
        out.append(f"PostDown = {iface['post_down']}")
    for name in sorted(clients, key=lambda n: ip_sort_key(clients[n]["ip"])):
        c = clients[name]
        if c.get("disabled"):
            continue  # 停用：保留登记表条目，但不渲染进配置（可秒级恢复）
        out += ["", f"# {name}", "[Peer]", f"PublicKey = {c['pubkey']}",
                f"AllowedIPs = {', '.join(c['allowed_ips'])}"]
        if c.get("endpoint"):
            out.append(f"Endpoint = {c['endpoint']}")
        if c.get("preshared_key"):
            out.append(f"PresharedKey = {c['preshared_key']}")
        if c.get("keepalive"):
            out.append(f"PersistentKeepalive = {c['keepalive']}")
    return "\n".join(out) + "\n"


def ip_sort_key(ip: str):
    m = re.search(r"\.(\d+)$", ip or "")
    return (0, int(m.group(1))) if m else (1, 0)


def parse_allowed_ips(value: str) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def iface_network(addr: str) -> str:
    """10.8.1.1/24 → 10.8.1.0/24"""
    try:
        ip, prefix = addr.split("/")
        octs = ip.split(".")
        octs[-1] = "0"
        return ".".join(octs) + "/" + prefix
    except Exception:
        return addr


# ─────────────────────────── 登记表 ───────────────────────────

def load_registry(required: bool = False) -> dict:
    if not REGISTRY.exists():
        if required:
            die(f"登记表不存在：{REGISTRY}\n先运行：python3 {Path(__file__).name} adopt --yes")
        return {}
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"登记表损坏：{e}", 2)
        return {}


def save_registry(reg: dict):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(REGISTRY)


def load_client_pubkey_map() -> dict:
    """clients/<name>.pub → pubkey，用于把现网 peer 反查成名字。"""
    m = {}
    if CLIENTS.is_dir():
        for f in CLIENTS.glob("*.pub"):
            try:
                m[f.read_text(encoding="utf-8").strip()] = f.stem
            except Exception:
                pass
    return m


def live_dump() -> tuple[str, dict]:
    """wg show <if> dump → (server_pubkey, {pubkey: {endpoint, allowed_ips, handshake, rx, tx}})"""
    p = sh([WG_BIN, "show", IFACE, "dump"], check=False)
    if p.returncode != 0:
        return "", {}
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    if not lines:
        return "", {}
    head = lines[0].split("\t")
    server_pub = head[1] if len(head) > 1 else ""
    peers = {}
    for l in lines[1:]:
        f = l.split("\t")
        if len(f) < 8:
            continue
        peers[f[0]] = {
            "endpoint": (f[2] or "").replace("(none)", ""),
            "allowed_ips": parse_allowed_ips(f[3]),
            "handshake": int(f[4]) if f[4].isdigit() else 0,
            "rx": int(f[5]) if f[5].isdigit() else 0,
            "tx": int(f[6]) if f[6].isdigit() else 0,
            "keepalive": f[7],
        }
    return server_pub, peers


def mask_secrets(text: str) -> str:
    """把配置文本里的私钥/预共享密钥遮掉。任何对外输出都必须先过这一层。"""
    return re.compile(r"^(\s*(?:PrivateKey|PresharedKey)\s*=\s*)\S+", re.I | re.M).sub(r"\1<redacted>", text)


def human_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}PiB"


def human_age(ts: int) -> str:
    if ts <= 0:
        return "从未"
    d = int(time.time()) - ts
    if d < 60:
        return f"{d}秒前"
    if d < 3600:
        return f"{d // 60}分钟前"
    if d < 86400:
        return f"{d // 3600}小时前"
    return f"{d // 86400}天前"


def view() -> dict:
    """汇总：登记表 + 现网 + clients 目录 → 每个客户端的完整视图。"""
    reg = load_registry()
    reg_clients = reg.get("clients", {})
    pub2name = load_client_pubkey_map()
    name2pub = {v: k for k, v in pub2name.items()}
    _, live = live_dump()
    iface_pairs, conf_peers = ([], [])
    if CONF.exists():
        iface_pairs, conf_peers = parse_conf(CONF)

    conf_by_pub = {}
    for idx, p in enumerate(conf_peers):
        pk = p.get("PublicKey")
        if not pk:
            continue
        conf_by_pub[pk] = {
            "comment": p.get("__comment", ""),
            "index": idx,
            "allowed_ips": parse_allowed_ips(p.get("AllowedIPs", "")),
            "endpoint": p.get("Endpoint", ""),
        }

    names = set(reg_clients) | set(pub2name.values()) | {c["comment"] for c in conf_by_pub.values() if c["comment"]}
    # 配置文件里既无注释、clients/ 也认不出的 peer，按位置兜底命名，避免整条漏掉
    for pk, meta in conf_by_pub.items():
        if pk not in pub2name and not meta["comment"]:
            names.add(f"peer-{meta['index'] + 1}")

    rows = []
    for name in sorted(names):
        rc = reg_clients.get(name, {})
        pk = rc.get("pubkey", "") or name2pub.get(name, "")
        if not pk:
            for p, meta in conf_by_pub.items():
                if meta["comment"] == name:
                    pk = p
                    break
        if not pk:
            m = re.match(r"peer-(\d+)$", name)
            if m:
                want = int(m.group(1)) - 1
                pk = next((p for p, meta in conf_by_pub.items() if meta["index"] == want), "")
        conf = conf_by_pub.get(pk, {})
        l = live.get(pk, {})
        allowed = rc.get("allowed_ips") or conf.get("allowed_ips") or []
        ip = rc.get("ip") or (allowed[0].split("/")[0] if allowed else "")
        hs = l.get("handshake", 0)
        if hs == 0 and pk not in live:
            state = "未连接"
        elif hs == 0:
            state = "未连接"
        elif time.time() - hs < 300:
            state = "在线"
        else:
            state = "离线"
        disabled = bool(rc.get("disabled"))
        rows.append({
            "name": name,
            "pubkey": pk,
            "ip": ip,
            "enabled": not disabled,
            "disabled": disabled,
            "allowed_ips": allowed,
            "site_routes": [a for a in allowed if a != f"{ip}/32"],
            "endpoint": l.get("endpoint") or rc.get("endpoint") or conf.get("endpoint", ""),
            "state": state,
            "handshake": hs,
            "handshake_human": human_age(hs),
            "rx": l.get("rx", 0),
            "tx": l.get("tx", 0),
            "tunnel": rc.get("tunnel", ""),
            "note": rc.get("note", ""),
            "in_registry": name in reg_clients,
            "in_conf": pk in conf_by_pub,
            "in_live": pk in live,
            "has_client_files": (CLIENTS / f"{name}.conf").exists(),
            "created": rc.get("created", ""),
        })
    rows.sort(key=lambda r: (ip_sort_key(r["ip"]), r["name"]))
    return {"interface": iface_map(iface_pairs), "rows": rows, "registry": reg}


# ─────────────────────────── 子命令：只读 ───────────────────────────

def cmd_list(args):
    v = view()
    rows = v["rows"]
    if args.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("（没有任何 peer）")
        return 0
    iface = v["interface"]
    sc = iface.get("SaveConfig", "?")
    warn = color("  ⚠ 应为 false", C_WARN) if sc.lower() == "true" else ""
    print(f"接口 {IFACE}  {iface.get('Address', '?')}  listen={iface.get('ListenPort', '?')}  "
          f"SaveConfig={sc}{warn}")
    mark = {"在线": color("● 在线", C_OK), "离线": "○ 离线", "未连接": "· 未连接"}
    hdr = f"{'名称':<18}{'IP':<14}{'状态':<10}{'最近握手':<12}{'接收':>10}{'发送':>10}  备注"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flags = []
        if not r["in_registry"]:
            flags.append("未纳管")
        if not r["has_client_files"]:
            flags.append("无配置文件")
        if r["in_live"] and not r["in_conf"]:
            flags.append("仅运行时")
        print(f"{r['name']:<18}{r['ip']:<14}{mark.get(r['state'], r['state']):<19}"
              f"{r['handshake_human']:<12}{human_bytes(r['rx']):>10}{human_bytes(r['tx']):>10}  {' '.join(flags)}")
    print(f"\n共 {len(rows)} 个 peer")
    return 0


def cmd_status(args):
    v = view()
    if args.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0
    iface = v["interface"]
    up = sh([WG_BIN, "show", IFACE], check=False).returncode == 0
    print(f"接口 {IFACE}: {'up' if up else 'down'}")
    print(f"地址 {iface.get('Address', '?')}   监听 {iface.get('ListenPort', '?')}")
    print(f"SaveConfig {iface.get('SaveConfig', '?')}")
    print(f"peers {len(v['rows'])}  "
          f"在线 {sum(1 for r in v['rows'] if r['state'] == '在线')}  "
          f"离线 {sum(1 for r in v['rows'] if r['state'] == '离线')}")
    return 0


def cmd_render(args):
    """把登记表渲染成配置文件文本；--check 只比对不写。"""
    reg = load_registry(required=True)
    _, conf_peers = parse_conf(CONF)
    old_priv = next((v for k, v in parse_conf(CONF)[0] if k == "PrivateKey"), None)
    if not old_priv:
        die("现有配置里找不到 PrivateKey", 2)
    text = render_conf(reg["interface"], reg.get("clients", {}), old_priv)
    if args.check:
        cur = mask_secrets(CONF.read_text(encoding="utf-8")).strip()
        new = mask_secrets(text).strip()
        same = cur == new
        print("✅ 一致" if same else "⚠ 不一致")
        if not same:
            import difflib
            for l in difflib.unified_diff(cur.splitlines(), new.splitlines(),
                                          "current", "rendered", lineterm="", n=2):
                print(l)
        return 0 if same else 1
    print(mask_secrets(text))
    return 0


def _issue(code: str, message: str, *, fix: str = "", hint: str = "",
           target: str = "") -> dict:
    """体检条目：面板按 code/fix 决定给什么按钮，hint 是展开后的处理指引。"""
    return {"code": code, "message": message, "fix": fix, "hint": hint, "target": target}


def cmd_doctor(args):
    v = view()
    problems: list[dict] = []
    warns: list[dict] = []
    iface = v["interface"]

    if sh([WG_BIN, "show", IFACE], check=False).returncode != 0:
        problems.append(_issue(
            "iface-down", f"接口 {IFACE} 未启动",
            hint=f"在中转节点上执行 wg-quick up {IFACE}；若是新节点，先确认已装 wireguard-tools"))
    if iface.get("SaveConfig", "false").lower() == "true":
        problems.append(_issue(
            "saveconfig-true", "SaveConfig = true：wg-quick 会把运行时快照写回配置，导致注释/顺序丢失、配置漂移",
            fix="adopt", hint="点「纳管既有配置」会改成 false 并按登记表重渲染（写前自动备份）"))
    if not CONF.exists():
        problems.append(_issue(
            "conf-missing", f"配置文件缺失：{CONF}",
            hint=f"确认接口名是否正确；若是全新节点，先手动创建 {CONF} 并写入 [Interface] 段"))
    else:
        m = oct(CONF.stat().st_mode)[-3:]
        if m != "600":
            warns.append(_issue(
                "conf-perms", f"{CONF.name} 权限 {m}，建议 600",
                fix="fix-perms", hint=f"chmod 600 {CONF}"))
    if CLIENTS.is_dir():
        for f in sorted(CLIENTS.iterdir()):
            if f.suffix == ".key" and oct(f.stat().st_mode)[-3:] != "600":
                warns.append(_issue(
                    "key-perms", f"{f.name} 权限 {oct(f.stat().st_mode)[-3:]}（私钥应为 600）",
                    fix="fix-perms", target=f.name,
                    hint="私钥可被同机其他用户读取。点「修复权限」一次性收紧到 600"))
    ipf = sh(["sysctl", "-n", "net.ipv4.ip_forward"], check=False).stdout.strip()
    if ipf != "1":
        problems.append(_issue(
            "ip-forward-off", "net.ipv4.ip_forward != 1：客户端无法经此访问外网",
            hint="在节点执行：echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf && sysctl -p"))
    for r in v["rows"]:
        if not r["in_registry"]:
            warns.append(_issue(
                "not-adopted", f"{r['name']}：配置文件里有 peer 但登记表没有",
                fix="adopt", target=r["name"], hint="点「纳管既有配置」把它收进登记表"))
        if r["in_live"] and not r["in_conf"]:
            warns.append(_issue(
                "runtime-only", f"{r['name']}：只存在于运行时，重启即丢",
                fix="sync", target=r["name"], hint="点「同步配置」按登记表重写并同步到内核"))
        if r["in_conf"] and not r["in_live"]:
            problems.append(_issue(
                "conf-not-live", f"{r['name']}：配置文件里有但运行时没有",
                fix="sync", target=r["name"], hint="点「同步配置」把配置同步进内核"))
        if r["handshake"] and time.time() - r["handshake"] > 90 * 86400:
            warns.append(_issue(
                "stale-peer", f"{r['name']}：{human_age(r['handshake'])}没握手，疑似僵尸节点",
                target=r["name"], hint="确认这设备还在用吗？不用了就在「客户端」页删除（先自动备份），暂时不用就选「停用」"))
        if not r["has_client_files"] and r["name"] not in ("unnamed",):
            if not r["in_registry"]:
                continue
            warns.append(_issue(
                "no-client-files", f"{r['name']}：clients/ 下无 .conf，无法重新下发",
                fix="resign", target=r["name"], hint="点「重签配置」用已存私钥重新生成（缺私钥则需重新添加该客户端）"))
    ips: dict[str, list[str]] = {}
    for r in v["rows"]:
        if r["ip"]:
            ips.setdefault(r["ip"], []).append(r["name"])
    for ip, who in ips.items():
        if len(who) > 1:
            problems.append(_issue(
                "ip-conflict", f"IP 冲突 {ip}：{', '.join(who)}",
                hint="在「客户端」页编辑其中一个，改掉虚拟 IP（面板会拒绝再次分配同一地址）"))

    if args.json:
        print(json.dumps({"problems": problems, "warnings": warns}, ensure_ascii=False, indent=2))
    else:
        if problems:
            print(color(f"发现 {len(problems)} 个问题：", C_ERR))
            for x in problems:
                print(f"  ✖ {x['message']}")
        if warns:
            print(color(f"{len(warns)} 条建议：", C_WARN))
            for x in warns:
                print(f"  ! {x['message']}")
        if not problems and not warns:
            print(color("✅ 一切正常", C_OK))
    return 1 if problems else 0


def cmd_fix_perms(args):
    """把客户端目录下的私钥与配置权限一次性收紧到 600（幂等，可反复跑）。"""
    fixed, skipped = [], 0
    targets = []
    if CLIENTS.is_dir():
        targets += [f for f in sorted(CLIENTS.iterdir()) if f.suffix in (".key", ".conf", ".pub")]
    if CONF.exists():
        targets.append(CONF)
    for f in targets:
        mode = oct(f.stat().st_mode)[-3:]
        if mode != "600":
            os.chmod(f, 0o600)
            fixed.append(f"{f.name}（{mode} → 600）")
        else:
            skipped += 1
    payload = {"fixed": fixed, "fixed_count": len(fixed), "already_ok": skipped}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 已收紧 {len(fixed)} 个文件权限" + ("" if not fixed else "：" + "，".join(fixed)))
    return 0


# ─────────────────────────── 备份 ───────────────────────────

def make_backup(tag: str = "manual") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    ts = now_local().strftime("%Y%m%d-%H%M%S")
    dst = BACKUP_DIR / f"{IFACE}.conf.{ts}.{tag}"
    # 用 copy 而不是 copy2：备份文件的 mtime 应该是「备份发生的时刻」，
    # 而不是被备份的那份配置的旧时间戳（否则列表里全是几个月前的日期）
    shutil.copy(CONF, dst)
    os.chmod(dst, 0o600)
    return dst


def prune_backups(keep: int = BACKUP_KEEP):
    files = sorted(BACKUP_DIR.glob(f"{IFACE}.conf.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        f.unlink(missing_ok=True)


def cmd_backup(args):
    if not CONF.exists():
        die(f"没有可备份的配置：{CONF}", 2)
    p = make_backup("manual")
    prune_backups(args.keep)
    print(f"已备份 → {p}")
    return 0


def cmd_backups(args):
    files = sorted(BACKUP_DIR.glob(f"{IFACE}.conf.*"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if BACKUP_DIR.is_dir() else []
    if args.json:
        print(json.dumps([{"name": f.name,
                           "size": f.stat().st_size,
                           "mtime": datetime.fromtimestamp(f.stat().st_mtime, TZ8)
                                    .strftime("%Y-%m-%d %H:%M:%S"),
                           "ts": int(f.stat().st_mtime)}
                          for f in files], ensure_ascii=False, indent=2))
        return 0
    if not files:
        print("（无备份）")
        return 0
    for f in files:
        print(f"{f.stat().st_size:>7}  {datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M:%S}  {f.name}")
    print(f"\n共 {len(files)} 份")
    return 0


def cmd_restore(args):
    src = Path(args.file)
    if not src.is_absolute():
        src = BACKUP_DIR / args.file
    if not src.exists():
        die(f"找不到备份：{src}", 2)
    if not args.yes:
        die(f"即将用 {src.name} 覆盖 {CONF}。确认请加 --yes", 3)
    make_backup("pre-restore")
    shutil.copy(src, CONF)
    os.chmod(CONF, 0o600)
    apply_syncconf()
    if args.json:
        print(json.dumps({"restored": src.name, "pre_restore_backup": True},
                         ensure_ascii=False))
        return 0
    print(f"已恢复 ← {src}")
    return 0


# ─────────────────────────── 同步到内核 ───────────────────────────

def apply_syncconf():
    """把文件配置原子同步到运行中的接口（可增可删，不断线、不清隧道）。"""
    fd, tmp = tempfile.mkstemp(prefix="wgsync-", suffix=".conf")
    os.close(fd)
    os.chmod(tmp, 0o600)
    try:
        p = sh([WG_QUICK_BIN, "strip", str(CONF)], check=False)
        if p.returncode != 0:
            die(f"wg-quick strip 失败：{p.stderr}", 2)
        Path(tmp).write_text(p.stdout, encoding="utf-8")
        p = sh([WG_BIN, "syncconf", IFACE, tmp], check=False)
        if p.returncode != 0:
            die(f"wg syncconf 失败：{p.stderr}", 2)
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_conf(text: str, tag: str = "auto"):
    make_backup(tag)
    prune_backups()
    CONF.write_text(text, encoding="utf-8")
    os.chmod(CONF, 0o600)
    apply_syncconf()


def _priv_key() -> str:
    pairs, _ = parse_conf(CONF)
    v = next((v for k, v in pairs if k == "PrivateKey"), None)
    if not v:
        die("现有配置里找不到 PrivateKey", 2)
    return v


def do_sync():
    reg = load_registry(required=True)
    write_conf(render_conf(reg["interface"], reg.get("clients", {}), _priv_key()), "sync")


def cmd_sync(args):
    do_sync()
    if args.json:
        print(json.dumps({"synced": True, "iface": IFACE, "conf": str(CONF)},
                         ensure_ascii=False))
        return 0
    print("✅ 已按登记表重渲染并同步到内核")
    return 0


# ─────────────────────────── 新增 / 删除 ───────────────────────────

def used_ips(reg: dict) -> set[str]:
    used = set()
    for c in reg.get("clients", {}).values():
        if c.get("ip"):
            used.add(c["ip"])
    return used


def next_free_ip(reg: dict, network: str) -> str:
    base = network.rsplit(".", 1)[0]
    used = used_ips(reg)
    for i in range(2, 255):
        ip = f"{base}.{i}"
        if ip not in used:
            return ip
    die("IP 池已耗尽", 2)
    return ""


def gen_keypair() -> tuple[str, str]:
    priv = sh([WG_BIN, "genkey"]).stdout.strip()
    pub = sh([WG_BIN, "pubkey"], input_=priv + "\n").stdout.strip()
    return priv, pub


def site_routes_of(c: dict) -> list[str]:
    """peer 背后的局域网网段 = 服务端侧 AllowedIPs 里除「虚拟 IP/32」之外的部分。

    这是站点互联（site-to-site）的关键：中转节点靠它知道「去 192.168.1.x 的包要交给谁」。
    """
    own = f"{c.get('ip', '')}/32"
    return [a for a in c.get("allowed_ips", []) if a and a != own]


def peer_allowed_ips(ip: str, site_routes: list[str]) -> list[str]:
    return [f"{ip}/32"] + list(site_routes)


def client_allowed_for(reg: dict, c: dict) -> list[str]:
    """客户端侧 AllowedIPs（与服务端 peer 的 ip/32 是两套东西，别混）。"""
    t = (c.get("tunnel") or "lan").lower()
    if t == "full":
        return list(reg.get("client_full_allowed_ips") or FULL_ALLOWED)
    if t == "custom":
        return list(c.get("client_allowed_ips") or c.get("allowed_ips") or [])
    return list(reg.get("client_lan_allowed_ips") or [iface_network(reg["interface"]["address"])])


def server_public_key() -> str:
    pub, _ = live_dump()
    if pub:
        return pub
    p = sh([WG_BIN, "show", IFACE, "public-key"], check=False)
    return p.stdout.strip()


def client_conf_text(name, priv, ip, server_pub, endpoint, allowed, dns, mtu, prefix="24") -> str:
    lines = ["[Interface]", f"PrivateKey = {priv}", f"Address = {ip}/{prefix}"]
    if mtu:
        lines.append(f"MTU = {mtu}")
    if dns:
        lines.append(f"DNS = {dns}")
    lines += ["", "[Peer]", f"PublicKey = {server_pub}", f"Endpoint = {endpoint}",
              f"AllowedIPs = {', '.join(allowed)}", "PersistentKeepalive = 25"]
    return "\n".join(lines) + "\n"


def cmd_add(args):
    reg = load_registry(required=True)
    name = args.name
    if not NAME_RE.match(name):
        die("客户端名只能是字母/数字/._-，1~32 字符", 1)
    clients = reg.setdefault("clients", {})
    if name in clients and not args.force:
        die(f"客户端已存在：{name}（要覆盖请加 --force）", 1)

    iface = reg["interface"]
    server_pub, _ = live_dump()
    if not server_pub:
        server_pub = sh([WG_BIN, "show", IFACE, "public-key"]).stdout.strip()

    ip = args.ip or next_free_ip(reg, iface_network(iface["address"]))
    owner = next((n for n, c in clients.items() if c.get("ip") == ip and n != name), None)
    if owner and not args.force:
        die(f"IP {ip} 已被 {owner} 占用。换一个地址，或用 --ip 指定空闲地址（强行覆盖加 --force）", 1)
    tunnel = args.tunnel
    if tunnel == "full":
        allowed = list(FULL_ALLOWED)
    elif tunnel == "lan":
        allowed = list(reg.get("client_lan_allowed_ips") or [iface_network(iface["address"])])
    else:  # custom
        if not args.allowed_ips:
            die("--tunnel custom 需要配合 --allowed-ips", 1)
        allowed = parse_allowed_ips(args.allowed_ips)

    endpoint = args.endpoint or reg.get("advertised_endpoint", "")
    if not endpoint:
        die("登记表里没有 advertised_endpoint，请用 --endpoint 指定（形如 vpn.example.com:51820）", 1)

    priv, pub = gen_keypair()

    CLIENTS.mkdir(parents=True, exist_ok=True)
    os.chmod(CLIENTS, 0o700)
    (CLIENTS / f"{name}.key").write_text(priv + "\n", encoding="utf-8")
    (CLIENTS / f"{name}.pub").write_text(pub + "\n", encoding="utf-8")
    os.chmod(CLIENTS / f"{name}.key", 0o600)
    os.chmod(CLIENTS / f"{name}.pub", 0o600)
    prefix = iface.get("address", "10.8.1.1/24").split("/")[1] if "/" in iface.get("address", "") else "24"
    conf_text = client_conf_text(name, priv, ip, server_pub, endpoint, allowed,
                                 args.dns or reg.get("client_dns"), args.mtu or reg.get("client_mtu"),
                                 prefix)
    (CLIENTS / f"{name}.conf").write_text(conf_text, encoding="utf-8")
    os.chmod(CLIENTS / f"{name}.conf", 0o600)

    site_routes = parse_allowed_ips(args.site_routes) if args.site_routes else []
    clients[name] = {
        "pubkey": pub,
        "ip": ip,
        "allowed_ips": peer_allowed_ips(ip, site_routes),
        "client_allowed_ips": list(allowed),
        "endpoint": "",
        "tunnel": tunnel,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "note": args.note or "",
    }
    save_registry(reg)
    write_conf(render_conf(iface, clients, _priv_key()), f"add-{name}")

    if args.json:
        payload = {"name": name, "ip": ip, "pubkey": pub, "tunnel": tunnel,
                   "conf": str(CLIENTS / f'{name}.conf'), "endpoint": endpoint,
                   "client_allowed_ips": list(allowed)}
        if args.include_private:
            payload["conf_text"] = conf_text
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 已新增 {name}")
        print(f"   IP        {ip}")
        print(f"   隧道      {tunnel}  → AllowedIPs = {', '.join(allowed)}")
        print(f"   Endpoint  {endpoint}")
        print(f"   配置      {CLIENTS / f'{name}.conf'}   （权限 600）")
    if args.show_conf:
        print()
        print(conf_text)
    if args.qr:
        show_qr(CLIENTS / f"{name}.conf")
    return 0


def show_qr(path: Path):
    if not shutil.which("qrencode"):
        eprint("未安装 qrencode，跳过二维码")
        return
    p = sh(["qrencode", "-t", "ansiutf8", "-o", "-", str(path)], check=False)
    sys.stdout.write(p.stdout)


def cmd_qr(args):
    p = CLIENTS / f"{args.name}.conf"
    if not p.exists():
        die(f"找不到客户端配置：{p}", 1)
    show_qr(p)
    return 0


def cmd_show(args):
    reg = load_registry()
    v = view()
    r = next((x for x in v["rows"] if x["name"] == args.name), None)
    if not r:
        die(f"找不到客户端：{args.name}", 1)
    p = CLIENTS / f"{args.name}.conf"
    if args.json:
        # json 模式下 stdout 里只能有 JSON：可读文本会让下游解析全盘失败
        payload = dict(r)
        if p.exists():
            body = p.read_text(encoding="utf-8")
            payload["conf_text"] = body if args.private else mask_secrets(body)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"名称      {r['name']}")
    print(f"状态      {r['state']}（{r['handshake_human']}）")
    print(f"IP        {r['ip']}")
    print(f"公钥      {r['pubkey'][:16]}…")
    print(f"AllowedIPs {', '.join(r['allowed_ips'])}")
    print(f"Endpoint  {r['endpoint'] or '—'}")
    print(f"隧道模式  {r['tunnel'] or '—'}   创建 {r['created'] or '—'}")
    print(f"客户端配置 {p if p.exists() else '（缺失）'}")
    if args.private:
        if not p.exists():
            die("客户端配置缺失，无法显示私钥", 1)
        print("\n" + p.read_text(encoding="utf-8"))
    return 0


def cmd_remove(args):
    name = args.name
    reg = load_registry()
    clients = reg.get("clients", {})
    if name not in clients:
        die(f"登记表里没有 {name}（先用 list 看看实际名字）", 1)
    if not args.yes:
        die(f"即将删除 {name}：运行时 peer + wg0.conf 条目 + clients/{name}.*。确认请加 --yes", 3)

    entry = clients[name]
    if args.keep_runtime:
        pass
    del clients[name]
    save_registry(reg)
    write_conf(render_conf(reg["interface"], clients, _priv_key()), f"remove-{name}")

    removed = []
    for ext in (".key", ".pub", ".conf"):
        f = CLIENTS / f"{name}{ext}"
        if f.exists():
            f.unlink()
            removed.append(f.name)
    if args.json:
        print(json.dumps({"name": name, "deleted": True, "ip": entry.get("ip", ""),
                          "files_removed": removed}, ensure_ascii=False))
        return 0
    print(f"✅ 已删除 {name}")
    if removed:
        print(f"   清理文件：{', '.join(removed)}")
    print(f"   （备份见 {BACKUP_DIR}）")
    return 0


# ─────────────── 中转节点参数 / 虚拟 IP 池 / 重签 / 启停 ───────────────

def resign_client(reg: dict, name: str, endpoint: str | None = None) -> str | None:
    """用登记表 + 已存私钥重新生成客户端 .conf。Endpoint 或网段变更后靠它重签。"""
    c = reg.get("clients", {}).get(name)
    if not c:
        return None
    keyp = CLIENTS / f"{name}.key"
    if not keyp.exists():
        return None
    iface = reg.get("interface", {})
    prefix = iface.get("address", "").split("/")[1] if "/" in iface.get("address", "") else "24"
    ep = endpoint or reg.get("advertised_endpoint", "")
    if not ep:
        return None
    text = client_conf_text(name, keyp.read_text(encoding="utf-8").strip(), c["ip"],
                            server_public_key(), ep, client_allowed_for(reg, c),
                            reg.get("client_dns"), reg.get("client_mtu"), prefix)
    p = CLIENTS / f"{name}.conf"
    p.write_text(text, encoding="utf-8")
    os.chmod(p, 0o600)
    return text


def resign_all(reg: dict, endpoint: str | None = None) -> int:
    return sum(1 for n in list(reg.get("clients", {})) if resign_client(reg, n, endpoint))


def cmd_resign(args):
    reg = load_registry(required=True)
    if args.all:
        n = resign_all(reg, args.endpoint)
        payload = {"resigned": n, "endpoint": args.endpoint or reg.get("advertised_endpoint", "")}
    else:
        text = resign_client(reg, args.name, args.endpoint)
        if not text:
            die(f"重签失败：{args.name}（登记表无此客户端，或缺 clients/{args.name}.key）", 1)
        n = 1
        payload = {"resigned": 1, "name": args.name,
                   "conf_text": text if args.include_private else mask_secrets(text)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 已重签 {n} 份客户端配置（Endpoint = "
              f"{args.endpoint or reg.get('advertised_endpoint', '—')}）")
    return 0


def cmd_set_endpoint(args):
    reg = load_registry(required=True)
    reg["advertised_endpoint"] = args.endpoint
    save_registry(reg)
    n = resign_all(reg, args.endpoint) if args.resign_all else 0
    if args.json:
        print(json.dumps({"advertised_endpoint": args.endpoint, "resigned": n}, ensure_ascii=False))
    else:
        extra = f"；已重签 {n} 份客户端配置" if n else "（旧配置仍指向旧地址，可用 resign --all 重签）"
        print(f"✅ 对外 Endpoint → {args.endpoint}{extra}")
    return 0


def cmd_set_lan(args):
    reg = load_registry(required=True)
    nets = parse_allowed_ips(args.nets)
    if not nets:
        die("网段列表为空，形如：10.8.1.0/24, 192.168.1.0/24", 1)
    reg["client_lan_allowed_ips"] = nets
    save_registry(reg)
    n = resign_all(reg) if args.resign_all else 0
    if args.json:
        print(json.dumps({"client_lan_allowed_ips": nets, "resigned": n}, ensure_ascii=False))
    else:
        print(f"✅ 局域网模式 AllowedIPs → {', '.join(nets)}" + (f"；已重签 {n} 份" if n else ""))
    return 0


def cmd_set_dns(args):
    reg = load_registry(required=True)
    reg["client_dns"] = args.dns
    save_registry(reg)
    n = resign_all(reg) if args.resign_all else 0
    if args.json:
        print(json.dumps({"client_dns": args.dns, "resigned": n}, ensure_ascii=False))
    else:
        print(f"✅ 客户端 DNS → {args.dns or '（不下发）'}" + (f"；已重签 {n} 份" if n else ""))
    return 0


def cmd_set_mtu(args):
    reg = load_registry(required=True)
    reg["client_mtu"] = int(args.mtu) if args.mtu else ""
    save_registry(reg)
    n = resign_all(reg) if args.resign_all else 0
    if args.json:
        print(json.dumps({"client_mtu": reg["client_mtu"], "resigned": n}, ensure_ascii=False))
    else:
        print(f"✅ 客户端 MTU → {reg['client_mtu'] or '（不下发）'}" + (f"；已重签 {n} 份" if n else ""))
    return 0


def cmd_update(args):
    """改已有客户端：隧道模式 / 客户端 AllowedIPs / 虚拟 IP / 备注。"""
    reg = load_registry(required=True)
    clients = reg.get("clients", {})
    if args.name not in clients:
        die(f"登记表里没有 {args.name}", 1)
    c = clients[args.name]

    if args.tunnel:
        c["tunnel"] = args.tunnel
    if args.allowed_ips:
        c["client_allowed_ips"] = parse_allowed_ips(args.allowed_ips)
        c["tunnel"] = c.get("tunnel") or "custom"
    if args.ip:
        owner = next((n for n, x in clients.items() if x.get("ip") == args.ip and n != args.name), None)
        if owner and not args.force:
            die(f"IP {args.ip} 已被 {owner} 占用（强行改加 --force）", 1)
        c["ip"] = args.ip
        c["allowed_ips"] = peer_allowed_ips(args.ip, site_routes_of(c))
    if args.site_routes is not None:
        c["allowed_ips"] = peer_allowed_ips(c["ip"], parse_allowed_ips(args.site_routes))
    if args.note is not None:
        c["note"] = args.note

    resigned = resign_client(reg, args.name) is not None
    save_registry(reg)
    write_conf(render_conf(reg["interface"], clients, _priv_key()), f"update-{args.name}")

    payload = {"name": args.name, "ip": c["ip"], "tunnel": c.get("tunnel", ""),
               "client_allowed_ips": client_allowed_for(reg, c),
               "site_routes": site_routes_of(c),
               "note": c.get("note", ""), "resigned": resigned}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 已更新 {args.name}（重签配置：{'是' if resigned else '否（缺私钥）'}）")
    return 0


def cmd_ip_pool(args):
    """虚拟 IP 占用视图：已分配的、空闲的、下一个可分配的。"""
    reg = load_registry(required=True)
    network = iface_network(reg.get("interface", {}).get("address", "10.8.1.1/24"))
    base = network.rsplit(".", 1)[0]
    used: dict[str, str] = {}
    for name, c in reg.get("clients", {}).items():
        if c.get("ip"):
            used[c["ip"]] = name
    free = [f"{base}.{i}" for i in range(2, 255) if f"{base}.{i}" not in used]
    payload = {
        "network": network,
        "used": [{"ip": ip, "name": n} for ip, n in
                 sorted(used.items(), key=lambda kv: ip_sort_key(kv[0]))],
        "used_count": len(used),
        "free_count": len(free),
        "next": free[0] if free else None,
        "free": free[: args.limit],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"网段 {network}   已分配 {len(used)}   空闲 {len(free)}   下一个 {payload['next'] or '（耗尽）'}")
        for row in payload["used"]:
            print(f"  {row['ip']:<14}{row['name']}")
    return 0


def _set_disabled(name: str, disabled: bool, tag: str) -> dict:
    reg = load_registry(required=True)
    clients = reg.get("clients", {})
    if name not in clients:
        die(f"登记表里没有 {name}", 1)
    clients[name]["disabled"] = disabled
    save_registry(reg)
    write_conf(render_conf(reg["interface"], clients, _priv_key()), f"{tag}-{name}")
    return {"name": name, "disabled": disabled,
            "note": "停用后不在 wg0.conf 中渲染，条目与私钥保留，可随时恢复" if disabled
                    else "已恢复，重新渲染进 wg0.conf"}


def cmd_disable(args):
    r = _set_disabled(args.name, True, "disable")
    print(json.dumps(r, ensure_ascii=False)) if args.json else print(f"✅ 已停用 {args.name}")
    return 0


def cmd_enable(args):
    r = _set_disabled(args.name, False, "enable")
    print(json.dumps(r, ensure_ascii=False)) if args.json else print(f"✅ 已启用 {args.name}")
    return 0


# ─────────────────────────── adopt：把现网配置纳管 ───────────────────────────

def detect_advertised_endpoint(reg_clients: dict) -> str:
    """从已有客户端配置里推断对外 Endpoint（取出现次数最多的那个）。"""
    counts: dict[str, int] = {}
    for f in CLIENTS.glob("*.conf"):
        try:
            body = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(r"^\s*Endpoint\s*=\s*(\S+)\s*$", body, re.M)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


def detect_lan_allowed(reg_clients: dict) -> list[str]:
    """从已有客户端配置里推断「局域网模式」的 AllowedIPs。

    现网可能同时存在好几种口径，这里取**覆盖最广**的那一种（CIDR 数最多），
    因为「只放行 10.8.1.0/24、进不了家里内网」的模板对使用者几乎没用。
    """
    counts: dict[str, int] = {}
    for f in CLIENTS.glob("*.conf"):
        try:
            body = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(r"^\s*AllowedIPs\s*=\s*(\S.*)$", body, re.M)
        if not m:
            continue
        val = ", ".join(parse_allowed_ips(m.group(1)))
        if val.strip() in (", ".join(FULL_ALLOWED), "0.0.0.0/0"):
            continue
        counts[val] = counts.get(val, 0) + 1
    if not counts:
        return []
    best = max(counts.items(), key=lambda kv: (len(parse_allowed_ips(kv[0])), kv[1]))
    return parse_allowed_ips(best[0])


def cmd_adopt(args):
    if not args.yes and not args.dry_run:
        die("adopt 会重写 wg0.conf（先自动备份）。确认请加 --yes，只预览加 --dry-run", 3)
    iface_pairs, peers = parse_conf(CONF)
    im = iface_map(iface_pairs)
    pub2name = load_client_pubkey_map()
    reg = load_registry()
    clients = reg.get("clients", {})

    for idx, p in enumerate(peers, 1):
        pk = p.get("PublicKey")
        if not pk:
            continue
        name = pub2name.get(pk) or p.get("__comment") or f"peer-{idx}"
        if name in clients and clients[name].get("pubkey") == pk:
            continue
        allowed = parse_allowed_ips(p.get("AllowedIPs", ""))
        ip = next((a.split("/")[0] for a in allowed if a.endswith("/32")), "")
        if not ip:
            ip = next((a.split("/")[0] for a in allowed), "")
        clients[name] = {
            "pubkey": pk,
            "ip": ip,
            "allowed_ips": allowed,
            "endpoint": p.get("Endpoint", ""),
            "tunnel": "",
            "created": "",
            "note": "adopt 纳管" + ("（原始配置含静态 Endpoint）" if p.get("Endpoint") else ""),
        }
        if p.get("PresharedKey"):
            clients[name]["preshared_key"] = p["PresharedKey"]
        if p.get("PersistentKeepalive"):
            clients[name]["keepalive"] = p["PersistentKeepalive"]

    lan = detect_lan_allowed(clients) or [iface_network(im["Address"])]
    reg = {
        "version": 1,
        "adopted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "interface": {
            "address": im.get("Address", ""),
            "listen_port": int(im.get("ListenPort", "51820")),
            "post_up": im.get("PostUp", ""),
            "post_down": im.get("PostDown", ""),
            "mtu": im.get("MTU", ""),
        },
        "advertised_endpoint": reg.get("advertised_endpoint") or detect_advertised_endpoint(clients),
        "client_lan_allowed_ips": lan,
        "client_full_allowed_ips": list(FULL_ALLOWED),
        "clients": clients,
    }
    text = render_conf(reg["interface"], clients, _priv_key())

    if args.dry_run:
        if args.emit:
            # 输出「将要写入」的配置（私钥已遮罩，仅用于比对/审查）
            sys.stdout.write(mask_secrets(text))
            return 0
        print(f"（预览模式，未写入任何文件）")
        print(f"将纳管 {len(clients)} 个客户端，SaveConfig → false，"
              f"并对齐 Key 序与补回 `# 名字` 注释。\n")
        cur = mask_secrets(CONF.read_text(encoding="utf-8"))
        new = mask_secrets(text)
        if cur.strip() == new.strip():
            print(color("✅ 现网配置已与登记表渲染结果一致，无需改动", C_OK))
        else:
            import difflib
            for l in difflib.unified_diff(cur.splitlines(), new.splitlines(),
                                          "现网 wg0.conf", "渲染结果", lineterm="", n=1):
                print(l)
        print()
        print(f"对外 Endpoint：{reg['advertised_endpoint'] or '（未识别）'}")
        print(f"局域网模式 AllowedIPs：{', '.join(lan)}")
        return 0

    save_registry(reg)
    write_conf(text, "adopt")
    if args.json:
        print(json.dumps({"adopted": len(clients),
                          "advertised_endpoint": reg["advertised_endpoint"],
                          "client_lan_allowed_ips": lan,
                          "registry": str(REGISTRY)}, ensure_ascii=False, indent=2))
        return 0
    print(f"✅ 已纳管 {len(clients)} 个客户端；SaveConfig 已改为 false")
    print(f"   对外 Endpoint：{reg['advertised_endpoint'] or '（未识别，请手工补 registry.json）'}")
    print(f"   局域网模式 AllowedIPs：{', '.join(lan)}")
    print(f"   登记表：{REGISTRY}")
    return 0


# ─────────────────────────── prune ───────────────────────────

def cmd_prune(args):
    v = view()
    days = args.stale_days
    stale = [r for r in v["rows"]
             if r["in_registry"] and r["handshake"] and time.time() - r["handshake"] > days * 86400]
    if not stale:
        print(f"没有超过 {days} 天未握手的节点")
        return 0
    for r in stale:
        print(f"  {r['name']:<18}{r['ip']:<14}{r['handshake_human']}")
    if not args.yes:
        print(f"\n共 {len(stale)} 个；要删除请加 --yes")
        return 0
    for r in stale:
        args.name = r["name"]
        a = argparse.Namespace(**vars(args))
        a.name = r["name"]
        cmd_remove(a)
    return 0


# ─────────────────────────── CLI ───────────────────────────

def build_parser():
    ap = argparse.ArgumentParser(description="WireGuard 服务端管理器")
    ap.add_argument("--iface", default=IFACE, help=f"接口名（默认 {IFACE}）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="客户端一览")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="接口状态摘要")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show", help="单个客户端详情")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.add_argument("--private", action="store_true",
                   help="输出含私钥的客户端配置（默认遮罩为 <redacted>）")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("qr", help="输出终端二维码")
    p.add_argument("name")
    p.set_defaults(func=cmd_qr)

    p = sub.add_parser("add", help="新增客户端")
    p.add_argument("name")
    p.add_argument("--tunnel", choices=["lan", "full", "custom"], default="lan")
    p.add_argument("--allowed-ips", help="--tunnel custom 时使用")
    p.add_argument("--endpoint", help="覆盖对外 Endpoint")
    p.add_argument("--dns", help="下发给客户端的 DNS")
    p.add_argument("--mtu", type=int)
    p.add_argument("--ip", help="手工指定 IP")
    p.add_argument("--site-routes", help="此节点背后的局域网网段，逗号分隔（站点互联用），"
                                         "如：192.168.1.0/24")
    p.add_argument("--note")
    p.add_argument("--force", action="store_true", help="覆盖同名客户端")
    p.add_argument("--qr", action="store_true", help="顺便打印二维码")
    p.add_argument("--show-conf", action="store_true", help="打印含私钥的客户端配置")
    p.add_argument("--include-private", action="store_true",
                   help="配合 --json：在 conf_text 里带上真实私钥")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="删除客户端")
    p.add_argument("name")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--keep-runtime", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("update", help="修改客户端（隧道模式 / AllowedIPs / IP / 备注）")
    p.add_argument("name")
    p.add_argument("--tunnel", choices=["lan", "full", "custom"])
    p.add_argument("--allowed-ips", help="客户端侧 AllowedIPs（隧道模式为 custom 时生效）")
    p.add_argument("--ip", help="改虚拟 IP")
    p.add_argument("--site-routes", help="此节点背后的局域网网段，逗号分隔；传空串表示清除")
    p.add_argument("--note")
    p.add_argument("--force", action="store_true", help="强制占用已被使用的 IP")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("disable", help="停用客户端（保留条目与私钥，移出 wg0.conf）")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("enable", help="重新启用客户端")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("resign", help="重签客户端配置（Endpoint/网段变更后）")
    p.add_argument("name", nargs="?", help="客户端名；配合 --all 可省略")
    p.add_argument("--all", action="store_true", help="重签全部客户端")
    p.add_argument("--endpoint", help="用指定 Endpoint 重签（默认取登记表）")
    p.add_argument("--include-private", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resign)

    p = sub.add_parser("set-endpoint", help="设置对外 Endpoint（客户端连的地址:端口）")
    p.add_argument("endpoint")
    p.add_argument("--resign-all", action="store_true", help="同时重签全部客户端配置")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_set_endpoint)

    p = sub.add_parser("set-lan", help="设置局域网模式的客户端 AllowedIPs")
    p.add_argument("nets", help="逗号分隔，如：10.8.1.0/24, 192.168.1.0/24")
    p.add_argument("--resign-all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_set_lan)

    p = sub.add_parser("set-dns", help="设置下发给客户端的 DNS")
    p.add_argument("dns")
    p.add_argument("--resign-all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_set_dns)

    p = sub.add_parser("set-mtu", help="设置下发给客户端的 MTU")
    p.add_argument("mtu", nargs="?", default="")
    p.add_argument("--resign-all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_set_mtu)

    p = sub.add_parser("ip-pool", help="虚拟 IP 占用与空闲视图")
    p.add_argument("--limit", type=int, default=50, help="最多列出多少个空闲 IP")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ip_pool)

    p = sub.add_parser("sync", help="按登记表重渲染并同步")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("render", help="渲染配置文本")
    p.add_argument("--check", action="store_true", help="只比对当前配置是否一致")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("adopt", help="把现网配置纳管进登记表")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="只预览差异，不写任何文件")
    p.add_argument("--emit", action="store_true", help="配合 --dry-run：直接输出将写入的配置（私钥遮罩）")
    p.set_defaults(func=cmd_adopt)

    p = sub.add_parser("backup", help="备份当前配置")
    p.add_argument("--keep", type=int, default=BACKUP_KEEP)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("backups", help="列出备份")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_backups)

    p = sub.add_parser("restore", help="恢复备份")
    p.add_argument("file")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("prune", help="列出 / 删除僵尸节点")
    p.add_argument("--stale-days", type=int, default=90)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("doctor", help="体检")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("fix-perms", help="把私钥/配置权限收紧到 600")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fix_perms)
    return ap


def main():
    global IFACE, CONF, WG_DIR, BASE, CLIENTS, BACKUP_DIR, REGISTRY  # noqa
    ap = build_parser()
    args = ap.parse_args()
    if args.iface:
        IFACE = args.iface
        CONF = WG_DIR / f"{IFACE}.conf"
    need_root()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
