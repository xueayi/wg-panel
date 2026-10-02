"""驱动抽象。

驱动只回答一件事：**怎么执行 wgagent.py**。
WireGuard 的语义（渲染 / syncconf / 备份 / 分配 IP）在 agent 里只有一份实现，
面板不许重写第二份——这是整个设计里最硬的一条边界。
"""

from __future__ import annotations

import json
from typing import Protocol


class ExecutorError(Exception):
    """agent 执行失败。message 里已经过脱敏，可直接回给前端。"""


# 只读命令：WGP_READ_ONLY=1 时仍然放行（用于上线前先验证纳管是否正确）
READ_ONLY_COMMANDS = {
    "list", "status", "show", "ip-pool", "doctor", "backups", "render", "qr",
}

# 破坏性命令：前端必须二次确认
DESTRUCTIVE_COMMANDS = {"remove", "restore", "prune", "adopt"}


def is_write(args: list[str]) -> bool:
    return bool(args) and args[0] not in READ_ONLY_COMMANDS


def parse_agent_output(stdout: str) -> dict:
    """agent 的 stdout 必须是纯 JSON；不是就说明命令用错了。"""
    text = stdout.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExecutorError(f"agent 输出不是合法 JSON：{exc}；原始输出前 200 字符：{text[:200]}")
    return data if isinstance(data, dict) else {"data": data}


class Executor(Protocol):
    def run_agent(self, args: list[str]) -> dict:
        """执行 `wgagent.py <args> --json`，返回解析后的 dict。"""

    def describe(self) -> dict:
        """驱动自身状态，用于面板「连接状态」展示。"""
