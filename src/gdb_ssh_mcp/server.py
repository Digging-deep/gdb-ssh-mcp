"""MCP server that registers the gdb-ssh tools and serves them over stdio."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from gdb_ssh_mcp import __version__
from gdb_ssh_mcp.tools import GdbTools

SERVER_NAME = 'gdb-ssh'


def create_server(tools: GdbTools) -> MCPServer:
    """Builds an MCP server with all gdb-ssh tools registered.

    Args:
        tools: The tool implementation layer.

    Returns:
        A configured MCPServer ready to ``run()`` over stdio.

    Examples:
        >>> server = create_server(GdbTools(ssh, tmux, config))
        >>> server.run()
    """
    mcp = MCPServer(SERVER_NAME, version=__version__)

    # ---- session management ------------------------------------------

    @mcp.tool()
    def session_status() -> dict:
        """Reports whether the tmux session and gdb are alive and stopped.

        Examples:
            Returns the dict from ``tools.session_status()``, e.g.
            ``{'session_alive': True, 'stopped': True, 'output': '(gdb)'}``.
        """
        return tools.session_status()

    @mcp.tool()
    def session_start(
        program: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
    ) -> dict:
        """Starts the tmux session and gdb (reuses it if already running).

        Args:
            program: Remote path to the binary. Defaults to the configured
                target.
            args: Arguments passed to the program.
            cwd: Working directory on the remote machine.

        Examples:
            ``session_start()`` starts gdb for the configured target and
            returns ``{'output': "gdb session 'gdb-ssh' started", 'running':
            False}``.
        """
        return tools.session_start(program, args, cwd)

    @mcp.tool()
    def session_stop() -> dict:
        """Stops gdb and tears down the tmux session.

        Examples:
            Returns ``{'output': 'gdb session stopped', 'running': False}``.
        """
        return tools.session_stop()

    # ---- execution control -------------------------------------------

    @mcp.tool()
    def run(args: list[str] | None = None) -> dict:
        """Starts or restarts the inferior program with the given args.

        Examples:
            ``run(['--config', 'a.conf'])`` executes gdb ``run --config
            a.conf`` and returns ``{'output': ..., 'running': ...}``.
        """
        return tools.run(args)

    @mcp.tool(name='continue')
    def continue_() -> dict:
        """Continues execution of the inferior program.

        Examples:
            Executes gdb ``continue`` and returns its captured output.
        """
        return tools.continue_()

    @mcp.tool(name='next')
    def next_(count: int = 1) -> dict:
        """Steps over the next `count` statements (step over).

        Examples:
            ``next_(3)`` executes gdb ``next 3``.
        """
        return tools.next_(count)

    @mcp.tool()
    def step(count: int = 1) -> dict:
        """Steps into the next `count` statements (step into).

        Examples:
            ``step(2)`` executes gdb ``step 2``.
        """
        return tools.step(count)

    @mcp.tool()
    def finish() -> dict:
        """Runs until the current function returns.

        Examples:
            Executes gdb ``finish`` and returns its captured output.
        """
        return tools.finish()

    @mcp.tool()
    def interrupt() -> dict:
        """Interrupts a running program (Ctrl-C) and waits for gdb to stop.

        Examples:
            Returns ``{'output': 'interrupt sent', 'stopped': True}`` when gdb
            stopped in time.
        """
        return tools.interrupt()

    # ---- breakpoints --------------------------------------------------

    @mcp.tool()
    def breakpoint_set(
        location: str, condition: str | None = None
    ) -> dict:
        """Sets a breakpoint at `location` (e.g. `file:line` or function).

        Examples:
            ``breakpoint_set('foo.c:42')`` executes gdb ``break foo.c:42``.
        """
        return tools.breakpoint_set(location, condition)

    @mcp.tool()
    def breakpoint_list() -> dict:
        """Lists all breakpoints.

        Examples:
            Executes gdb ``info breakpoints`` and returns the list.
        """
        return tools.breakpoint_list()

    @mcp.tool()
    def breakpoint_delete(number: int) -> dict:
        """Deletes the breakpoint with the given number.

        Examples:
            ``breakpoint_delete(3)`` executes gdb ``delete 3``.
        """
        return tools.breakpoint_delete(number)

    # ---- inspection ---------------------------------------------------

    @mcp.tool()
    def backtrace(depth: int = 20) -> dict:
        """Prints the call stack, limited to `depth` frames.

        Examples:
            ``backtrace(10)`` executes gdb ``bt 10``.
        """
        return tools.backtrace(depth)

    @mcp.tool()
    def frame(level: int | None = None) -> dict:
        """Selects and shows the stack frame at `level` (default: current).

        Examples:
            ``frame(2)`` executes gdb ``frame 2``.
        """
        return tools.frame(level)

    @mcp.tool(name='print')
    def print_expression(expression: str) -> dict:
        """Evaluates and prints a gdb expression.

        Examples:
            ``print_expression('x')`` executes gdb ``p x``.
        """
        return tools.print_expression(expression)

    @mcp.tool(name='locals')
    def print_locals() -> dict:
        """Prints the current frame's local variables.

        Examples:
            Executes gdb ``info locals``.
        """
        return tools.print_locals()

    @mcp.tool(name='args')
    def print_args() -> dict:
        """Prints the current frame's function arguments.

        Examples:
            Executes gdb ``info args``.
        """
        return tools.print_args()

    @mcp.tool()
    def read_memory(address: str, length: int) -> dict:
        """Reads `length` bytes at `address` and returns them as hex.

        Examples:
            ``read_memory('0x400000', 16)`` executes gdb ``x/16bx 0x400000``.
        """
        return tools.read_memory(address, length)

    @mcp.tool()
    def registers(names: str | None = None) -> dict:
        """Reads register values, optionally limited to `names`.

        Examples:
            ``registers('rax rbx')`` executes gdb ``info registers rax rbx``.
        """
        return tools.registers(names)

    @mcp.tool()
    def source(location: str | None = None) -> dict:
        """Shows source code at `location` or around the current line.

        Examples:
            ``source('foo.c:10')`` executes gdb ``list foo.c:10``.
        """
        return tools.source(location)

    @mcp.tool()
    def disassemble(function: str | None = None) -> dict:
        """Disassembles `function` or the current function.

        Examples:
            ``disassemble('main')`` executes gdb ``disassemble main``.
        """
        return tools.disassemble(function)

    @mcp.tool()
    def execute(command: str) -> dict:
        """Passes a raw gdb command through untouched (escape hatch).

        Examples:
            ``execute('info threads')`` sends ``info threads`` to gdb
            verbatim.
        """
        return tools.execute(command)

    # ---- batch mode ---------------------------------------------------

    @mcp.tool()
    def run_batch(commands: list[str]) -> dict:
        """Runs gdb in non-interactive batch mode over a fresh process.

        Useful for one-shot crash analysis. `commands` are gdb commands run
        after startup, e.g. `["break main", "run", "bt"]`.

        Examples:
            ``run_batch(['bt'])`` runs gdb in batch mode and returns the
            combined output, without touching the interactive session.
        """
        return tools.run_batch(commands)

    return mcp
