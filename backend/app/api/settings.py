"""节点连接设置：把「中转节点地址 / SSH 端口 / 私钥 / 接口名」搬到 Web 上。

env 变量仍然是初始来源，但在面板里改过的值会写进 SQLite 并覆盖 env——
这样换节点、改端口不用再去 NAS 上编辑 compose 文件。

私钥只进不出：上传后落盘 600，接口只回路径与指纹，永不回正文。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import settings
from ..drivers.base import ExecutorError
from .deps import current_user, get_executor, reset_executor

router = APIRouter(prefix="/api/settings", tags=["settings"])

KEY_DIR_NAME = "keys"


def _store(request: Request):
    return request.app.state.store


def _key_path(request: Request) -> Path:
    return Path(settings.data_dir) / KEY_DIR_NAME / "id_ed25519"


def _fingerprint(path: str) -> str:
    """只回指纹，不回私钥内容。"""
    p = Path(path)
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


class ConnectionPatch(BaseModel):
    ssh_host: Optional[str] = Field(default=None, max_length=255)
    ssh_port: Optional[int] = Field(default=None, ge=1, le=65535)
    ssh_user: Optional[str] = Field(default=None, max_length=64)
    ssh_key: Optional[str] = Field(default=None, max_length=512)
    ssh_remote_tool: Optional[str] = Field(default=None, max_length=512)
    iface: Optional[str] = Field(default=None, max_length=32)
    read_only: Optional[bool] = None


class KeyUpload(BaseModel):
    private_key: str = Field(min_length=40)


@router.get("")
def get_settings(request: Request, _: str = Depends(current_user)):
    key_path = settings.ssh_key
    return {
        "driver": settings.driver,
        "read_only": settings.read_only,
        "iface": settings.iface,
        "ssh_host": settings.ssh_host,
        "ssh_port": settings.ssh_port,
        "ssh_user": settings.ssh_user,
        "ssh_remote_tool": settings.ssh_remote_tool,
        "agent_path": settings.agent_path,
        "key": {
            "path": key_path,
            "uploaded": bool(_fingerprint(key_path)),
            "fingerprint": _fingerprint(key_path),
            "managed": str(_key_path(request)) == key_path,
        },
        "problems": settings.validate(),
        "env_defaults_hint": "改过的值存在面板数据库里，会覆盖容器环境变量",
    }


@router.patch("")
def patch_settings(body: ConnectionPatch, request: Request, _: str = Depends(current_user)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not data:
        return {"changed": False}
    if "ssh_host" in data:
        data["ssh_host"] = data["ssh_host"].strip()
    _store(request).put_settings(data)
    settings.apply(data)
    reset_executor()
    _store(request).audit("settings.update", "", "ok",
                          ", ".join(f"{k}={v}" for k, v in data.items() if k != "ssh_key"))
    return {"changed": True, **{k: v for k, v in data.items() if k != "ssh_key"}}


@router.post("/ssh-key")
def upload_key(body: KeyUpload, request: Request, _: str = Depends(current_user)):
    """上传 SSH 私钥。内容只落盘（600），接口不回显。"""
    text = body.private_key.strip()
    if "BEGIN" not in text or "PRIVATE KEY" not in text:
        raise HTTPException(status_code=400, detail="这不像一个 OpenSSH 私钥（缺少 BEGIN/PRIVATE KEY 头）")
    dst = _key_path(request)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dst.parent, 0o700)
    dst.write_text(text + "\n", encoding="utf-8")
    os.chmod(dst, 0o600)
    _store(request).put_settings({"ssh_key": str(dst)})
    settings.apply({"ssh_key": str(dst)})
    reset_executor()
    _store(request).audit("settings.ssh_key", str(dst), "ok", f"指纹 {_fingerprint(str(dst))}")
    return {"uploaded": True, "path": str(dst), "fingerprint": _fingerprint(str(dst))}


@router.post("/test")
def test_connection(request: Request, _: str = Depends(current_user)):
    """真连一次：建立 SSH、下发内核脚本、跑一次 status。"""
    reset_executor()
    executor = get_executor()
    out: dict = {"ok": False, "driver": executor.describe()}
    try:
        if hasattr(executor, "deploy"):
            out["agent_redeployed"] = executor.deploy()
        data = executor.run_agent(["status"])
        out["ok"] = True
        out["peers"] = len(data.get("rows", [])) if isinstance(data, dict) else 0
        out["online"] = sum(1 for r in (data.get("rows", []) if isinstance(data, dict) else [])
                            if r.get("state") == "在线")
    except ExecutorError as exc:
        out["error"] = str(exc)
    _store(request).audit("settings.test", "", "ok" if out["ok"] else "failed",
                          out.get("error", ""))
    return out
