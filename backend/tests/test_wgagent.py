"""wgagent.py 行为契约。

这些用例锁住的是「管理语义」而不是实现细节：
IP 分发、停用不删、Endpoint 变更重签、写前必备份、私钥默认不可见。
"""

from __future__ import annotations

import pytest


def test_add_allocates_ip_and_writes_client_files(wg):
    r = wg["run"]("add", "iphone15", "--tunnel", "lan")
    assert r["ip"] == "10.8.1.2"
    assert r["pubkey"].startswith("FAKEPUB_")
    assert r["endpoint"] == "vpn.example.com:10015"

    for ext in (".key", ".pub", ".conf"):
        f = wg["clients_dir"] / f"iphone15{ext}"
        assert f.exists(), f"缺 {f.name}"
        assert oct(f.stat().st_mode)[-3:] == "600", f"{f.name} 权限不是 600"


def test_add_assigns_next_free_ip(wg):
    wg["run"]("add", "a", "--ip", "10.8.1.2")
    r = wg["run"]("add", "b")
    assert r["ip"] == "10.8.1.3"


def test_add_json_hides_private_key_by_default(wg):
    r = wg["run"]("add", "mac", "--tunnel", "lan")
    assert "conf_text" not in r, "默认不应回传配置明文"

    r2 = wg["run"]("add", "mac2", "--tunnel", "lan", "--include-private")
    assert "FAKEPRIV_" in r2["conf_text"], "--include-private 才给明文"


def test_client_conf_carries_lan_routes_and_endpoint(wg):
    wg["run"]("add", "pad", "--tunnel", "lan")
    body = wg["client_conf"]("pad")
    assert "Endpoint = vpn.example.com:10015" in body
    assert "AllowedIPs = 10.8.1.0/24, 192.168.1.0/24" in body
    assert "Address = 10.8.1.2/24" in body


def test_full_tunnel_client_gets_default_route(wg):
    wg["run"]("add", "phone", "--tunnel", "full")
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in wg["client_conf"]("phone")


def test_disable_keeps_registry_entry_but_drops_from_conf(wg):
    wg["run"]("add", "pc", "--tunnel", "lan")
    assert "PublicKey = " in wg["conf_text"]()

    r = wg["run"]("disable", "pc")
    assert r["disabled"] is True
    assert "pc" not in wg["conf_text"]() or "FAKEPUB_" not in wg["conf_text"]()
    assert "pc" in wg["registry"]()["clients"], "停用不能删条目"
    assert (wg["clients_dir"] / "pc.key").exists(), "停用不能删私钥"


def test_enable_restores_peer_into_conf(wg):
    wg["run"]("add", "pc", "--tunnel", "lan")
    wg["run"]("disable", "pc")
    r = wg["run"]("enable", "pc")
    assert r["disabled"] is False
    assert "[Peer]" in wg["conf_text"]()


def test_ip_pool_reports_used_free_and_next(wg):
    wg["run"]("add", "a", "--ip", "10.8.1.2")
    wg["run"]("add", "b", "--ip", "10.8.1.5")
    pool = wg["run"]("ip-pool")

    assert pool["network"] == "10.8.1.0/24"
    assert pool["used_count"] == 2
    assert pool["next"] == "10.8.1.3", "应跳过已占用的 .2 和 .5"
    assert {"ip": "10.8.1.2", "name": "a"} in pool["used"]


def test_set_endpoint_then_resign_all_updates_client_confs(wg):
    wg["run"]("add", "phone", "--tunnel", "lan")
    assert "vpn.example.com:10015" in wg["client_conf"]("phone")

    r = wg["run"]("set-endpoint", "vpn2.example.com:51820", "--resign-all")
    assert r["advertised_endpoint"] == "vpn2.example.com:51820"
    assert r["resigned"] == 1
    assert "vpn2.example.com:51820" in wg["client_conf"]("phone")


def test_set_lan_resigns_client_allowed_ips(wg):
    wg["run"]("add", "phone", "--tunnel", "lan")
    wg["run"]("set-lan", "10.8.1.0/24, 192.168.1.0/24, 192.168.2.0/24", "--resign-all")
    assert "192.168.2.0/24" in wg["client_conf"]("phone")


def test_remove_frees_ip_and_cleans_files(wg):
    wg["run"]("add", "temp", "--tunnel", "lan")
    r = wg["run"]("remove", "temp", "--yes")
    assert r["deleted"] is True
    assert "temp" not in wg["registry"]()["clients"]
    assert not (wg["clients_dir"] / "temp.conf").exists()

    again = wg["run"]("add", "temp2")
    assert again["ip"] == "10.8.1.2", "删除后 IP 应回收"


def test_every_write_makes_a_backup(wg):
    wg["run"]("add", "a", "--tunnel", "lan")
    wg["run"]("add", "b", "--tunnel", "lan")
    backups = sorted(wg["backups"].glob("wg0.conf.*"))
    assert len(backups) >= 2, "每次写配置前都应自动备份"


def test_backups_listing_is_json(wg):
    wg["run"]("add", "a")
    rows = wg["run"]("backups")
    assert isinstance(rows, list)
    assert rows and rows[0]["name"].startswith("wg0.conf.")


def test_render_matches_registry_after_changes(wg):
    wg["run"]("add", "a", "--tunnel", "lan")
    wg["run"]("disable", "a")
    out = wg["run"]("render", "--check", expect_ok=False, as_json=False)
    assert "一致" in out.get("raw", ""), "登记表渲染结果应与现网配置一致"


def test_show_masks_private_key_by_default(wg):
    wg["run"]("add", "phone", "--tunnel", "lan")
    detail = wg["run"]("show", "phone")
    assert "<redacted>" in detail["conf_text"]
    assert "FAKEPRIV_" not in detail["conf_text"]

    revealed = wg["run"]("show", "phone", "--private")
    assert "FAKEPRIV_" in revealed["conf_text"]


def test_manual_duplicate_ip_is_rejected(wg):
    """虚拟 IP 是分发资源：手工指定撞车必须当场拒绝，而不是等体检才发现。"""
    wg["run"]("add", "a", "--ip", "10.8.1.9")
    with pytest.raises(AssertionError) as exc:
        wg["run"]("add", "b", "--ip", "10.8.1.9")
    assert "已被" in str(exc.value) and "10.8.1.9" in str(exc.value)


def test_resign_reports_failure_without_private_key(wg):
    with pytest.raises(AssertionError):
        wg["run"]("resign", "ghost")
