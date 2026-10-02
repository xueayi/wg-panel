"""配置：全部从环境变量读，代码里零明文。

镜像里不存任何凭证：SSH 私钥靠只读挂载，管理员口令靠 env 注入或从 Vaultwarden 取。
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings:
    def __init__(self) -> None:
        # 驱动：ssh（生产，纳管云端中转节点） / mock（本地开发与前端联调）
        self.driver: str = _env("WGP_DRIVER", "mock").lower()
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

        # 面板自身
        self.data_dir: Path = Path(_env("WGP_DATA_DIR", "./data"))
        self.admin_password: str = _env("WGP_ADMIN_PASSWORD", "")
        self.secret_key: str = _env("WGP_SECRET_KEY", "")
        self.session_hours: int = int(_env("WGP_SESSION_HOURS", "12") or 12)

    def validate(self) -> list[str]:
        """启动自检：只报告问题，不抛异常（面板要能起来告诉你哪里错了）。"""
        problems = []
        if self.driver == "ssh" and not self.ssh_host:
            problems.append("WGP_DRIVER=ssh 但没给 WGP_SSH_HOST")
        if self.driver == "ssh" and not Path(self.ssh_key).exists():
            problems.append(f"SSH 私钥不存在：{self.ssh_key}（应只读挂载，权限 600）")
        if not Path(self.agent_path).exists():
            problems.append(f"找不到 wgagent.py：{self.agent_path}")
        if not self.secret_key:
            problems.append("未设 WGP_SECRET_KEY，将使用临时密钥（重启后需重新登录）")
        return problems


settings = Settings()
