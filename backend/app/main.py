"""wg-panel 后端入口。

职责边界：这里只做「校验 + 编排 + 审计」，WireGuard 语义全在 agent/wgagent.py。
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import auth, peers, server, system
from .api import settings as settings_api
from .config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USER, settings
from .drivers.base import ExecutorError
from .security import hash_password
from .services.store import Store

log = logging.getLogger("wgpanel")
APP_ROOT = Path(__file__).resolve().parents[2]


def create_app() -> FastAPI:
    app = FastAPI(title="wg-panel", version="0.1.0", docs_url="/api/docs")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.store = Store(settings.data_dir / "panel.db")
    app.state.secret = settings.secret_key or secrets.token_hex(32)

    # 首次启动：把 env 里注入的密码变成哈希存进 SQLite，之后 env 不再需要
    if not app.state.store.get_admin():
        pw = settings.admin_password or DEFAULT_ADMIN_PASSWORD
        app.state.store.set_admin(DEFAULT_ADMIN_USER, hash_password(pw))
        if pw == DEFAULT_ADMIN_PASSWORD:
            app.state.store.put_settings({"must_change_password": "1"})
            log.warning("面板用默认账号 %s/%s 初始化，登录后请立即在面板里改掉",
                        DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASSWORD)
        else:
            log.info("已用 WGP_ADMIN_PASSWORD 初始化管理员账号 %s", DEFAULT_ADMIN_USER)
        log.info("已用 WGP_ADMIN_PASSWORD 初始化管理员密码")

    # 面板里改过的连接设置覆盖 env 默认值（换节点、改端口不用动 compose）
    overrides = app.state.store.get_settings()
    if overrides:
        parsed: dict = {}
        for k, v in overrides.items():
            if k in ("ssh_port", "ssh_timeout"):
                try:
                    parsed[k] = int(v)
                except ValueError:
                    continue
            elif k == "read_only":
                parsed[k] = str(v).lower() in ("1", "true", "yes")
            else:
                parsed[k] = v
        settings.apply(parsed)
        log.info("已载入面板里的连接设置：%s", ", ".join(f"{k}={v}" for k, v in parsed.items()))

    app.include_router(auth.router)
    app.include_router(peers.router)
    app.include_router(server.router)
    app.include_router(settings_api.router)
    app.include_router(system.router)

    @app.on_event("startup")
    def _startup() -> None:
        problems = settings.validate()
        for p in problems:
            log.warning("配置自检：%s", p)
        if settings.driver == "ssh":
            try:
                from .drivers.ssh import SshExecutor
                SshExecutor(settings).deploy()
                log.info("wgagent.py 已下发到 %s", settings.ssh_host or "（未配置）")
            except ExecutorError as exc:
                log.warning("启动期下发 agent 失败：%s", exc)

    # 容器里目录布局不同，静态资源目录可用 WGP_STATIC_DIR 覆盖
    dist = Path(os.environ.get("WGP_STATIC_DIR") or (APP_ROOT / "frontend" / "dist"))
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    else:
        log.warning("未找到前端构建产物：%s（仅 API 可用）", dist)

    return app


app = create_app()
