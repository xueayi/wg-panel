"""本地 WireGuard 驱动。

面板与 WireGuard 运行在同一个容器网络空间时，直接以受控参数调用 agent。
不访问 Docker socket，WireGuard 的配置语义仍只存在于 agent 中。
"""

from __future__ import annotations

import copy

import os
import subprocess
import sys
from pathlib import Path

from .base import ExecutorError, parse_agent_output
from .ssh import scrub


class LocalExecutor:
    def __init__(self, settings):
        self.s = copy.copy(settings)
        self._env = os.environ.copy()
        self._env.update({
            "WG_DIR": str(settings.local_wg_dir),
            "WG_BASE": str(settings.local_state_dir),
            "WG_IFACE": str(settings.iface),
        })

    def deploy(self, force: bool = False) -> bool:
        """本地模式不需要下发 agent；返回 False 保持 refresh API 兼容。"""
        return False

    def run_agent(self, args: list[str]) -> dict | list:
        if not args:
            raise ExecutorError("空命令")
        agent = Path(self.s.agent_path)
        if not agent.exists():
            raise ExecutorError(f"本地缺少 wgagent.py：{agent}")
        argv = [sys.executable, str(agent), "--iface", str(self.s.iface), *map(str, args)]
        if "--json" not in argv:
            argv.append("--json")
        try:
            proc = subprocess.run(
                argv,
                env=self._env,
                capture_output=True,
                text=True,
                timeout=max(1, int(self.s.local_timeout)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError(f"本地 agent 超时（{self.s.local_timeout}s）") from exc
        if proc.returncode != 0 and not (args[0] == "doctor" and proc.returncode == 1):
            raise ExecutorError(scrub(proc.stderr.strip() or f"agent 退出码 {proc.returncode}"))
        stdout = proc.stdout.strip()
        if not stdout:
            raise ExecutorError(scrub(proc.stderr.strip()) or f"agent 退出码 {proc.returncode}")
        try:
            return parse_agent_output(stdout)
        except ExecutorError as exc:
            raise ExecutorError(scrub(proc.stderr.strip() or str(exc))) from exc

    def describe(self) -> dict:
        return {
            "driver": "local",
            "target": f"本地 {self.s.local_wg_dir}",
            "iface": self.s.iface,
            "agent": self.s.agent_path,
            "read_only": self.s.read_only,
        }
