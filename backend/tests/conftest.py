"""测试脚手架：把 wgagent.py 关进 tmp_path 的沙箱里跑。

要点：
- 用假 `wg` / `wg-quick` 二进制顶替（WG_BIN / WG_QUICK_BIN），**绝不碰真实 /etc/wireguard**
- WG_SKIP_ROOT=1 跳过 root 检查（开发机是普通用户）
- 所有路径走 WG_DIR / WG_BASE 环境变量
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]          # 项目根（backend/tests → 根）
AGENT = ROOT / "agent" / "wgagent.py"

SERVER_PRIV = "FAKEPRIV_SERVER"
SERVER_PUB = "FAKEPUB_SERVER"
ENDPOINT = "vpn.example.com:10015"
LAN_NETS = ["10.8.1.0/24", "192.168.1.0/24"]

FAKE_WG = '#!/usr/bin/env python3\nimport hashlib, json, os, sys\nfrom pathlib import Path\na = sys.argv[1:]\nstate = Path(os.environ[\'WG_BASE\']) / \'fake-runtime.json\'\nif not a:\n    sys.exit(1)\nif a[0] == "genkey":\n    print(\'FAKEPRIV_\' + os.urandom(8).hex())\nelif a[0] == "pubkey":\n    priv = sys.stdin.read().strip()\n    print(\'FAKEPUB_SERVER\' if priv == \'FAKEPRIV_SERVER\' else "FAKEPUB_" + hashlib.sha256(priv.encode()).hexdigest()[:16])\nelif a[0] == "show" and "dump" in a:\n    print("FAKEPRIV_SERVER\\tFAKEPUB_SERVER\\t10015\\toff")\n    for peer in json.loads(state.read_text()) if state.exists() else []:\n        print(\'\\t\'.join([peer[\'PublicKey\'],peer.get(\'PresharedKey\',\'(none)\'),peer.get(\'Endpoint\',\'(none)\'),peer[\'AllowedIPs\'], \'0\',\'0\',\'0\',peer.get(\'PersistentKeepalive\',\'0\')]))\nelif a[0] == "show" and a[-1] == "public-key":\n    print("FAKEPUB_SERVER")\nelif a[0] == "syncconf":\n    peers=[]; cur=None\n    for line in Path(a[-1]).read_text().splitlines():\n        line=line.strip()\n        if line == \'[Peer]\': cur={}; peers.append(cur)\n        elif \'=\' in line and cur is not None:\n            key,value=line.split(\'=\',1);cur[key.strip()]=value.strip()\n    state.write_text(json.dumps(peers))\n    sys.exit(0)\nelse:\n    sys.exit(0)\n'

FAKE_WG_QUICK = '''#!/usr/bin/env python3
import sys
if len(sys.argv) > 2 and sys.argv[1] == "strip":
    print(open(sys.argv[2]).read(), end="")
    sys.exit(0)
sys.exit(0)
'''


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def wg(tmp_path) -> dict:
    """一个完整的沙箱：配置目录 + 登记表 + 假的 wg 二进制。"""
    wg_dir = tmp_path / "wireguard"
    base = tmp_path / "root"
    wg_dir.mkdir()
    base.mkdir()

    (wg_dir / "wg0.conf").write_text(
        "[Interface]\n"
        "Address = 10.8.1.1/24\n"
        "SaveConfig = false\n"
        "ListenPort = 10015\n"
        f"PrivateKey = {SERVER_PRIV}\n"
        "PostUp = iptables -A FORWARD -i wg0 -j ACCEPT\n"
        "PostDown = iptables -D FORWARD -i wg0 -j ACCEPT\n",
        encoding="utf-8",
    )
    (base / "registry.json").write_text(
        json.dumps({
            "version": 1,
            "interface": {
                "address": "10.8.1.1/24",
                "listen_port": 10015,
                "post_up": "iptables -A FORWARD -i wg0 -j ACCEPT",
                "post_down": "iptables -D FORWARD -i wg0 -j ACCEPT",
                "mtu": "",
            },
            "advertised_endpoint": ENDPOINT,
            "client_lan_allowed_ips": list(LAN_NETS),
            "client_full_allowed_ips": ["0.0.0.0/0", "::/0"],
            "clients": {},
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fake_bin = _write_exec(tmp_path / "fake-wg", FAKE_WG)
    fake_quick = _write_exec(tmp_path / "fake-wg-quick", FAKE_WG_QUICK)

    fake_ip = _write_exec(tmp_path / 'fake-ip', """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['WG_BASE']) / 'ip-commands.json'
rows=json.loads(p.read_text()) if p.exists() else []
rows.append(sys.argv[1:]);p.write_text(json.dumps(rows))
if '-j' in sys.argv: print('[]')
""")
    env = dict(os.environ)
    env.update({
        "WG_DIR": str(wg_dir),
        "WG_BASE": str(base),
        "WG_IFACE": "wg0",
        "WG_BIN": str(fake_bin),
        "WG_QUICK_BIN": str(fake_quick),
        "WG_SKIP_ROOT": "1",
        "WG_IP_BIN": str(fake_ip),
    })

    def run(*args, expect_ok: bool = True, as_json: bool = True) -> dict:
        """执行 wgagent 子命令；默认 --json，返回解析后的 dict。"""
        argv = [sys.executable, str(AGENT), "--iface", "wg0"]
        argv += [str(a) for a in args]
        if as_json and "--json" not in argv:
            argv.append("--json")
        p = subprocess.run(argv, capture_output=True, text=True, env=env)
        if expect_ok and p.returncode != 0:
            raise AssertionError(
                f"wgagent {args} 失败 (exit={p.returncode})\nstdout={p.stdout}\nstderr={p.stderr}")
        out = p.stdout.strip()
        if not out:
            return {}
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"raw": out}

    def conf_text() -> str:
        return (wg_dir / "wg0.conf").read_text(encoding="utf-8")

    def registry() -> dict:
        return json.loads((base / "registry.json").read_text(encoding="utf-8"))

    def client_conf(name: str) -> str:
        return (base / "clients" / f"{name}.conf").read_text(encoding="utf-8")

    return {
        "run": run,
        "conf_text": conf_text,
        "registry": registry,
        "client_conf": client_conf,
        "wg_dir": wg_dir,
        "base": base,
        "clients_dir": base / "clients",
        "backups": base / "backup",
        "fake_wg": fake_bin,
    }
