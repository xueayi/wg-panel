"""后端 API 契约测试（mock 驱动，不碰真实节点）。

锁的是面板这条边的行为：认证、二次确认、私钥默认不可见、只读护栏、审计留痕。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

PASS = "testpass12345"


@pytest.fixture
def client(tmp_path, monkeypatch):
    for mod in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[mod]
    from app.config import settings

    settings.driver = "mock"
    settings.read_only = False
    settings.data_dir = tmp_path / "data"
    settings.admin_password = PASS
    settings.secret_key = "test-secret"
    settings.agent_path = str(BACKEND.parent / "agent" / "wgagent.py")

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": PASS})
        yield c


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_api_requires_login(tmp_path, monkeypatch):
    from app.config import settings

    settings.driver = "mock"
    settings.data_dir = tmp_path / "data2"
    settings.admin_password = PASS
    settings.secret_key = "s"
    from app.main import create_app

    with TestClient(create_app()) as c:
        assert c.get("/api/peers").status_code == 401


def test_login_sets_httponly_cookie(tmp_path, monkeypatch):
    from app.config import settings

    settings.driver = "mock"
    settings.data_dir = tmp_path / "data3"
    settings.admin_password = PASS
    settings.secret_key = "s"
    from app.main import create_app

    with TestClient(create_app()) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
        r = c.post("/api/auth/login", json={"username": "admin", "password": PASS})
        assert r.status_code == 200
        assert "httponly" in r.headers.get("set-cookie", "").lower()


def test_list_peers(client):
    rows = client.get("/api/peers").json()
    assert rows["total"] >= 3
    assert any(r["name"] == "laptop" for r in rows["rows"])
    assert rows["online"] >= 1


def test_create_peer_distributes_ip(client):
    r = client.post("/api/peers", json={"name": "ipad", "tunnel": "lan", "note": "看漫画"})
    assert r.status_code == 201
    body = r.json()
    assert body["ip"].startswith("10.8.1.")
    assert body["tunnel"] == "lan"

    rows = client.get("/api/peers").json()["rows"]
    assert any(p["name"] == "ipad" for p in rows)


def test_config_download_is_masked_by_default(client):
    masked = client.get("/api/peers/laptop/config").text
    assert "<redacted>" in masked or "PrivateKey = <" in masked
    assert "FAKE" not in masked

    audit = client.get("/api/audit").json()["rows"]
    assert not any(a["action"] == "peer.reveal" for a in audit)


def test_qr_returns_png_and_is_audited(client):
    r = client.get("/api/peers/phone/qr")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert any(a["action"] == "peer.qr" for a in client.get("/api/audit").json()["rows"])


def test_delete_requires_confirmation(client):
    r = client.delete("/api/peers/old-laptop")
    assert r.status_code == 428, "删除必须二次确认"
    r = client.delete("/api/peers/old-laptop", params={"confirm": "true"})
    assert r.status_code == 200
    assert not any(p["name"] == "old-laptop" for p in client.get("/api/peers").json()["rows"])


def test_disable_keeps_peer_in_list(client):
    r = client.patch("/api/peers/laptop", json={"enabled": False})
    assert r.status_code == 200
    row = next(p for p in client.get("/api/peers").json()["rows"] if p["name"] == "laptop")
    assert row["disabled"] is True
    assert row["enabled"] is False


def test_read_only_mode_blocks_writes(tmp_path, monkeypatch):
    from app.config import settings

    settings.driver = "mock"
    settings.read_only = True
    settings.data_dir = tmp_path / "data-ro"
    settings.admin_password = PASS
    settings.secret_key = "s"
    from app.main import create_app

    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": PASS})
        assert c.get("/api/peers").status_code == 200, "只读模式仍应能看"
        r = c.post("/api/peers", json={"name": "nope", "tunnel": "lan"})
        assert r.status_code == 423, "只读模式不能写"


def test_server_endpoint_update(client):
    r = client.patch("/api/server", json={"endpoint": "vpn2.example.com:51820"})
    assert r.status_code == 200
    assert client.get("/api/server").json()["advertised_endpoint"] == "vpn2.example.com:51820"


def test_ip_pool_exposes_next_free(client):
    pool = client.get("/api/ip-pool").json()
    assert pool["network"] == "10.8.1.0/24"
    assert pool["used_count"] >= 1
    assert pool["next"].startswith("10.8.1.")
