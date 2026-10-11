from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.drivers.local import LocalExecutor


def test_local_executor_runs_agent_without_docker_socket(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json, os; print(json.dumps({'env': os.environ['WG_DIR'], 'ok': True}))\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config"
    state = tmp_path / "state"
    cfg.mkdir()
    state.mkdir()
    settings.agent_path = str(agent)
    settings.local_wg_dir = cfg
    settings.local_state_dir = state
    settings.iface = "wg0"
    settings.local_timeout = 2
    out = LocalExecutor(settings).run_agent(["metrics"])
    assert out == {"env": str(cfg), "ok": True}


def test_local_executor_nonzero_json_is_failure_and_stderr_is_redacted(tmp_path):
    from app.config import Settings
    from app.drivers.local import LocalExecutor
    from app.drivers.base import ExecutorError
    import pytest
    agent = tmp_path / 'error.py'
    agent.write_text("import sys; print('{}'); print('PrivateKey = secret PresharedKey = psk', file=sys.stderr); sys.exit(4)")
    cfg = Settings()
    cfg.agent_path = str(agent)
    with pytest.raises(ExecutorError) as err:
        LocalExecutor(cfg).run_agent(['apply'])
    assert 'secret' not in str(err.value) and 'psk' not in str(err.value)


def test_local_doctor_allows_structured_exit_one(tmp_path):
    from app.config import Settings
    from app.drivers.local import LocalExecutor
    agent = tmp_path / 'doctor.py'
    agent.write_text("import sys; print('{\"problems\": [\"missing\"]}'); sys.exit(1)")
    cfg = Settings()
    cfg.agent_path = str(agent)
    assert LocalExecutor(cfg).run_agent(['doctor']) == {'problems': ['missing']}
