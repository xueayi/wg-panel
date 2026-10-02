"""SSH 驱动：纳管云端的中转节点。

要点：
- 支持密钥与密码两种认证（密码只从 600 权限的文件里读，不进 argv、不回显）
- 首次连接自动下发 wgagent.py（sha256 比对，走 stdin 不经 argv）
- 远端输出的 stderr 一律脱敏后再往上抛
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import paramiko

from .base import ExecutorError, parse_agent_output

SECRET_RE = re.compile(r"(PrivateKey|PresharedKey)\s*=\s*\S+", re.I)

KEY_CLASSES = (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey)


def scrub(text: str) -> str:
    """远端报错可能整段回显配置，先把密钥形状的东西抹掉。"""
    return SECRET_RE.sub(r"\1 = <redacted>", text or "")


def load_pkey(path: str):
    p = Path(path).expanduser()
    if not p.exists():
        return None
    for cls in KEY_CLASSES:
        try:
            return cls.from_private_key_file(str(p))
        except Exception:
            continue
    return None


def key_fingerprint(path: str) -> str:
    """OpenSSH 风格指纹（SHA256:…），与 `ssh-keygen -lf 私钥` 显示的一致。"""
    k = load_pkey(path)
    if not k:
        return ""
    return "SHA256:" + base64.b64encode(hashlib.sha256(k.asbytes()).digest()).decode().rstrip("=")


def key_public_line(path: str) -> str:
    """`ssh-ed25519 AAAA…` 形式，方便拿去和服务端 authorized_keys 对账。"""
    k = load_pkey(path)
    if not k:
        return ""
    return f"{k.get_name()} {k.get_base64()}"


class SshExecutor:
    def __init__(self, settings):
        self.s = settings
        self._client: paramiko.SSHClient | None = None

    # ── 连接 ──
    def _pkey(self):
        path = Path(self.s.ssh_key).expanduser()
        if not path.exists():
            raise ExecutorError(f"SSH 私钥不存在：{path}（需只读挂载且权限 600）")
        key = load_pkey(str(path))
        if not key:
            raise ExecutorError(f"无法解析私钥：{path}（支持 ed25519 / rsa / ecdsa）")
        return key

    def _password(self) -> str:
        """密码只从 600 权限的文件读，避免进 argv / 日志 / 数据库明文。"""
        path = Path(getattr(self.s, "ssh_password_file", "") or "").expanduser()
        if not path or not path.exists():
            raise ExecutorError("当前用密码登录，但还没有设置密码（去「连接设置」里填）")
        return path.read_text(encoding="utf-8").rstrip("\n")

    @property
    def auth_mode(self) -> str:
        return (getattr(self.s, "ssh_auth", "key") or "key").lower()

    def connect(self) -> paramiko.SSHClient:
        if self._client:
            return self._client
        cli = paramiko.SSHClient()
        kh = self.s.known_hosts
        if kh and Path(kh).exists():
            cli.load_host_keys(str(kh))
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=self.s.ssh_host,
            port=self.s.ssh_port,
            username=self.s.ssh_user,
            timeout=self.s.ssh_timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        if self.auth_mode == "password":
            kwargs["password"] = self._password()
            kwargs["look_for_keys"] = False
        else:
            kwargs["pkey"] = self._pkey()
        try:
            cli.connect(**kwargs)
        except Exception as exc:
            raise ExecutorError(f"SSH 连接失败（{self.s.ssh_user}@{self.s.ssh_host}:"
                               f"{self.s.ssh_port}，{self.auth_mode} 认证）：{scrub(str(exc))}")
        self._client = cli
        return cli

    def _exec(self, cmd: str, stdin_bytes: bytes | None = None) -> tuple[int, str, str]:
        cli = self.connect()
        try:
            stdin, stdout, stderr = cli.exec_command(cmd, timeout=self.s.ssh_timeout)
            if stdin_bytes is not None:
                stdin.write(stdin_bytes)
            stdin.channel.shutdown_write()
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            return stdout.channel.recv_exit_status(), out, err
        except Exception as exc:
            self._client = None
            raise ExecutorError(f"远程执行失败：{scrub(str(exc))}")

    # ── 下发 agent ──
    def local_agent_bytes(self) -> bytes:
        p = Path(self.s.agent_path)
        if not p.exists():
            raise ExecutorError(f"本地缺少 wgagent.py：{p}")
        return p.read_bytes()

    def deploy(self, force: bool = False) -> bool:
        tool = self.s.ssh_remote_tool
        data = self.local_agent_bytes()
        want = hashlib.sha256(data).hexdigest()
        if not force:
            code, out, _ = self._exec(
                f"sha256sum {tool} 2>/dev/null | cut -d' ' -f1 || true")
            if code == 0 and out.strip() == want:
                return False
        code, out, err = self._exec(
            f"mkdir -p $(dirname {tool}) && cat > {tool} && chmod 700 {tool} "
            f"&& sha256sum {tool} | cut -d' ' -f1",
            stdin_bytes=data,
        )
        got = out.strip().splitlines()[-1] if out.strip() else ""
        if code != 0 or got != want:
            raise ExecutorError(f"下发 wgagent.py 校验失败（远端 {got[:12] or '空'} ≠ 本地 "
                               f"{want[:12]}）：{scrub(err)}")
        return True

    # ── 执行 ──
    def run_agent(self, args: list[str]) -> dict | list:
        argv = ["python3", self.s.ssh_remote_tool, "--iface", self.s.iface]
        argv += [str(a) for a in args]
        if "--json" not in argv:
            argv.append("--json")
        cmd = " ".join(f"'{a}'" for a in argv)
        code, out, err = self._exec(cmd)
        # agent 用退出码表达「体检有问题」（doctor 发现问题时返回 1），不是「执行失败」。
        # 所以只要 stdout 是合法 JSON 就照常消费，退出码只在解析不出结果时才当错误。
        if not out.strip():
            if code == 0:
                return {}
            raise ExecutorError(scrub(err.strip() or f"agent 退出码 {code}（无输出）"))
        try:
            return parse_agent_output(out)
        except ExecutorError:
            if code == 0:
                raise
            raise ExecutorError(scrub(err.strip() or out.strip() or f"agent 退出码 {code}"))

    def describe(self) -> dict:
        return {
            "driver": "ssh",
            "target": f"{self.s.ssh_user}@{self.s.ssh_host}:{self.s.ssh_port}",
            "iface": self.s.iface,
            "remote_tool": self.s.ssh_remote_tool,
            "read_only": self.s.read_only,
        }
