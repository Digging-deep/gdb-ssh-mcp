"""Thin wrapper around the system OpenSSH client.

Runs remote commands via ``subprocess``, reusing the user's existing
``~/.ssh/config`` (keys, ProxyJump, known_hosts) and enabling ControlMaster
connection multiplexing so repeated tool calls do not re-authenticate.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from gdb_ssh_mcp.config import SSHConfig

# ControlPath template: ``%C`` expands to a hash of the connection parameters,
# giving a unique, short, collision-free socket path per target.
_CONTROL_PATH = '~/.ssh/cm-gdbssh-%C'


class SSHError(RuntimeError):
    """Raised when an ssh command fails or times out."""


@dataclass
class SSHResult:
    """Output of a remote command executed over ssh.

    Attributes:
        stdout: Standard output of the remote command.
        stderr: Standard error of the remote command.
        returncode: Exit code of the remote command.
    """

    stdout: str
    stderr: str
    returncode: int


class SSHClient:
    """Executes commands on a remote host via the system ssh client."""

    def __init__(self, config: SSHConfig) -> None:
        """Stores the SSH connection settings used by every command.

        Args:
            config: Connection settings for the target machine.
        """
        self._config = config

    def run(
        self, argv: Sequence[str], *, timeout: float | None = None
    ) -> SSHResult:
        """Runs the remote command described by *argv* and returns its output.

        Args:
            argv: The remote command tokens. They are joined and quoted for
                the remote shell, so pass ``['tmux', 'capture-pane', ...]``
                rather than a raw command string.
            timeout: Seconds to wait before timing out. ``None`` waits
                indefinitely.

        Returns:
            The command's stdout, stderr, and return code.

        Raises:
            SSHError: If the ssh process times out.

        Examples:
            With ``SSHClient(SSHConfig(host='10.0.0.5', user='wmz',
            port=2222))``, calling ``run(['tmux', 'has-session', '-t',
            'gdb-ssh'])`` joins the tokens into the remote command ``tmux
            has-session -t gdb-ssh``, wraps it in a full ``ssh`` invocation
            (ControlMaster options, port, user@host), and returns an
            :class:`SSHResult` carrying the remote stdout, stderr, and exit
            code.
        """
        remote_command = shlex.join(argv)
        command = [
            'ssh',
            '-o',
            'ControlMaster=auto',
            '-o',
            f'ControlPath={_CONTROL_PATH}',
            '-o',
            f'ControlPersist={self._config.control_persist}',
        ]
        if self._config.key:
            command += ['-i', self._config.key]
        if self._config.proxy_jump:
            command += ['-J', self._config.proxy_jump]
        command += [
            '-p',
            str(self._config.port),
            f'{self._config.user}@{self._config.host}',
            remote_command,
        ]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SSHError(
                f'ssh timed out after {timeout}s: {remote_command}'
            ) from exc
        return SSHResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )

    def run_checked(
        self, argv: Sequence[str], *, timeout: float | None = None
    ) -> str:
        """Runs *argv* and returns stdout, raising on failure.

        Args:
            argv: The remote command tokens.
            timeout: Seconds to wait before timing out.

        Returns:
            The command's stdout.

        Raises:
            SSHError: If the command exits non-zero or times out.

        Examples:
            For a successful command such as ``run_checked(['tmux',
            'has-session', '-t', 'gdb-ssh'])`` the remote stdout is returned
            unchanged. If the remote command exits non-zero, an
            :class:`SSHError` is raised whose message includes the return code
            and stderr.
        """
        result = self.run(argv, timeout=timeout)
        if result.returncode != 0:
            raise SSHError(
                f'ssh command failed ({result.returncode}): '
                f'{shlex.join(argv)}\nstderr: {result.stderr.strip()}'
            )
        return result.stdout
