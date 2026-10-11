"""登录：密码 → HttpOnly Cookie 会话。不引入用户体系，单管理员足够。"""

from __future__ import annotations

import secrets
import time
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import settings
from ..security import create_token, hash_password, verify_password
from .deps import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "wgp_session"
_failures: dict[str, list[float]] = {}
_failure_lock = threading.Lock()
_MAX_FAILURES = 10
_LOCKOUT_SECONDS = 300


class LoginBody(BaseModel):
    username: str = "admin"
    password: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str
    force: Optional[bool] = False


class AccountBody(BaseModel):
    old_password: str
    username: Optional[str] = None
    new_password: Optional[str] = None


def _store(request: Request):
    return request.app.state.store


def _must_change(request: Request) -> bool:
    return _store(request).get_settings().get("must_change_password") == "1"


@router.get("/state")
def auth_state(request: Request):
    """未登录也要能问：面板初始化了没有、是不是还在用初始密码。"""
    store = _store(request)
    return {
        "initialized": store.get_admin() is not None,
        "must_change": store.get_settings().get("must_change_password") == "1",
        "default_hint": "初始账号是 admin / admin，登录后请立刻修改",
    }


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    store = _store(request)
    admin = store.get_admin()
    if not admin:
        raise HTTPException(status_code=409,
                            detail="面板尚未初始化：请设置 WGP_ADMIN_PASSWORD 后重启容器")
    username, stored_hash = admin
    client_key = request.client.host if request.client else "unknown"
    now = time.time()
    with _failure_lock:
        for key in list(_failures):
            recent = [ts for ts in _failures[key] if now - ts < _LOCKOUT_SECONDS]
            if recent:
                _failures[key] = recent
            else:
                _failures.pop(key, None)
        recent = _failures.get(client_key, [])
        if len(recent) >= _MAX_FAILURES:
            retry = max(1, int(_LOCKOUT_SECONDS - (now - recent[0])))
            raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试",
                                headers={"Retry-After": str(retry)})
        if body.username != username or not verify_password(body.password, stored_hash):
            if client_key not in _failures and len(_failures) >= 4096:
                raise HTTPException(status_code=429, detail="登录请求过多，请稍后再试")
            recent.append(now)
            _failures[client_key] = recent
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        _failures.pop(client_key, None)
    token = create_token(request.app.state.secret, username, settings.session_hours)
    response.set_cookie(
        COOKIE, token,
        httponly=True, samesite="lax", secure=False,
        max_age=settings.session_hours * 3600, path="/",
    )
    store.audit("auth.login", username, "ok", "")
    return {"ok": True, "username": username}


@router.post("/logout")
def logout(request: Request, response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"username": current_user(request), "must_change": _must_change(request)}


@router.post("/password")
def change_password(body: PasswordBody, request: Request):
    store = _store(request)
    if not current_user(request):
        raise HTTPException(status_code=401, detail="未登录")
    admin = store.get_admin()
    if admin and not body.force and not verify_password(body.old_password, admin[1]):
        raise HTTPException(status_code=400, detail="原密码不对")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    store.set_admin(admin[0] if admin else "admin", hash_password(body.new_password))
    store.put_settings({"must_change_password": "0"})
    store.audit("auth.password", admin[0] if admin else "admin", "ok", "修改密码")
    return {"ok": True}


@router.post("/account")
def update_account(body: AccountBody, request: Request):
    """改登录用户名与/或密码。改完用户名需要重新登录。"""
    store = _store(request)
    admin = store.get_admin()
    if not current_user(request) or not admin:
        raise HTTPException(status_code=401, detail="未登录")
    if not verify_password(body.old_password, admin[1]):
        raise HTTPException(status_code=400, detail="当前密码不对")

    name = (body.username or admin[0]).strip()
    if not 1 <= len(name) <= 32:
        raise HTTPException(status_code=400, detail="用户名长度需在 1–32 之间")
    pw_hash = admin[1]
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(status_code=400, detail="新密码至少 8 位")
        pw_hash = hash_password(body.new_password)
    if name == admin[0] and pw_hash == admin[1]:
        return {"ok": True, "changed": False, "username": name}

    store.set_admin(name, pw_hash)
    store.put_settings({"must_change_password": "0"})
    store.audit("auth.account", name, "ok",
                f"用户名 {admin[0]} → {name}；密码{'已更新' if body.new_password else '未变'}")
    return {"ok": True, "changed": True, "username": name,
            "relogin": name != admin[0]}
