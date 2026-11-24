"""tmux session control over ssh.

The remote gdb runs inside a named tmux session so its state survives ssh
disconnects and repeated MCP tool calls. This module sends gdb commands to
that session and captures the resulting output.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass

from gdb_ssh_mcp import gdb
from gdb_ssh_mcp.config import GDBConfig, TargetConfig
from gdb_ssh_mcp.ssh import SSHClient

# Seconds between capture-pane polls while waiting for a command to finish.
_POLL_INTERVAL = 0.2

# tmux key names that should be sent as control keys rather than literal text.
_SPECIAL_KEYS = frozenset({'Enter', 'C-c', 'C-l'})


@dataclass
class CommandResult:
    """Result of sending a gdb command to the tmux session.

    Attributes:
        output: The cleaned gdb output captured up to completion or timeout.
        running: ``True`` when the command did not finish within the timeout.
    """

    output: str
    running: bool


class TmuxSession:
    """Controls the tmux session that hosts the remote gdb."""

    def __init__(self, ssh: SSHClient, config: GDBConfig) -> None:
        """Stores the ssh transport and gdb session settings.

        Args:
            ssh: The SSH transport used to reach the target machine.
            config: gdb session settings (tmux session name, timeouts, output
                limits).
        """
        self._ssh = ssh
        self._config = config
        self._session = config.tmux_session
        self._marker_counter = itertools.count()

    # ---- session lifecycle -------------------------------------------

    def start(self, target: TargetConfig) -> None:
        """Creates the tmux session and launches gdb in it.

        Idempotent: if the session already exists it is reused, matching the
        ``session_start`` semantics in the plan.

        Args:
            target: The program to debug and how gdb should launch it.

        Raises:
            SSHError: If the session could not be created.

        Examples:
            ``start(TargetConfig(program='/opt/bin/apxd', args=['--config',
            'a.conf']))`` runs ``tmux new-session -d -s gdb-ssh`` on the
            remote host, then types the gdb launch command
            (``gdb -q -ex 'set pagination off' ... --args /opt/bin/apxd
            --config a.conf``) into the fresh shell so quoting survives.
        """
        if self.is_running():
            return
        argv = ['tmux', 'new-session', '-d', '-s', self._session]
        if target.cwd:
            argv += ['-c', target.cwd]
        self._ssh.run_checked(argv)
        # Type the gdb launch command into the fresh shell rather than passing
        # it to ``new-session``: a command passed to tmux is re-run through a
        # shell, which would mangle arguments containing spaces (e.g. the
        # ``-ex`` options). Letting the shell parse the typed line preserves
        # quoting correctly.
        command = gdb.gdb_launch_command(
            target.gdb, target.program, target.args
        )
        self.send_keys([command, 'Enter'])

    def is_running(self) -> bool:
        """Returns True if the tmux session exists.

        Returns:
            True when the remote tmux session is present.

        Examples:
            ``is_running()`` returns ``True`` after :meth:`start` and ``False``
            after :meth:`stop`.
        """
        result = self._ssh.run(['tmux', 'has-session', '-t', self._session])
        return result.returncode == 0

    def stop(self) -> None:
        """Kills the tmux session (and the gdb it hosts), if present.

        Examples:
            ``stop()`` is idempotent: calling it with no active session is a
            no-op.
        """
        if self.is_running():
            self._ssh.run(['tmux', 'kill-session', '-t', self._session])

    # ---- low-level tmux primitives -----------------------------------

    def send_keys(self, keys: Sequence[str]) -> None:
        """Sends key strokes to the session.

        Special key names such as ``Enter`` or ``C-c`` are sent as control
        keys; all other strings are sent literally.

        Args:
            keys: The key names or literal strings to send, in order.

        Examples:
            ``send_keys(['next', 'Enter'])`` types ``next`` into gdb and then
            presses Enter. ``send_keys(['C-c'])`` sends Ctrl-C to interrupt a
            running inferior.
        """
        for key in keys:
            if key in _SPECIAL_KEYS:
                self._ssh.run_checked(
                    ['tmux', 'send-keys', '-t', self._session, key]
                )
            else:
                self._ssh.run_checked(
                    ['tmux', 'send-keys', '-l', '-t', self._session, key]
                )

    def capture_pane(self) -> str:
        """Returns the current visible text of the session's pane.

        Returns:
            The pane text as captured by ``tmux capture-pane``.

        Examples:
            ``capture_pane()`` returns the raw pane content, which may still
            contain ANSI escapes and a trailing ``(gdb)`` prompt; callers pass
            it through :func:`gdb.clean_output` before returning to the user.
        """
        return self._ssh.run_checked(
            ['tmux', 'capture-pane', '-p', '-t', self._session]
        )

    # ---- high-level gdb command execution ----------------------------

    def run_gdb_command(
        self, command: str, *, timeout: float | None = None
    ) -> CommandResult:
        """Sends *command* to gdb and waits for it to complete.

        Completion is detected with a unique marker rather than by polling for
        the ``(gdb)`` prompt: the command is followed by ``echo <marker>`` and
        gdb only reaches the ``echo`` after the command finishes, so the
        marker's appearance is unambiguous. Polling for the prompt alone is
        racy because the prompt is already on screen before the command runs.

        The screen is cleared first (``C-l``) so captured output does not
        contain stale text from earlier commands.

        Args:
            command: The gdb command text to execute.
            timeout: Seconds to wait for completion. Defaults to the
                configured ``command_timeout_sec``.

        Returns:
            A CommandResult. ``running`` is True when the command did not
            complete within the timeout; in that case ``output`` holds
            whatever was captured so far.

        Examples:
            ``run_gdb_command('bt')`` clears the pane, sends ``bt``, then
            ``echo __GDBSSH_DONE_0__``. It polls ``capture-pane`` until that
            marker appears, then returns the text before the marker as
            ``CommandResult(output=..., running=False)``. If the marker never
            appears within the timeout, it returns ``running=True`` with the
            output captured so far.
        """
        if timeout is None:
            timeout = self._config.command_timeout_sec
        marker = f'__GDBSSH_DONE_{next(self._marker_counter)}__'
        self.send_keys(['C-l', command, 'Enter', f'echo {marker}', 'Enter'])
        deadline = time.monotonic() + timeout
        captured = ''
        while time.monotonic() < deadline:
            captured = self.capture_pane()
            if marker in captured:
                return CommandResult(
                    output=self._extract_output(captured, marker),
                    running=False,
                )
            time.sleep(_POLL_INTERVAL)
        return CommandResult(
            output=gdb.clean_output(captured, self._config.max_output_chars),
            running=True,
        )

    def interrupt(self) -> None:
        """Sends Ctrl-C to gdb to interrupt a running inferior.

        Examples:
            ``interrupt()`` sends ``C-c`` to the pane; it does not itself wait
            for gdb to stop (see :meth:`wait_for_prompt`).
        """
        self.send_keys(['C-c'])

    def wait_for_prompt(self, *, timeout: float) -> bool:
        """Polls until gdb is idle at its prompt or the timeout expires.

        Args:
            timeout: Seconds to wait.

        Returns:
            True if the prompt was observed within *timeout* seconds.

        Examples:
            ``wait_for_prompt(timeout=30)`` returns ``True`` as soon as the
            pane shows gdb back at ``(gdb)``, or ``False`` after 30 seconds.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if gdb.has_prompt(self.capture_pane()):
                return True
            time.sleep(_POLL_INTERVAL)
        return False

    def _extract_output(self, captured: str, marker: str) -> str:
        """Returns the portion of *captured* before the marker line.

        Args:
            captured: Raw pane text captured after the marker appeared.
            marker: The unique completion marker string.

        Returns:
            The cleaned text preceding the marker line.

        Examples:
            For captured text ``'bt\\n#0 main ()\\n__GDBSSH_DONE_0__'`` and
            marker ``'__GDBSSH_DONE_0__'``, the result is the cleaned
            backtrace ``'bt\\n#0 main ()'``.
        """
        kept: list[str] = []
        for line in captured.splitlines():
            if marker in line:
                break
            kept.append(line)
        return gdb.clean_output('\n'.join(kept), self._config.max_output_chars)
