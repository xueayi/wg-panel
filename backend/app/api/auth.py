"""登录：口令 → HttpOnly Cookie 会话。不引入用户体系，单管理员足够。"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import settings
from ..security import create_token, hash_password, verify_password
from .deps import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "wgp_session"


class LoginBody(BaseModel):
    username: str = "admin"
    password: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str
    force: Optional[bool] = False


def _store(request: Request):
    return request.app.state.store


@router.get("/state")
def auth_state(request: Request):
    """未登录也要能问：面板到底初始化了没有、要不要先设口令。"""
    return {"initialized": _store(request).get_admin() is not None}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    store = _store(request)
    admin = store.get_admin()
    if not admin:
        raise HTTPException(status_code=409,
                            detail="面板尚未初始化：请设置 WGP_ADMIN_PASSWORD 后重启容器")
    username, stored_hash = admin
    if body.username != username or not verify_password(body.password, stored_hash):
        raise HTTPException(status_code=401, detail="用户名或口令错误")
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
    return {"username": current_user(request)}


@router.post("/password")
def change_password(body: PasswordBody, request: Request):
    store = _store(request)
    if not current_user(request):
        raise HTTPException(status_code=401, detail="未登录")
    admin = store.get_admin()
    if admin and not body.force and not verify_password(body.old_password, admin[1]):
        raise HTTPException(status_code=400, detail="原口令不对")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新口令至少 8 位")
    store.set_admin(admin[0] if admin else "admin", hash_password(body.new_password))
    store.audit("auth.password", admin[0] if admin else "admin", "ok", "修改口令")
    return {"ok": True}
