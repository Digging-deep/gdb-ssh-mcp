"""Unit tests for gdb_ssh_mcp.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdb_ssh_mcp.config import ConfigError, load_config


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / 'config.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
ssh:
  host: 10.0.0.5
  user: wmz
  port: 22
target:
  program: /opt/apexdb
  args: ["--config", "xx.conf"]
gdb:
  tmux_session: gdb-ssh
""",
    )
    config = load_config(path)
    assert config.ssh.host == '10.0.0.5'
    assert config.ssh.user == 'wmz'
    assert config.ssh.port == 22
    assert config.target.program == '/opt/apexdb'
    assert config.target.args == ['--config', 'xx.conf']
    assert config.gdb.tmux_session == 'gdb-ssh'


def test_defaults_applied(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
ssh:
  host: h
  user: u
target:
  program: p
""",
    )
    config = load_config(path)
    assert config.ssh.port == 22
    assert config.ssh.key is None
    assert config.ssh.proxy_jump is None
    assert config.ssh.control_persist == 600
    assert config.target.args == []
    assert config.target.cwd is None
    assert config.target.gdb == 'gdb'
    assert config.gdb.tmux_session == 'gdb-ssh'
    assert config.gdb.max_output_chars == 20000
    assert config.gdb.command_timeout_sec == 30


def test_missing_required_field(tmp_path: Path) -> None:
    path = _write_config(tmp_path, 'ssh:\n  host: h\n')
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / 'nope.yaml')


def test_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_config(
        tmp_path,
        """
ssh:
  host: file-host
  user: file-user
target:
  program: file-prog
""",
    )
    monkeypatch.setenv('GDBSSH_HOST', 'env-host')
    monkeypatch.setenv('GDBSSH_PORT', '2200')
    monkeypatch.setenv('GDBSSH_PROGRAM', 'env-prog')
    config = load_config(path)
    assert config.ssh.host == 'env-host'
    assert config.ssh.port == 2200
    assert config.target.program == 'env-prog'


def test_invalid_port(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
ssh:
  host: h
  user: u
  port: 70000
target:
  program: p
""",
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_root_not_mapping(tmp_path: Path) -> None:
    path = _write_config(tmp_path, '- a\n- b\n')
    with pytest.raises(ConfigError):
        load_config(path)
