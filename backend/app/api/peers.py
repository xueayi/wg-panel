"""peer 管理：虚拟 IP 分发 + 客户端配置导入三件套（二维码 / 下载 / 复制）。"""

from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from ..config import settings
from ..drivers.base import ExecutorError
from .deps import agent_call, current_reader, current_user, get_executor

router = APIRouter(prefix="/api/peers", tags=["peers"])


class PeerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
    tunnel: str = Field(default="lan", pattern="^(lan|full|custom)$")
    ip: Optional[str] = None
    allowed_ips: Optional[str] = None
    site_routes: Optional[str] = None      # 此节点背后的局域网网段（站点互联）
    net: Optional[str] = None              # 从哪个虚拟网段分配地址（多网段时用）
    dns: Optional[str] = None
    mtu: Optional[int] = None
    endpoint: Optional[str] = None
    advertised_endpoint: Optional[str] = None
    keepalive: Optional[int] = Field(default=None, ge=0, le=65535)
    client_keepalive: int = Field(default=25, ge=0, le=65535)
    note: str = ""


class PeerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_name: Optional[str] = Field(default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
    tunnel: Optional[str] = Field(default=None, pattern="^(lan|full|custom)$")
    allowed_ips: Optional[str] = None
    site_routes: Optional[str] = None
    ip: Optional[str] = None
    note: Optional[str] = None
    enabled: Optional[bool] = None
    endpoint: Optional[str] = None
    keepalive: Optional[int] = Field(default=None, ge=0, le=65535)
    client_keepalive: Optional[int] = Field(default=None, ge=0, le=65535)


def _store(request: Request):
    return request.app.state.store


@router.get("")
def list_peers(request: Request, _: str = Depends(current_reader)):
    data = request.app.state.state_cache.refresh(get_executor())
    if not data.get("connected"):
        raise HTTPException(status_code=400, detail=data.get("error", "读取客户端失败"))
    rows = data.get("peers", [])
    rates = {r.get("public_key"): r for r in request.app.state.traffic_sampler.snapshot().get("peers", [])}
    for row in rows:
        rate = rates.get(row.get("pubkey"), {})
        row["rx_rate"] = rate.get("rx_rate")
        row["tx_rate"] = rate.get("tx_rate")
    return {
        "rows": rows,
        "total": len(rows),
        "online": sum(1 for r in rows if r.get("state") == "在线"),
        "disabled": sum(1 for r in rows if r.get("disabled")),
    }


@router.post("", status_code=201)
def create_peer(body: PeerCreate, request: Request, _: str = Depends(current_user)):
    args = ["add", body.name, "--tunnel", body.tunnel]
    if body.ip:
        args += ["--ip", body.ip]
    if body.allowed_ips:
        args += ["--allowed-ips", body.allowed_ips]
    if body.site_routes:
        args += ["--site-routes", body.site_routes]
    if body.net:
        args += ["--net", body.net]
    if body.dns:
        args += ["--dns", body.dns]
    if body.mtu:
        args += ["--mtu", str(body.mtu)]
    if body.endpoint:
        args += ["--peer-endpoint", body.endpoint]
    if body.advertised_endpoint:
        args += ["--endpoint", body.advertised_endpoint]
    if body.keepalive is not None:
        args += ["--peer-keepalive", str(body.keepalive)]
    args += ["--client-keepalive", str(body.client_keepalive)]
    if body.note:
        args += ["--note", body.note]
    out = agent_call(get_executor(), args, action="peer.add", target=body.name,
                     store=_store(request))
    _store(request).audit("peer.add", body.name, "ok",
                          f"ip={out.get('ip')} tunnel={body.tunnel}")
    request.app.state.state_cache.refresh(get_executor())
    return out


@router.get("/{name}")
def show_peer(name: str, request: Request, _: str = Depends(current_user)):
    return agent_call(get_executor(), ["show", name], store=_store(request))


@router.patch("/{name}")
def update_peer(name: str, body: PeerUpdate, request: Request, _: str = Depends(current_user)):
    args = ["update", name]
    if body.tunnel:
        args += ["--tunnel", body.tunnel]
    if body.allowed_ips:
        args += ["--allowed-ips", body.allowed_ips]
    if body.site_routes is not None:
        args += ["--site-routes", body.site_routes]
    if body.ip:
        args += ["--ip", body.ip]
    if body.note is not None:
        args += ["--note", body.note]
    if body.new_name:
        args += ["--new-name", body.new_name]
    if body.endpoint is not None:
        args += ["--peer-endpoint", body.endpoint]
    if body.keepalive is not None:
        args += ["--peer-keepalive", str(body.keepalive)]
    if body.client_keepalive is not None:
        args += ["--client-keepalive", str(body.client_keepalive)]
    if body.enabled is not None:
        args += ["--enabled", str(body.enabled).lower()]
    if len(args) > 2:
        out = agent_call(get_executor(), args, action="peer.update", target=name,
                         store=_store(request))
        _store(request).audit("peer.update", name, "ok", " ".join(args[2:]))
        request.app.state.state_cache.refresh(get_executor())
        return out
    return {"name": name, "enabled": body.enabled}


@router.delete("/{name}")
def delete_peer(name: str, request: Request, confirm: bool = Query(False),
                _: str = Depends(current_user)):
    if not confirm:
        raise HTTPException(status_code=428, detail="删除不可撤销，需要 confirm=true 二次确认")
    out = agent_call(get_executor(), ["remove", name, "--yes"], action="peer.remove",
                     target=name, confirmed=True, store=_store(request))
    _store(request).audit("peer.remove", name, "ok", f"ip={out.get('ip')}")
    request.app.state.state_cache.refresh(get_executor())
    return out


@router.get("/{name}/config")
def peer_config(name: str, request: Request, reveal: bool = Query(False),
                _: str = Depends(current_user)):
    """下载 .conf。默认私钥打码；reveal=true 才给明文并记审计。"""
    data = agent_call(get_executor(), ["show", name] + (["--private"] if reveal else []),
                      store=_store(request))
    text = data.get("conf_text")
    if not text:
        raise HTTPException(status_code=404, detail="节点上没有该客户端的配置文件")
    if reveal:
        _store(request).audit("peer.reveal", name, "ok", "下载了含私钥的配置")
    return PlainTextResponse(
        text,
        headers={"Content-Disposition": f'attachment; filename="{name}.conf"'},
    )


@router.get("/{name}/qr")
def peer_qr(name: str, request: Request, _: str = Depends(current_user)):
    """手机扫码导入用的二维码。内容就是 .conf 全文（含私钥）。"""
    data = agent_call(get_executor(), ["show", name, "--private"], store=_store(request))
    text = data.get("conf_text")
    if not text:
        raise HTTPException(status_code=404, detail="节点上没有该客户端的配置文件")
    try:
        import segno
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"缺少二维码库：{exc}")
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=6, border=2)
    buf.seek(0)
    _store(request).audit("peer.qr", name, "ok", "生成二维码（含私钥）")
    return StreamingResponse(buf, media_type="image/png",
                             headers={"Cache-Control": "no-store, max-age=0"})
