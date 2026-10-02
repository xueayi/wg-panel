"""中转节点：对外 Endpoint、转发网段、客户端 DNS/MTU，以及虚拟 IP 池视图。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .deps import agent_call, current_user, get_executor

router = APIRouter(prefix="/api", tags=["server"])


class ServerPatch(BaseModel):
    endpoint: Optional[str] = None          # 形如 vpn.example.com:10015
    lan_allowed_ips: Optional[str] = None   # 逗号分隔
    dns: Optional[str] = None
    mtu: Optional[int] = None
    resign_all: bool = False                # 是否同时重签已下发的客户端配置


def _store(request: Request):
    return request.app.state.store


@router.get("/server")
def get_server(request: Request, _: str = Depends(current_user)):
    """接口状态 + 中转节点参数 + 在线统计，一屏给全。"""
    data = agent_call(get_executor(), ["list"], store=_store(request))
    reg = data.get("registry", {}) or {}
    rows = data.get("rows", [])
    iface = data.get("interface", {}) or {}
    return {
        "interface": {
            "name": "wg0",
            "address": iface.get("Address", ""),
            "listen_port": iface.get("ListenPort", ""),
            "public_key": "",  # 由 status 补全
            "save_config": iface.get("SaveConfig", "false"),
        },
        "advertised_endpoint": reg.get("advertised_endpoint", ""),
        "client_lan_allowed_ips": reg.get("client_lan_allowed_ips", []),
        "client_dns": reg.get("client_dns", ""),
        "client_mtu": reg.get("client_mtu", ""),
        "stats": {
            "total": len(rows),
            "online": sum(1 for r in rows if r.get("state") == "在线"),
            "disabled": sum(1 for r in rows if r.get("disabled")),
            "rx": sum(r.get("rx", 0) for r in rows),
            "tx": sum(r.get("tx", 0) for r in rows),
        },
    }


@router.patch("/server")
def patch_server(body: ServerPatch, request: Request, _: str = Depends(current_user)):
    results = {}
    resign = ["--resign-all"] if body.resign_all else []
    if body.endpoint:
        results["endpoint"] = agent_call(
            get_executor(), ["set-endpoint", body.endpoint] + resign,
            action="server.set_endpoint", target=body.endpoint, store=_store(request))
    if body.lan_allowed_ips:
        results["lan"] = agent_call(
            get_executor(), ["set-lan", body.lan_allowed_ips] + resign,
            action="server.set_lan", target=body.lan_allowed_ips, store=_store(request))
    if body.dns is not None:
        results["dns"] = agent_call(
            get_executor(), ["set-dns", body.dns] + resign,
            action="server.set_dns", target=body.dns, store=_store(request))
    if body.mtu is not None:
        results["mtu"] = agent_call(
            get_executor(), ["set-mtu", str(body.mtu)] + resign,
            action="server.set_mtu", target=str(body.mtu), store=_store(request))
    if not results:
        return {"changed": False}
    _store(request).audit("server.update", "", "ok", ",".join(results))
    return {"changed": True, **results}


@router.get("/ip-pool")
def ip_pool(request: Request, _: str = Depends(current_user)):
    return agent_call(get_executor(), ["ip-pool"], store=_store(request))


@router.post("/sync-site-routes")
def sync_site_routes(request: Request, confirm: bool = Query(False),
                     _: str = Depends(current_user)):
    """把所有站点背后的局域网网段并入「客户端 AllowedIPs」并重签。

    站点互联差的就是这一步：新加了一个带 LAN 的节点，其他节点的客户端配置
    必须也包含那个网段，否则彼此访问不到。
    """
    data = agent_call(get_executor(), ["list"], store=_store(request))
    reg = data.get("registry", {}) or {}
    lan = list(reg.get("client_lan_allowed_ips", []))
    sites = sorted({c for r in data.get("rows", [])
                    for c in (r.get("site_routes") or [])})
    merged = sorted(set(lan) | set(sites), key=lambda x: (len(x), x))
    if set(merged) == set(lan):
        return {"changed": False, "site_routes": sites, "client_lan_allowed_ips": lan}
    if not confirm:
        raise HTTPException(status_code=428,
                            detail=f"将把 {', '.join(sites)} 并入客户端 AllowedIPs 并重签全部配置，"
                                   f"需要 confirm=true")
    agent_call(get_executor(), ["set-lan", ", ".join(merged), "--resign-all"],
               action="server.sync_site_routes", target=", ".join(sites),
               confirmed=True, store=_store(request))
    _store(request).audit("server.sync_site_routes", "", "ok", ", ".join(sites))
    return {"changed": True, "site_routes": sites, "client_lan_allowed_ips": merged}
