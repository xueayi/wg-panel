"""系统面：连接状态、体检、备份回滚、审计日志。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..config import settings
from ..drivers.base import ExecutorError
from .deps import agent_call, get_executor, reset_executor

router = APIRouter(prefix="/api", tags=["system"])


def _store(request: Request):
    return request.app.state.store


def current_user_dep(request: Request) -> str:
    from .deps import current_user
    return current_user(request)


@router.get("/health")
def health():
    """未认证也可访问：给容器编排做存活探测。"""
    return {"ok": True, "driver": settings.driver, "read_only": settings.read_only}


@router.get("/status")
def status(request: Request, _: str = Depends(current_user_dep)):
    """面板 ↔ 中转节点的连接状态，含启动自检发现的问题。"""
    executor = get_executor()
    info = {"driver": executor.describe(), "problems": settings.validate(), "connected": False}
    try:
        data = agent_call(executor, ["status"], store=_store(request))
        info["connected"] = True
        rows = data.get("rows", [])
        info["peers"] = len(rows)
        info["online"] = sum(1 for r in rows if r.get("state") == "在线")
    except ExecutorError as exc:
        info["error"] = str(exc)
    except HTTPException as exc:
        info["error"] = exc.detail
    return info


@router.post("/connect/refresh")
def refresh(request: Request, _: str = Depends(current_user_dep)):
    """重连 + 强制重新下发 wgagent.py（升级面板后用它同步内核）。"""
    reset_executor()
    executor = get_executor()
    deployed = executor.deploy(force=True) if hasattr(executor, "deploy") else False
    _store(request).audit("system.refresh", "", "ok", f"redeployed={deployed}")
    return {"reconnected": True, "agent_redeployed": deployed, "driver": executor.describe()}


@router.get("/doctor")
def doctor(request: Request, _: str = Depends(current_user_dep)):
    return agent_call(get_executor(), ["doctor"], store=_store(request))


@router.get("/backups")
def backups(request: Request, _: str = Depends(current_user_dep)):
    data = agent_call(get_executor(), ["backups"], store=_store(request))
    # 契约固定为数组：前端按数组渲染，绝不能回一个包了一层的对象
    if isinstance(data, dict):
        return data.get("data", [])
    return data


@router.post("/backups/{name}/restore")
def restore(name: str, request: Request, confirm: bool = Query(False),
            _: str = Depends(current_user_dep)):
    if not confirm:
        raise HTTPException(status_code=428, detail="回滚会覆盖现网配置，需要 confirm=true")
    args = ["restore", name, "--yes"]
    out = agent_call(get_executor(), args, action="system.restore", target=name,
                     confirmed=True, store=_store(request))
    _store(request).audit("system.restore", name, "ok", "回滚配置")
    return {"restored": name, **out} if isinstance(out, dict) else {"restored": name}


@router.post("/adopt")
def adopt(request: Request, confirm: bool = Query(False), _: str = Depends(current_user_dep)):
    """首次纳管既有 wg0：把现网 peer 收进登记表，并按登记表重渲染配置。

    写前自动备份，SaveConfig 一并改成 false。幂等，可重复执行。
    """
    if not confirm:
        raise HTTPException(status_code=428,
                            detail="纳管会重写 wg0.conf（先自动备份），需要 confirm=true")
    out = agent_call(get_executor(), ["adopt", "--yes"], action="system.adopt",
                     confirmed=True, store=_store(request))
    _store(request).audit("system.adopt", "", "ok", "纳管既有配置")
    return out


@router.get("/audit")
def audit(request: Request, limit: int = Query(100, ge=1, le=500),
          _: str = Depends(current_user_dep)):
    return {"rows": _store(request).list_audit(limit)}
