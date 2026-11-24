"""Tool implementations for the gdb-ssh MCP server.

Each method maps one MCP tool to a gdb command (built by :mod:`gdb_ssh_mcp.gdb`)
and executes it through the tmux session. Methods return JSON-serializable
dicts so the server layer only needs to register them.
"""

from __future__ import annotations

from gdb_ssh_mcp import gdb
from gdb_ssh_mcp.config import Config, TargetConfig
from gdb_ssh_mcp.ssh import SSHClient
from gdb_ssh_mcp.tmux import TmuxSession


class GdbTools:
    """Implements the gdb-ssh tool set on top of ssh and tmux."""

    def __init__(
        self, ssh: SSHClient, tmux: TmuxSession, config: Config
    ) -> None:
        """Stores the ssh, tmux, and config dependencies.

        Args:
            ssh: The SSH transport used for batch-mode commands.
            tmux: The tmux session that hosts the interactive gdb.
            config: Full configuration (target program, gdb settings, ...).
        """
        self._ssh = ssh
        self._tmux = tmux
        self._config = config

    # ---- session management ------------------------------------------

    def session_status(self) -> dict:
        """Reports whether the tmux session and gdb are alive and stopped.

        Returns:
            A dict with ``session_alive``, ``stopped``, and ``output`` keys.

        Examples:
            Returns ``{'session_alive': False, 'stopped': False, 'output':
            'no gdb session'}`` when no tmux session exists; otherwise
            ``session_alive`` is ``True`` and ``stopped`` reflects whether gdb
            is idle at its prompt.
        """
        if not self._tmux.is_running():
            return {
                'session_alive': False,
                'stopped': False,
                'output': 'no gdb session',
            }
        captured = self._tmux.capture_pane()
        stopped = gdb.has_prompt(captured)
        return {
            'session_alive': True,
            'stopped': stopped,
            'output': gdb.clean_output(
                captured, self._config.gdb.max_output_chars
            ),
        }

    def session_start(
        self,
        program: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
    ) -> dict:
        """Starts the tmux session and gdb, reusing it if already running.

        Any parameter left as ``None`` falls back to the configured target.

        Args:
            program: Remote path to the binary. Defaults to configured target.
            args: Arguments passed to the program.
            cwd: Working directory on the remote machine.

        Returns:
            A dict describing the result; ``running`` is always ``False``.

        Examples:
            ``session_start()`` with no arguments starts gdb using the
            configured target and returns ``{'output': "gdb session 'gdb-ssh'
            started", 'running': False}``.
        """
        target = TargetConfig(
            program=program or self._config.target.program,
            args=args if args is not None else list(self._config.target.args),
            cwd=cwd if cwd is not None else self._config.target.cwd,
            gdb=self._config.target.gdb,
        )
        self._tmux.start(target)
        return {
            'output': f"gdb session '{self._config.gdb.tmux_session}' started",
            'running': False,
        }

    def session_stop(self) -> dict:
        """Stops gdb and tears down the tmux session.

        Returns:
            A dict describing the result.

        Examples:
            Returns ``{'output': 'gdb session stopped', 'running': False}``.
        """
        self._tmux.stop()
        return {'output': 'gdb session stopped', 'running': False}

    # ---- execution control -------------------------------------------

    def run(self, args: list[str] | None = None) -> dict:
        """Starts or restarts the inferior program.

        Args:
            args: Optional arguments to pass to the inferior.

        Returns:
            A dict with ``output`` and ``running`` keys from the gdb command.

        Examples:
            ``run()`` executes gdb ``run``; ``run(['--config', 'a.conf'])``
            executes gdb ``run --config a.conf``. ``running`` is ``True`` if
            the program is still running when the timeout expires.
        """
        return self._execute(gdb.cmd_run(args))

    def continue_(self) -> dict:
        """Continues execution of the inferior.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            Executes gdb ``continue`` and returns its captured output.
        """
        return self._execute(gdb.cmd_continue())

    def next_(self, count: int = 1) -> dict:
        """Steps over the next *count* statements.

        Args:
            count: Number of statements to step over.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``next_()`` executes gdb ``next``; ``next_(3)`` executes gdb
            ``next 3``.
        """
        return self._execute(gdb.cmd_next(count))

    def step(self, count: int = 1) -> dict:
        """Steps into the next *count* statements.

        Args:
            count: Number of statements to step into.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``step()`` executes gdb ``step``; ``step(2)`` executes gdb
            ``step 2``.
        """
        return self._execute(gdb.cmd_step(count))

    def finish(self) -> dict:
        """Runs until the current function returns.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            Executes gdb ``finish`` and returns its captured output.
        """
        return self._execute(gdb.cmd_finish())

    def interrupt(self) -> dict:
        """Interrupts a running inferior and waits for gdb to stop.

        Returns:
            A dict with ``output`` and ``stopped`` keys; ``stopped`` is
            ``True`` when gdb returned to its prompt within the timeout.

        Examples:
            Returns ``{'output': 'interrupt sent', 'stopped': True}`` when gdb
            stopped in time, or ``{'output': 'interrupt sent', 'stopped':
            False}`` otherwise.
        """
        self._tmux.interrupt()
        stopped = self._tmux.wait_for_prompt(
            timeout=self._config.gdb.command_timeout_sec
        )
        return {'output': 'interrupt sent', 'stopped': stopped}

    # ---- breakpoints --------------------------------------------------

    def breakpoint_set(
        self, location: str, condition: str | None = None
    ) -> dict:
        """Sets a breakpoint at *location*, optionally with *condition*.

        Args:
            location: Breakpoint location, e.g. ``foo.c:42`` or a function.
            condition: Optional gdb condition expression.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``breakpoint_set('foo.c:42')`` executes gdb ``break foo.c:42``;
            ``breakpoint_set('foo', 'x > 3')`` executes gdb ``break foo if
            x > 3``.
        """
        return self._execute(gdb.cmd_breakpoint_set(location, condition))

    def breakpoint_list(self) -> dict:
        """Lists all breakpoints.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            Executes gdb ``info breakpoints`` and returns the list.
        """
        return self._execute(gdb.cmd_breakpoint_list())

    def breakpoint_delete(self, number: int) -> dict:
        """Deletes breakpoint *number*.

        Args:
            number: The gdb breakpoint number to delete.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``breakpoint_delete(3)`` executes gdb ``delete 3``.
        """
        return self._execute(gdb.cmd_breakpoint_delete(number))

    # ---- inspection ---------------------------------------------------

    def backtrace(self, depth: int = 20) -> dict:
        """Prints the call stack, limited to *depth* frames.

        Args:
            depth: Maximum number of stack frames to print.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            Executes gdb ``bt 20`` by default, or ``bt 10`` when ``depth=10``.
        """
        return self._execute(gdb.cmd_backtrace(depth))

    def frame(self, level: int | None = None) -> dict:
        """Selects and shows the stack frame at *level*.

        Args:
            level: The stack frame number to select.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``frame()`` executes gdb ``frame``; ``frame(2)`` executes gdb
            ``frame 2``.
        """
        return self._execute(gdb.cmd_frame(level))

    def print_expression(self, expression: str) -> dict:
        """Evaluates and prints *expression*.

        Args:
            expression: A gdb expression to evaluate and print.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``print_expression('x')`` executes gdb ``p x``.
        """
        return self._execute(gdb.cmd_print(expression))

    def print_locals(self) -> dict:
        """Prints the current frame's local variables.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            Executes gdb ``info locals``.
        """
        return self._execute(gdb.cmd_locals())

    def print_args(self) -> dict:
        """Prints the current frame's function arguments.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            Executes gdb ``info args``.
        """
        return self._execute(gdb.cmd_args())

    def read_memory(self, address: str, length: int) -> dict:
        """Reads *length* bytes at *address* as hex.

        Args:
            address: A gdb address expression, e.g. ``0x400000`` or ``&buf``.
            length: Number of bytes to dump.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``read_memory('0x400000', 16)`` executes gdb ``x/16bx 0x400000``.
        """
        return self._execute(gdb.cmd_read_memory(address, length))

    def registers(self, names: str | None = None) -> dict:
        """Reads register values, optionally limited to *names*.

        Args:
            names: Optional space-separated register names to show.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``registers()`` executes gdb ``info registers``; ``registers('rax
            rbx')`` executes gdb ``info registers rax rbx``.
        """
        return self._execute(gdb.cmd_registers(names))

    def source(self, location: str | None = None) -> dict:
        """Shows source code at *location* or the current line.

        Args:
            location: Optional source location, e.g. ``foo.c:10``.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``source()`` executes gdb ``list``; ``source('foo.c:10')`` executes
            gdb ``list foo.c:10``.
        """
        return self._execute(gdb.cmd_source(location))

    def disassemble(self, function: str | None = None) -> dict:
        """Disassembles *function* or the current function.

        Args:
            function: Optional function name to disassemble.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``disassemble()`` executes gdb ``disassemble``;
            ``disassemble('main')`` executes gdb ``disassemble main``.
        """
        return self._execute(gdb.cmd_disassemble(function))

    def execute(self, command: str) -> dict:
        """Passes a raw gdb command through untouched (escape hatch).

        Args:
            command: The raw gdb command text to execute.

        Returns:
            A dict with ``output`` and ``running`` keys.

        Examples:
            ``execute('info threads')`` sends ``info threads`` to gdb verbatim,
            useful for gdb commands that have no dedicated tool.
        """
        return self._execute(command)

    # ---- batch mode ---------------------------------------------------

    def run_batch(self, commands: list[str]) -> dict:
        """Runs gdb in non-interactive batch mode over a fresh process.

        Useful for one-shot crash analysis that does not need session state.

        Args:
            commands: gdb commands to run after startup, e.g.
                ``['break main', 'run', 'bt']``.

        Returns:
            A dict with the combined output and ``running`` always False.

        Examples:
            ``run_batch(['break main', 'run', 'bt'])`` runs ``gdb -batch -ex
            'set pagination off' ... -ex 'break main' -ex run -ex bt --args
            <program> <args>`` directly over ssh and returns the combined
            stdout.
        """
        target = self._config.target
        argv = [target.gdb, '-batch']
        for option in gdb.gdb_init_options():
            argv += ['-ex', option]
        for command in commands:
            argv += ['-ex', command]
        argv += ['--args', target.program, *target.args]
        result = self._ssh.run(
            argv, timeout=self._config.gdb.command_timeout_sec
        )
        output = result.stdout
        if result.returncode != 0:
            output += f'\n[gdb exited with code {result.returncode}]'
        return {'output': output, 'running': False}

    # ---- helpers ------------------------------------------------------

    def _execute(self, command: str) -> dict:
        """Runs *command* in the interactive gdb and returns its result.

        Args:
            command: The gdb command text to execute.

        Returns:
            A dict with ``output`` (cleaned captured text) and ``running``
            (whether the command is still running).

        Examples:
            ``_execute('bt')`` delegates to
            :meth:`TmuxSession.run_gdb_command` and reshapes the
            :class:`~gdb_ssh_mcp.tmux.CommandResult` into a plain dict.
        """
        result = self._tmux.run_gdb_command(command)
        return {'output': result.output, 'running': result.running}
