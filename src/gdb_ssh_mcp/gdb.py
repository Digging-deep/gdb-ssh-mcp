"""gdb command construction and output post-processing.

Pure helpers with no I/O or network access, so they are unit-testable
without a gdb, tmux, or ssh environment. The tmux layer sends the commands
built here and feeds captured pane text back through the cleaning helpers.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

# gdb ``-ex`` options applied at startup so interactive output is stable and
# machine-readable: no paging, no confirmation prompts, no ANSI colors.
_GDB_INIT_OPTIONS: tuple[str, ...] = (
    'set pagination off',
    'set confirm off',
    'set style enabled off',
)

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
_GDB_PROMPT_RE = re.compile(r'^\(gdb\)\s*$')

_TRUNCATION_MARKER = '\n...[truncated]...\n'


def gdb_init_options() -> list[str]:
    """Returns the ``-ex`` options used when launching gdb.

    Returns:
        A list of gdb ``set`` commands applied at startup to keep output
        machine-readable.

    Examples:
        >>> gdb_init_options()
        ['set pagination off', 'set confirm off', 'set style enabled off']
    """
    return list(_GDB_INIT_OPTIONS)


def gdb_launch_argv(
    gdb_path: str, program: str, args: Sequence[str]
) -> list[str]:
    """Builds the gdb argv that loads *program* with the given *args*.

    Args:
        gdb_path: Path to the gdb binary on the target machine.
        program: Path to the program being debugged.
        args: Arguments passed to the inferior program.

    Returns:
        The gdb command-line tokens, ready to be run inside a tmux session.

    Examples:
        >>> gdb_launch_argv('gdb', '/opt/bin/apxd', ['--config', 'a.conf'])
        ['gdb', '-q', '-ex', 'set pagination off', '-ex', 'set confirm off', '-ex', 'set style enabled off', '--args', '/opt/bin/apxd', '--config', 'a.conf']
    """
    argv = [gdb_path, '-q']
    for option in _GDB_INIT_OPTIONS:
        argv += ['-ex', option]
    argv += ['--args', program, *args]
    return argv


def gdb_launch_command(
    gdb_path: str, program: str, args: Sequence[str]
) -> str:
    """Builds the shell command line that launches gdb with *program*.

    The result is a single line meant to be typed into a shell (for example
    inside a tmux session), so quoting is handled by ``shlex.join`` and the
    shell interprets it.

    Args:
        gdb_path: Path to the gdb binary on the target machine.
        program: Path to the program being debugged.
        args: Arguments passed to the inferior program.

    Returns:
        A single shell command line that starts gdb with the program loaded.

    Examples:
        >>> gdb_launch_command('gdb', '/opt/bin/apxd', ['--config', 'a.conf'])
        "gdb -q -ex 'set pagination off' -ex 'set confirm off' -ex 'set style enabled off' --args /opt/bin/apxd --config a.conf"
    """
    return shlex.join(gdb_launch_argv(gdb_path, program, args))


def clean_ansi(text: str) -> str:
    """Strips ANSI escape sequences from *text*.

    Args:
        text: Raw text that may contain ANSI color/control sequences.

    Returns:
        The text with escape sequences removed.

    Examples:
        >>> clean_ansi('a\\x1b[31mred\\x1b[0m')
        'ared'
    """
    return _ANSI_ESCAPE_RE.sub('', text)


def has_prompt(text: str) -> bool:
    """Returns True if the last non-empty line of *text* is the gdb prompt.

    The prompt must occupy its own line, so a value that merely contains
    ``(gdb)`` elsewhere does not count.

    Args:
        text: Captured terminal text to inspect.

    Returns:
        True when gdb is idle at its prompt.

    Examples:
        >>> has_prompt('(gdb)')
        True
        >>> has_prompt('(gdb) is not idle')
        False
    """
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and _GDB_PROMPT_RE.match(lines[-1]) is not None


def strip_prompt(text: str) -> str:
    """Removes a trailing gdb prompt line from *text*, if present.

    Args:
        text: Captured terminal text, possibly ending with ``(gdb)``.

    Returns:
        The text with trailing prompt lines removed.

    Examples:
        >>> strip_prompt('hello\\n(gdb)')
        'hello'
        >>> strip_prompt('hello')
        'hello'
    """
    lines = text.splitlines()
    while lines and _GDB_PROMPT_RE.match(lines[-1]):
        lines.pop()
    return '\n'.join(lines)


def truncate(text: str, max_chars: int) -> str:
    """Truncates *text* to at most *max_chars* characters.

    When truncation is needed, the head and tail are both kept and a marker is
    inserted in the middle, so the start and end of a long gdb output remain
    visible.

    Args:
        text: The text to truncate.
        max_chars: The maximum number of characters to keep.

    Returns:
        *text* unchanged if it fits, otherwise a head/tail slice joined by a
        ``[truncated]`` marker.

    Examples:
        >>> truncate('short', 100)
        'short'
        >>> truncate('abcdefghij', 4)
        'abcd'
    """
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_TRUNCATION_MARKER):
        return text[:max_chars]
    keep = (max_chars - len(_TRUNCATION_MARKER)) // 2
    return text[:keep] + _TRUNCATION_MARKER + text[-keep:]


def clean_output(text: str, max_chars: int) -> str:
    """Cleans captured gdb output: strip ANSI, trim prompt, truncate.

    Args:
        text: Raw captured terminal text.
        max_chars: The maximum number of characters to keep after cleaning.

    Returns:
        The cleaned text with ANSI sequences, trailing prompt, and excess
        length removed.

    Examples:
        >>> clean_output('value = 1\\n(gdb)\\n', 200)
        'value = 1'
    """
    cleaned = strip_prompt(clean_ansi(text)).rstrip()
    return truncate(cleaned, max_chars)


def cmd_run(args: Sequence[str] | None = None) -> str:
    """Builds a ``run`` command, optionally with inferior arguments.

    Args:
        args: Arguments passed to the inferior program.

    Returns:
        The gdb ``run`` command string.

    Examples:
        >>> cmd_run()
        'run'
        >>> cmd_run(['--config', 'a.conf'])
        'run --config a.conf'
    """
    if not args:
        return 'run'
    return 'run ' + ' '.join(args)


def cmd_continue() -> str:
    """Builds a ``continue`` command.

    Returns:
        The gdb ``continue`` command string.

    Examples:
        >>> cmd_continue()
        'continue'
    """
    return 'continue'


def cmd_next(count: int = 1) -> str:
    """Builds a ``next`` (step over) command for *count* statements.

    Args:
        count: Number of statements to step over.

    Returns:
        The gdb ``next`` command string.

    Examples:
        >>> cmd_next()
        'next'
        >>> cmd_next(3)
        'next 3'
    """
    return 'next' if count == 1 else f'next {count}'


def cmd_step(count: int = 1) -> str:
    """Builds a ``step`` (step into) command for *count* statements.

    Args:
        count: Number of statements to step into.

    Returns:
        The gdb ``step`` command string.

    Examples:
        >>> cmd_step()
        'step'
        >>> cmd_step(2)
        'step 2'
    """
    return 'step' if count == 1 else f'step {count}'


def cmd_finish() -> str:
    """Builds a ``finish`` command.

    Returns:
        The gdb ``finish`` command string.

    Examples:
        >>> cmd_finish()
        'finish'
    """
    return 'finish'


def cmd_breakpoint_set(location: str, condition: str | None = None) -> str:
    """Builds a ``break`` command, optionally with a condition.

    Args:
        location: Breakpoint location, e.g. ``foo.c:42`` or a function name.
        condition: Optional gdb condition expression, e.g. ``x > 3``.

    Returns:
        The gdb ``break`` command string.

    Examples:
        >>> cmd_breakpoint_set('foo.c:42')
        'break foo.c:42'
        >>> cmd_breakpoint_set('foo', 'x > 3')
        'break foo if x > 3'
    """
    command = f'break {location}'
    if condition:
        command += f' if {condition}'
    return command


def cmd_breakpoint_list() -> str:
    """Builds the command that lists all breakpoints.

    Returns:
        The gdb command string for listing breakpoints.

    Examples:
        >>> cmd_breakpoint_list()
        'info breakpoints'
    """
    return 'info breakpoints'


def cmd_breakpoint_delete(number: int) -> str:
    """Builds the command that deletes breakpoint *number*.

    Args:
        number: The gdb breakpoint number to delete.

    Returns:
        The gdb ``delete`` command string.

    Examples:
        >>> cmd_breakpoint_delete(3)
        'delete 3'
    """
    return f'delete {number}'


def cmd_backtrace(depth: int | None = None) -> str:
    """Builds a ``bt`` command, optionally limited to *depth* frames.

    Args:
        depth: Maximum number of stack frames to print.

    Returns:
        The gdb ``bt`` command string.

    Examples:
        >>> cmd_backtrace()
        'bt'
        >>> cmd_backtrace(10)
        'bt 10'
    """
    return 'bt' if depth is None else f'bt {depth}'


def cmd_frame(level: int | None = None) -> str:
    """Builds a ``frame`` command, optionally selecting *level*.

    Args:
        level: The stack frame number to select.

    Returns:
        The gdb ``frame`` command string.

    Examples:
        >>> cmd_frame()
        'frame'
        >>> cmd_frame(2)
        'frame 2'
    """
    return 'frame' if level is None else f'frame {level}'


def cmd_print(expression: str) -> str:
    """Builds a ``print`` command for *expression*.

    Args:
        expression: A gdb expression to evaluate and print.

    Returns:
        The gdb ``print`` command string.

    Examples:
        >>> cmd_print('x')
        'p x'
    """
    return f'p {expression}'


def cmd_locals() -> str:
    """Builds the command that prints the current frame's locals.

    Returns:
        The gdb command string for printing local variables.

    Examples:
        >>> cmd_locals()
        'info locals'
    """
    return 'info locals'


def cmd_args() -> str:
    """Builds the command that prints the current frame's arguments.

    Returns:
        The gdb command string for printing function arguments.

    Examples:
        >>> cmd_args()
        'info args'
    """
    return 'info args'


def cmd_read_memory(address: str, length: int) -> str:
    """Builds the command that dumps *length* bytes at *address* in hex.

    Args:
        address: A gdb address expression, e.g. ``0x400000`` or ``&buf``.
        length: Number of bytes to dump.

    Returns:
        The gdb ``x/`` examine-memory command string.

    Examples:
        >>> cmd_read_memory('0x400000', 16)
        'x/16bx 0x400000'
    """
    return f'x/{length}bx {address}'


def cmd_registers(names: str | None = None) -> str:
    """Builds an ``info registers`` command, optionally filtered by *names*.

    Args:
        names: Optional space-separated register names to show.

    Returns:
        The gdb ``info registers`` command string.

    Examples:
        >>> cmd_registers()
        'info registers'
        >>> cmd_registers('rax rbx')
        'info registers rax rbx'
    """
    return 'info registers' if names is None else f'info registers {names}'


def cmd_source(location: str | None = None) -> str:
    """Builds a ``list`` command for *location* or the current line.

    Args:
        location: Optional source location, e.g. ``foo.c:10``.

    Returns:
        The gdb ``list`` command string.

    Examples:
        >>> cmd_source()
        'list'
        >>> cmd_source('foo.c:10')
        'list foo.c:10'
    """
    return 'list' if location is None else f'list {location}'


def cmd_disassemble(function: str | None = None) -> str:
    """Builds a ``disassemble`` command for *function* or the current frame.

    Args:
        function: Optional function name to disassemble.

    Returns:
        The gdb ``disassemble`` command string.

    Examples:
        >>> cmd_disassemble()
        'disassemble'
        >>> cmd_disassemble('main')
        'disassemble main'
    """
    return 'disassemble' if function is None else f'disassemble {function}'
