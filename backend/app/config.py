"""配置：环境变量只提供「初始值」，运行时以面板里保存的连接设置为准。

镜像里不存任何凭证：SSH 私钥/密码由用户在面板里上传（落盘 600），或只读挂载进来。
只有初始管理员密码与会话密钥来自 env。
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 首次启动、且没有提供 WGP_ADMIN_PASSWORD 时用的初始密码（登录后应立刻改掉）
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


class Settings:
    def __init__(self) -> None:
        # 驱动：ssh（生产，纳管云端中转节点） / mock（本地开发与前端联调）
        self.driver: str = _env("WGP_DRIVER", "ssh").lower()
        self.read_only: bool = _env("WGP_READ_ONLY", "0") == "1"

        self.iface: str = _env("WGP_IFACE", "wg0")
        self.agent_path: str = _env("WGP_AGENT", str(PROJECT_ROOT / "agent" / "wgagent.py"))

        # SSH 驱动
        self.ssh_host: str = _env("WGP_SSH_HOST")
        self.ssh_user: str = _env("WGP_SSH_USER", "root")
        self.ssh_port: int = int(_env("WGP_SSH_PORT", "22") or 22)
        self.ssh_key: str = _env("WGP_SSH_KEY", "/ssh/id_ed25519")
        self.ssh_remote_tool: str = _env("WGP_SSH_REMOTE_TOOL", "/root/wireguard/wgagent.py")
        self.ssh_timeout: int = int(_env("WGP_SSH_TIMEOUT", "20") or 20)
        self.known_hosts: str = _env("WGP_KNOWN_HOSTS", "")
        # 认证方式：key（默认，推荐） / password
        self.ssh_auth: str = (_env("WGP_SSH_AUTH", "key") or "key").lower()

        # 面板自身
        self.data_dir: Path = Path(_env("WGP_DATA_DIR", "./data"))
        # 密码只从 600 权限的文件读，不进 DB 明文
        self.ssh_password_file: str = _env(
            "WGP_SSH_PASSWORD_FILE", str(self.data_dir / "keys" / "ssh_password"))
        self.admin_password: str = _env("WGP_ADMIN_PASSWORD", "")
        self.secret_key: str = _env("WGP_SECRET_KEY", "")
        self.session_hours: int = int(_env("WGP_SESSION_HOURS", "12") or 12)

    # 面板上可改的字段（白名单，避免把任意属性暴露成可写）
    TUNABLE = {
        "driver", "read_only", "iface", "ssh_host", "ssh_user", "ssh_port",
        "ssh_key", "ssh_remote_tool", "ssh_timeout", "known_hosts", "agent_path",
        "ssh_auth", "ssh_password_file",
    }

    def apply(self, data: dict) -> None:
        """用面板里存下来的值覆盖 env 默认值（类型已在 API 层转好）。"""
        for k, v in (data or {}).items():
            if k in self.TUNABLE and v is not None:
                setattr(self, k, v)

    def snapshot(self) -> dict:
        return {k: getattr(self, k) for k in sorted(self.TUNABLE)}

    def validate(self) -> list[str]:
        """启动自检：只报告问题，不抛异常（面板要能起来告诉你哪里错了）。"""
        problems = []
        if self.driver == "ssh" and not self.ssh_host:
            problems.append("还没配置中转节点地址——去「连接设置」填上服务地址（Endpoint）")
        using_password = self.ssh_auth == "password"
        if self.driver == "ssh" and not using_password and not Path(self.ssh_key).exists():
            problems.append(f"SSH 私钥不存在：{self.ssh_key}（只读挂载，或在「连接设置」里上传）")
        if self.driver == "ssh" and using_password and not Path(self.ssh_password_file).exists():
            problems.append("选了密码登录但还没设置密码——去「连接设置」里填")
        if not Path(self.agent_path).exists():
            problems.append(f"找不到 wgagent.py：{self.agent_path}")
        if not self.secret_key:
            problems.append("未设 WGP_SECRET_KEY，将使用临时密钥（重启后需重新登录）")
        return problems


settings = Settings()
