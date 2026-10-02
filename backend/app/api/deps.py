"""路由依赖：驱动单例、登录校验、写操作护栏。"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..config import settings
from ..drivers.base import DESTRUCTIVE_COMMANDS, ExecutorError, is_write
from ..drivers.mock import MockExecutor
from ..drivers.ssh import SshExecutor

_executor = None


def build_executor():
    if settings.driver == "ssh":
        return SshExecutor(settings)
    return MockExecutor(settings)


def get_executor():
    """驱动单例：SSH 连接复用，避免每次请求都握手。"""
    global _executor
    if _executor is None:
        _executor = build_executor()
    return _executor


def reset_executor() -> None:
    global _executor
    _executor = None


def current_user(request: Request) -> str:
    from ..security import decode_token

    token = request.cookies.get("wgp_session", "")
    payload = decode_token(request.app.state.secret, token)
    if not payload:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return payload.get("sub", "")


def guard_write(args: list[str], confirmed: bool = False) -> None:
    """只读模式下拦写操作；破坏性命令要求显式确认。"""
    if not is_write(args):
        return
    if settings.read_only:
        raise HTTPException(status_code=423,
                            detail="面板处于只读模式（WGP_READ_ONLY=1），不执行任何写操作")
    if args and args[0] in DESTRUCTIVE_COMMANDS and not confirmed:
        raise HTTPException(status_code=428,
                            detail=f"{args[0]} 是破坏性操作，需要显式确认（confirm=true）")


def agent_call(executor, args: list[str], *, action: str = "", target: str = "",
               confirmed: bool = False, store=None):
    """统一入口：护栏 → 执行 → 审计 → 错误归类。"""
    guard_write(args, confirmed)
    try:
        return executor.run_agent(args)
    except ExecutorError as exc:
        if store and action:
            store.audit(action, target, "failed", str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
