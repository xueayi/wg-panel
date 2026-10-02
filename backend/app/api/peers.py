"""peer 管理：虚拟 IP 分发 + 客户端配置导入三件套（二维码 / 下载 / 复制）。"""

from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..drivers.base import ExecutorError
from .deps import agent_call, current_user, get_executor

router = APIRouter(prefix="/api/peers", tags=["peers"])


class PeerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    tunnel: str = Field(default="lan", pattern="^(lan|full|custom)$")
    ip: Optional[str] = None
    allowed_ips: Optional[str] = None
    site_routes: Optional[str] = None      # 此节点背后的局域网网段（站点互联）
    net: Optional[str] = None              # 从哪个虚拟网段分配地址（多网段时用）
    dns: Optional[str] = None
    mtu: Optional[int] = None
    note: str = ""


class PeerUpdate(BaseModel):
    tunnel: Optional[str] = None
    allowed_ips: Optional[str] = None
    site_routes: Optional[str] = None
    ip: Optional[str] = None
    note: Optional[str] = None
    enabled: Optional[bool] = None


def _store(request: Request):
    return request.app.state.store


@router.get("")
def list_peers(request: Request, _: str = Depends(current_user)):
    data = agent_call(get_executor(), ["list"], store=_store(request))
    rows = data.get("rows", [])
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
    if body.note:
        args += ["--note", body.note]
    out = agent_call(get_executor(), args, action="peer.add", target=body.name,
                     store=_store(request))
    _store(request).audit("peer.add", body.name, "ok",
                          f"ip={out.get('ip')} tunnel={body.tunnel}")
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
    if body.enabled is False:
        agent_call(get_executor(), ["disable", name], action="peer.disable", target=name,
                   store=_store(request))
    elif body.enabled is True:
        agent_call(get_executor(), ["enable", name], action="peer.enable", target=name,
                   store=_store(request))
    if len(args) > 2:
        out = agent_call(get_executor(), args, action="peer.update", target=name,
                         store=_store(request))
        _store(request).audit("peer.update", name, "ok", " ".join(args[2:]))
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
