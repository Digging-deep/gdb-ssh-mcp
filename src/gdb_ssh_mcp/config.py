"""Configuration loading and validation for gdb-ssh-mcp.

Loads the YAML config file (plan §9) and applies ``GDBSSH_*`` environment
variables on top, so per-machine values can be inlined via ``claude mcp add``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class SSHConfig:
    """SSH connection settings used to reach the target machine.

    Attributes:
        host: Target machine address.
        user: Login user on the target machine.
        port: SSH port.
        key: Optional path to an SSH private key; ``None`` falls back to the
            system ssh defaults (``~/.ssh/config``, agent, etc.).
        proxy_jump: Optional jump-host argument passed to ``ssh -J``.
        control_persist: ControlMaster connection reuse duration in seconds.

    Examples:
        >>> SSHConfig(host='10.0.0.5', user='wmz', port=2222)
        SSHConfig(host='10.0.0.5', user='wmz', port=2222, key=None, proxy_jump=None, control_persist=3600)
    """

    host: str
    user: str
    port: int = 22
    key: str | None = None
    proxy_jump: str | None = None
    control_persist: int = 3600


@dataclass
class TargetConfig:
    """The program to debug and how gdb should launch it.

    Attributes:
        program: Remote path to the binary being debugged.
        args: Arguments passed to the inferior program.
        cwd: Optional working directory for the inferior.
        gdb: Path to the gdb binary on the target machine.

    Examples:
        >>> TargetConfig(program='/opt/bin/apxd', args=['--config', 'a.conf'])
        TargetConfig(program='/opt/bin/apxd', args=['--config', 'a.conf'], cwd=None, gdb='gdb')
    """

    program: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    gdb: str = 'gdb'


@dataclass
class GDBConfig:
    """Tuning knobs for the interactive gdb session and output handling.

    Attributes:
        tmux_session: Name of the tmux session that hosts gdb.
        max_output_chars: Maximum characters returned from a single capture.
        command_timeout_sec: Seconds to wait for a gdb command to complete.

    Examples:
        >>> GDBConfig()
        GDBConfig(tmux_session='gdb-ssh', max_output_chars=20000, command_timeout_sec=30)
    """

    tmux_session: str = 'gdb-ssh'
    max_output_chars: int = 20000
    command_timeout_sec: int = 30


@dataclass
class Config:
    """Top-level configuration for gdb-ssh-mcp.

    Attributes:
        ssh: Connection settings for the target machine.
        target: The program to debug and its launch arguments.
        gdb: Interactive session and output handling settings.
    """

    ssh: SSHConfig
    target: TargetConfig
    gdb: GDBConfig


def load_config(path: str | Path) -> Config:
    """Load and validate configuration from a YAML file.

    Args:
        path: Path to the config file, e.g.
            ``~/.config/gdb-ssh-mcp/config.yaml``.

    Returns:
        A validated :class:`Config`.

    Raises:
        ConfigError: If the file is missing, is not valid YAML, or fails
            validation.

    Examples:
        For a config file containing::

            ssh:
              host: 10.0.0.5
              user: wmz
            target:
              program: /opt/bin/apxd

        ``load_config(path)`` returns a :class:`Config` whose ``ssh.host`` is
        ``'10.0.0.5'`` and ``target.program`` is ``'/opt/bin/apxd'``.
    """
    path = Path(path).expanduser()
    raw = _read_yaml(path)
    config = _parse(raw)
    _apply_env_overrides(config)
    _validate(config)
    return config


def _read_yaml(path: str | Path) -> dict[str, Any]:
    """Reads and parses the YAML file at *path* into a dict.

    Args:
        path: Path to the YAML file.

    Returns:
        The parsed mapping. An empty file yields ``{}``.

    Raises:
        ConfigError: If the file is missing, invalid YAML, or not a mapping.

    Examples:
        ``_read_yaml(path)`` returns the parsed mapping for a valid YAML file;
        an empty file yields ``{}``. It raises :class:`ConfigError` with a
        message like ``config file not found: <path>`` when the file is
        missing.
    """
    try:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as exc:
        raise ConfigError(f'config file not found: {path}') from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f'invalid YAML in {path}: {exc}') from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f'config root must be a mapping, got {type(data).__name__}'
        )
    return data


def _parse(raw: dict[str, Any]) -> Config:
    """Builds a :class:`Config` from a raw mapping.

    Missing sections become empty dicts and per-field defaults apply; required
    string fields (``ssh.host``, ``ssh.user``, ``target.program``) raise when
    absent or empty.

    Args:
        raw: The mapping returned by :func:`_read_yaml`.

    Returns:
        A :class:`Config` built from *raw*.

    Raises:
        ConfigError: If a required field is missing or a field has the wrong
            type.

    Examples:
        >>> _parse({'ssh': {'host': 'h', 'user': 'u'},
        ...         'target': {'program': '/p'}})
        Config(ssh=SSHConfig(host='h', user='u', port=22, key=None, proxy_jump=None, control_persist=600), target=TargetConfig(program='/p', args=[], cwd=None, gdb='gdb'), gdb=GDBConfig(tmux_session='gdb-ssh', max_output_chars=20000, command_timeout_sec=30))
    """
    ssh = _section(raw, 'ssh')
    target = _section(raw, 'target')
    gdb = _section(raw, 'gdb')
    return Config(
        ssh=SSHConfig(
            host=_required_str(ssh, 'host'),
            user=_required_str(ssh, 'user'),
            port=_optional_int(ssh, 'port', 22),
            key=_optional_str(ssh, 'key'),
            proxy_jump=_optional_str(ssh, 'proxy_jump'),
            control_persist=_optional_int(ssh, 'control_persist', 600),
        ),
        target=TargetConfig(
            program=_required_str(target, 'program'),
            args=_str_list(target, 'args'),
            cwd=_optional_str(target, 'cwd'),
            gdb=_optional_str(target, 'gdb') or 'gdb',
        ),
        gdb=GDBConfig(
            tmux_session=_optional_str(gdb, 'tmux_session') or 'gdb-ssh',
            max_output_chars=_optional_int(gdb, 'max_output_chars', 20000),
            command_timeout_sec=_optional_int(gdb, 'command_timeout_sec', 30),
        ),
    )


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    """Returns the *name* section of *raw*, or ``{}`` when absent.

    Args:
        raw: The top-level config mapping.
        name: Section key, e.g. ``'ssh'``.

    Returns:
        The section mapping.

    Raises:
        ConfigError: If the section exists but is not a mapping.

    Examples:
        >>> _section({'ssh': {'host': 'h'}}, 'ssh')
        {'host': 'h'}
        >>> _section({}, 'ssh')
        {}
    """
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"'{name}' must be a mapping, got {type(value).__name__}"
        )
    return value


def _required_str(section: dict[str, Any], key: str) -> str:
    """Returns *section*[*key*] as a non-empty string, or raises.

    Args:
        section: The section mapping.
        key: The required string key.

    Returns:
        The trimmed-validated string value.

    Raises:
        ConfigError: If the value is missing, empty, or not a string.

    Examples:
        >>> _required_str({'host': '10.0.0.5'}, 'host')
        '10.0.0.5'
    """
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' is required and must be a non-empty string")
    return value


def _optional_str(section: dict[str, Any], key: str) -> str | None:
    """Returns *section*[*key*] as a string, or ``None`` when absent.

    Args:
        section: The section mapping.
        key: The optional string key.

    Returns:
        The string value, or ``None`` if the key is missing.

    Raises:
        ConfigError: If the value is present but not a string.

    Examples:
        >>> _optional_str({'key': 'x'}, 'key')
        'x'
        >>> _optional_str({}, 'key')
    """
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"'{key}' must be a string, got {type(value).__name__}")
    return value


def _optional_int(section: dict[str, Any], key: str, default: int) -> int:
    """Returns *section*[*key*] as an int, falling back to *default*.

    Args:
        section: The section mapping.
        key: The integer key.
        default: Value returned when the key is missing.

    Returns:
        The integer value, coerced with ``int()`` if necessary.

    Raises:
        ConfigError: If the value is present but not an integer.

    Examples:
        >>> _optional_int({'port': '2222'}, 'port', 22)
        2222
        >>> _optional_int({}, 'port', 22)
        22
    """
    value = section.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'{key}' must be an integer, got {value!r}") from exc


def _str_list(section: dict[str, Any], key: str) -> list[str]:
    """Returns *section*[*key*] as a list of strings, or ``[]`` when absent.

    Args:
        section: The section mapping.
        key: The list key.

    Returns:
        A copy of the list value.

    Raises:
        ConfigError: If the value is not a list or contains non-strings.

    Examples:
        >>> _str_list({'args': ['--config', 'a.conf']}, 'args')
        ['--config', 'a.conf']
        >>> _str_list({}, 'args')
        []
    """
    value = section.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"'{key}' must be a list, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"'{key}' items must be strings, got {item!r}")
    return list(value)


def _apply_env_overrides(config: Config) -> None:
    """Apply ``GDBSSH_*`` environment variables over the file values.

    Each supported variable, when set, replaces the corresponding field on
    *config* in place. Unset variables leave the file value untouched.

    Args:
        config: The :class:`Config` to mutate.

    Examples:
        With ``GDBSSH_HOST=10.9.9.9`` and ``GDBSSH_PORT=2222`` in the
        environment, ``_apply_env_overrides(config)`` sets ``config.ssh.host``
        to ``'10.9.9.9'`` and ``config.ssh.port`` to ``2222``.
    """
    if host := os.environ.get('GDBSSH_HOST'):
        config.ssh.host = host
    if user := os.environ.get('GDBSSH_USER'):
        config.ssh.user = user
    if key := os.environ.get('GDBSSH_KEY'):
        config.ssh.key = key
    if proxy_jump := os.environ.get('GDBSSH_PROXY_JUMP'):
        config.ssh.proxy_jump = proxy_jump
    if port := os.environ.get('GDBSSH_PORT'):
        config.ssh.port = _env_int('GDBSSH_PORT', port)
    if program := os.environ.get('GDBSSH_PROGRAM'):
        config.target.program = program
    if tmux_session := os.environ.get('GDBSSH_TMUX_SESSION'):
        config.gdb.tmux_session = tmux_session


def _env_int(name: str, value: str) -> int:
    """Parses an environment variable as an integer.

    Args:
        name: The environment variable name, used in the error message.
        value: The string value to parse.

    Returns:
        The parsed integer.

    Raises:
        ConfigError: If *value* is not an integer.

    Examples:
        >>> _env_int('GDBSSH_PORT', '2222')
        2222
    """
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f'{name} must be an integer, got {value!r}') from exc


def _validate(config: Config) -> None:
    """Checks range constraints on *config* and raises on violation.

    Args:
        config: The :class:`Config` to validate.

    Raises:
        ConfigError: If any port, timeout, or output-size value is out of its
            valid range.

    Examples:
        >>> _validate(Config(ssh=SSHConfig(host='h', user='u', port=22),
        ...                  target=TargetConfig(program='/p'),
        ...                  gdb=GDBConfig()))
    """
    if not 1 <= config.ssh.port <= 65535:
        raise ConfigError(
            f'ssh.port must be in [1, 65535], got {config.ssh.port}'
        )
    if config.ssh.control_persist <= 0:
        raise ConfigError('ssh.control_persist must be positive')
    if config.gdb.max_output_chars <= 0:
        raise ConfigError('gdb.max_output_chars must be positive')
    if config.gdb.command_timeout_sec <= 0:
        raise ConfigError('gdb.command_timeout_sec must be positive')
