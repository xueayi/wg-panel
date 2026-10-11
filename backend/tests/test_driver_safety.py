"""执行器保护协议：参数无 shell 注入，非零写退出不能作为成功。"""
import shlex
from pathlib import Path

import pytest


def test_ssh_quotes_every_agent_argument(tmp_path):
    from app.config import Settings
    from app.drivers.ssh import SshExecutor
    cfg = Settings()
    cfg.ssh_remote_tool = "/tmp/a'b agent.py"
    executor = SshExecutor(cfg)
    captured = []
    executor._exec = lambda cmd: (captured.append(cmd) or (0, '{}', ''))
    args = ['update', 'laptop', '--note', "it's $(touch /tmp/not-created); literal"]
    executor.run_agent(args)
    assert shlex.split(captured[0]) == ['python3', cfg.ssh_remote_tool, '--iface', cfg.iface, *args, '--json']


def test_ssh_nonzero_write_json_and_parse_errors_are_redacted():
    from app.config import Settings
    from app.drivers.ssh import SshExecutor
    from app.drivers.base import ExecutorError
    executor = SshExecutor(Settings())
    executor._exec = lambda cmd: (4, '{}', 'PrivateKey = secret')
    with pytest.raises(ExecutorError) as exc:
        executor.run_agent(['apply'])
    assert 'secret' not in str(exc.value)
    executor._exec = lambda cmd: (0, 'PrivateKey = secret', '')
    with pytest.raises(ExecutorError) as exc:
        executor.run_agent(['list'])
    assert 'secret' not in str(exc.value)


def test_executor_connection_settings_are_immutable_after_creation():
    from app.config import Settings
    from app.drivers.ssh import SshExecutor
    from app.drivers.local import LocalExecutor
    cfg = Settings()
    cfg.ssh_host = 'node-a'
    cfg.local_wg_dir = Path('/config-a')
    ssh, local = SshExecutor(cfg), LocalExecutor(cfg)
    cfg.ssh_host = 'node-b'
    cfg.local_wg_dir = Path('/config-b')
    assert 'node-a' in ssh.describe()['target']
    assert '/config-a' in local.describe()['target']
