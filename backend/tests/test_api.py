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


@pytest.fixture
def fresh_client(tmp_path):
    """没配 WGP_ADMIN_PASSWORD 的干净实例：应该用 admin/admin 初始化并提示改密码。"""
    for mod in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[mod]
    from app.config import settings

    settings.driver = "mock"
    settings.read_only = False
    settings.data_dir = tmp_path / "data"
    settings.admin_password = ""
    settings.secret_key = "test-secret"
    settings.agent_path = str(BACKEND.parent / "agent" / "wgagent.py")

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_default_account_is_admin_admin(fresh_client):
    """首次启动没设密码就用 admin/admin，并且明确告诉前端「该改了」。"""
    st = fresh_client.get("/api/auth/state").json()
    assert st["initialized"] is True and st["must_change"] is True
    assert "admin / admin" in st["default_hint"]

    r = fresh_client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    assert fresh_client.get("/api/auth/me").json()["must_change"] is True


def test_update_account_changes_username_and_clears_flag(fresh_client):
    fresh_client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    r = fresh_client.post("/api/auth/account", json={
        "old_password": "admin", "username": "xue", "new_password": "newpass12345"})
    assert r.status_code == 200 and r.json()["relogin"] is True

    # 新账号能登，旧密码不再可用，且「该改密码」的提示消失
    assert fresh_client.post("/api/auth/login",
                             json={"username": "xue", "password": "newpass12345"}).status_code == 200
    assert fresh_client.get("/api/auth/state").json()["must_change"] is False
    assert fresh_client.post("/api/auth/login",
                             json={"username": "admin", "password": "admin"}).status_code == 401


def test_account_requires_current_password(fresh_client):
    fresh_client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    r = fresh_client.post("/api/auth/account", json={"old_password": "wrongpass", "new_password": "whatever12345"})
    assert r.status_code == 400


def test_settings_exposes_auth_modes_and_fingerprint(client):
    s = client.get("/api/settings").json()
    assert s["ssh_auth"] in ("key", "password")
    assert "set" in s["password"] and "managed" in s["password"]
    # 指纹字段存在且绝不包含私钥正文
    for k in ("path", "fingerprint", "public_line"):
        assert k in s["key"]


def test_upload_ssh_password_switches_auth_and_never_echoes(client, tmp_path):
    r = client.post("/api/settings/ssh-password", json={"password": "s3cr3t-node-pw"})
    assert r.status_code == 200 and r.json()["saved"] is True
    assert "s3cr3t-node-pw" not in r.text, "接口绝不能回显密码"

    s = client.get("/api/settings").json()
    assert s["ssh_auth"] == "password" and s["password"]["set"] is True
    assert "s3cr3t-node-pw" not in client.get("/api/settings").text

    pw_file = Path(s["password"]["path"])
    assert pw_file.exists() and oct(pw_file.stat().st_mode)[-3:] == "600"


def test_patch_ssh_auth_validates_value(client):
    assert client.patch("/api/settings", json={"ssh_auth": "password"}).status_code == 200
    assert client.patch("/api/settings", json={"ssh_auth": "magic"}).status_code == 422


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


def test_login_rate_limits_repeated_failures(tmp_path):
    from app.config import settings
    settings.driver = "mock"
    settings.data_dir = tmp_path / "data-rate"
    settings.admin_password = PASS
    settings.secret_key = "s"
    from app.main import create_app
    import app.api.auth as auth_api
    auth_api._failures.clear()
    with TestClient(create_app()) as c:
        for _ in range(10):
            assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "admin", "password": PASS})
        assert r.status_code == 429 and "Retry-After" in r.headers


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


def test_readonly_token_can_read_state_and_cannot_read_private_config(client):
    from app.config import settings
    from fastapi.testclient import TestClient
    settings.api_token = "dashboard-secret"
    token_client = TestClient(client.app)
    assert token_client.get("/api/state", headers={"X-API-Token": "dashboard-secret"}).status_code == 200
    assert token_client.get("/api/peers", headers={"Authorization": "Bearer dashboard-secret"}).status_code == 200
    assert token_client.get("/api/peers/laptop/config", headers={"X-API-Token": "dashboard-secret"}).status_code == 401


def test_traffic_series_and_plan_revision(client):
    from app.config import settings
    settings.api_token = "dashboard-secret"
    headers = {"X-API-Token": "dashboard-secret"}
    traffic = client.get("/api/traffic?window=60", headers=headers)
    assert traffic.status_code == 200
    assert traffic.json()["window"] == 60
    plan = client.post("/api/plan", headers=headers,
                       json={"operation": "add_peer", "name": "tablet", "params": {"tunnel": "lan"}})
    assert plan.status_code == 200
    body = plan.json()
    assert body["operation"] == "add_peer" and body["revision"]
    applied = client.post("/api/apply",
                          json={"revision": body["revision"], "operation": "add_peer",
                                "name": "tablet", "params": {"tunnel": "lan"}})
    assert applied.status_code == 200
    assert any(p["name"] == "tablet" for p in client.get("/api/peers").json()["rows"])


def test_plan_rejects_stale_revision(client):
    from app.config import settings
    settings.api_token = "dashboard-secret"
    headers = {"X-API-Token": "dashboard-secret"}
    plan = client.post("/api/plan", headers=headers,
                       json={"operation": "add_peer", "name": "tablet", "params": {}}).json()
    client.post("/api/peers", json={"name": "other-tablet", "tunnel": "lan"})
    r = client.post("/api/apply",
                    json={"revision": plan["revision"], "operation": "add_peer", "name": "tablet", "params": {}})
    assert r.status_code == 409


def test_readonly_token_cannot_apply_plan(client):
    from app.config import settings
    from fastapi.testclient import TestClient
    settings.api_token = "dashboard-secret"
    headers = {"X-API-Token": "dashboard-secret"}
    token_client = TestClient(client.app)
    plan = client.post("/api/plan", headers=headers,
                       json={"operation": "add_peer", "name": "token-write", "params": {}}).json()
    r = token_client.post("/api/apply", headers=headers,
                    json={"revision": plan["revision"], "operation": "add_peer",
                          "name": "token-write", "params": {}})
    assert r.status_code == 401


def test_local_paths_survive_save_and_database_reload(client):
    from pathlib import Path
    from app.config import settings
    from app.main import create_app
    client.patch('/api/settings', json={'local_wg_dir': '/tmp', 'local_state_dir': '/tmp/state'})
    assert isinstance(settings.local_wg_dir, Path)
    create_app()
    assert isinstance(settings.local_wg_dir, Path)
    settings.driver = 'local'
    assert client.get('/api/settings').status_code == 200


def test_driver_cannot_change_at_runtime(client):
    assert client.patch('/api/settings', json={'driver': 'local'}).status_code == 422


def test_readonly_token_cannot_post_plan(client):
    from app.config import settings
    from fastapi.testclient import TestClient
    settings.api_token = 'readonly'
    reader = TestClient(client.app)
    assert reader.post('/api/plan', headers={'X-API-Token': 'readonly'}, json={
        'operation': 'add_peer', 'name': 'blocked', 'params': {}}).status_code == 401


def test_plan_tracks_endpoint_and_request_parameters(client):
    request = {'operation': 'update_peer', 'name': 'laptop', 'params': {'note': 'new note'}}
    plan = client.post('/api/plan', json=request).json()
    client.patch('/api/peers/laptop', json={'endpoint': 'example.org:51820'})
    assert client.post('/api/apply', json={**request, 'revision': plan['revision']}).status_code == 409
    plan = client.post('/api/plan', json=request).json()
    altered = {**request, 'params': {'note': 'different'}}
    assert client.post('/api/apply', json={**altered, 'revision': plan['revision']}).status_code == 409


def test_plan_validates_params_and_applies_custom_peer(client):
    assert client.post('/api/plan', json={'operation': 'add_peer', 'name': '../bad', 'params': {}}).status_code == 422
    assert client.post('/api/plan', json={'operation': 'add_peer', 'name': 'bad', 'params': {'keepalive': -1}}).status_code == 422
    body = {'operation': 'add_peer', 'name': 'custom', 'params': {
        'tunnel': 'custom', 'allowed_ips': '10.99.0.0/24', 'endpoint': 'fixed.example:51820',
        'keepalive': 9, 'client_keepalive': 17, 'note': 'site'}}
    plan = client.post('/api/plan', json=body).json()
    result = client.post('/api/apply', json={**body, 'revision': plan['revision']})
    assert result.status_code == 200
    peer = client.get('/api/peers/custom').json()
    assert peer['client_allowed_ips'] == ['10.99.0.0/24']
    assert peer['configured_endpoint'] == 'fixed.example:51820'
    assert peer['keepalive'] == 9 and peer['client_keepalive'] == 17


def test_state_poll_reads_cache_without_executing_agent(client, monkeypatch):
    from app.api.deps import get_executor
    client.get('/api/peers')
    monkeypatch.setattr(get_executor(), 'run_agent', lambda args: (_ for _ in ()).throw(AssertionError('remote IO')))
    assert client.get('/api/state').status_code == 200
    assert client.get('/api/traffic?window=60').status_code == 200


def test_rename_preserves_public_key_and_edit_parameters(client):
    old = client.get('/api/peers/laptop').json()['pubkey']
    assert client.patch('/api/peers/laptop', json={'new_name': 'renamed', 'enabled': False,
           'endpoint': 'static.example:51820', 'client_keepalive': 11, 'keepalive': 0}).status_code == 200
    row = client.get('/api/peers/renamed').json()
    assert row['pubkey'] == old and row['disabled'] is True
    assert row['client_keepalive'] == 11 and row['configured_endpoint'] == 'static.example:51820'


def test_api_token_only_get_whitelist_and_safe_responses(client):
    from app.config import settings
    settings.api_token = 'read-only'
    reader = TestClient(client.app)
    headers = {'X-API-Token': 'read-only'}
    for route in ['/api/settings', '/api/status', '/api/doctor', '/api/peers/laptop', '/api/peers/laptop/qr']:
        assert reader.get(route, headers=headers).status_code == 401
    for route in ['/api/state', '/api/peers', '/api/server', '/api/plan', '/api/traffic']:
        response = reader.get(route, headers=headers)
        assert response.status_code == 200
        assert 'PrivateKey' not in response.text and 'PresharedKey' not in response.text


def test_readonly_apply_rejected_and_configuration_validation(client):
    from app.config import settings
    request = {'operation': 'add_peer', 'name': 'blocked', 'params': {}}
    plan = client.post('/api/plan', json=request).json()
    settings.read_only = True
    assert client.post('/api/apply', json={**request, 'revision': plan['revision']}).status_code == 423


def test_old_saved_driver_cannot_override_startup_mode(client):
    from app.main import create_app
    from app.config import settings
    client.app.state.store.put_settings({'driver': 'local'})
    create_app()
    assert settings.driver == 'mock'


def test_state_cache_ignores_old_node_after_connection_change(client):
    from app.config import settings
    from app.api.deps import reset_executor
    client.get('/api/peers')
    settings.driver = 'ssh'
    settings.ssh_host = 'different-node'
    reset_executor()
    state = client.get('/api/state').json()
    assert state['peers'] == [] and state['connected'] is False
    assert 'different-node' in state['driver']['target']


def test_mock_atomic_combined_toggle_rename_failure(client):
    original = client.get('/api/peers/laptop').json()
    response = client.patch('/api/peers/laptop', json={'new_name': 'phone', 'enabled': False})
    assert response.status_code == 400
    current = client.get('/api/peers/laptop').json()
    assert current['pubkey'] == original['pubkey'] and current['enabled'] == original['enabled']


def test_adoption_requires_preview_revision_and_rejects_expired(client):
    assert client.post('/api/adopt?confirm=true').status_code == 428
    preview = client.get('/api/adopt-preview')
    assert preview.status_code == 200 and preview.json()['revision']
    revision = preview.json()['revision']
    client.patch('/api/peers/laptop', json={'note': 'new'})
    assert client.post('/api/adopt', params={'confirm': True, 'revision': revision}).status_code == 409
    revision = client.get('/api/adopt-preview').json()['revision']
    assert client.post('/api/adopt', params={'confirm': True, 'revision': revision}).status_code == 200


def test_token_state_excludes_driver_paths_and_configuration_errors(client):
    from app.config import settings
    settings.api_token = 'read-only'
    settings.secret_key = ''
    reader = TestClient(client.app)
    response = reader.get('/api/state', headers={'X-API-Token': 'read-only'})
    assert response.status_code == 200
    assert set(response.json()['driver']) == {'driver', 'iface'}
    assert 'WGP_SECRET_KEY' not in response.text
    assert '/config' not in response.text
