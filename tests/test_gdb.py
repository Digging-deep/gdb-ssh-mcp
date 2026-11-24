"""Unit tests for gdb_ssh_mcp.gdb."""

from __future__ import annotations

from gdb_ssh_mcp import gdb


def test_gdb_init_options_stable() -> None:
    options = gdb.gdb_init_options()
    assert 'set pagination off' in options
    assert 'set confirm off' in options


def test_gdb_launch_argv_includes_program_and_args() -> None:
    argv = gdb.gdb_launch_argv('gdb', '/opt/prog', ['--conf', 'x.conf'])
    assert argv[0] == 'gdb'
    assert '-ex' in argv
    assert 'set pagination off' in argv
    # --args must precede the program and its arguments.
    assert argv[argv.index('--args') + 1] == '/opt/prog'
    assert argv[-2:] == ['--conf', 'x.conf']


def test_gdb_launch_command_quotes_options_with_spaces() -> None:
    command = gdb.gdb_launch_command('gdb', '/p', [])
    assert "'set pagination off'" in command
    assert command.startswith('gdb -q')
    assert '--args /p' in command


def test_clean_ansi_strips_escape_sequences() -> None:
    text = '\x1b[31mred\x1b[0m plain \x1b[1;32mgreen\x1b[m'
    assert gdb.clean_ansi(text) == 'red plain green'


def test_clean_ansi_leaves_plain_text_untouched() -> None:
    text = 'no escapes here\n(just text)'
    assert gdb.clean_ansi(text) == text


def test_has_prompt_detects_trailing_prompt() -> None:
    assert gdb.has_prompt('some output\n(gdb)')
    assert gdb.has_prompt('(gdb)')
    assert gdb.has_prompt('line1\nline2\n(gdb) ')


def test_has_prompt_rejects_mid_line_prompt() -> None:
    assert not gdb.has_prompt('the string (gdb) is not a prompt')
    assert not gdb.has_prompt('output\nmore output')


def test_strip_prompt_removes_trailing_prompt() -> None:
    assert gdb.strip_prompt('hello\n(gdb)') == 'hello'
    assert gdb.strip_prompt('hello\n(gdb)\n(gdb)') == 'hello'
    assert gdb.strip_prompt('no prompt') == 'no prompt'


def test_truncate_keeps_short_text() -> None:
    assert gdb.truncate('abc', 100) == 'abc'


def test_truncate_respects_max_chars() -> None:
    text = 'x' * 1000
    result = gdb.truncate(text, 200)
    assert len(result) <= 200
    assert 'truncated' in result
    # Head and tail are both preserved.
    assert result.startswith('x')
    assert result.endswith('x')


def test_truncate_tiny_limit() -> None:
    text = 'x' * 1000
    result = gdb.truncate(text, 5)
    assert len(result) <= 5


def test_clean_output_pipeline() -> None:
    raw = '\x1b[32mvalue = 42\x1b[0m\n(gdb)'
    assert gdb.clean_output(raw, 100) == 'value = 42'


def test_cmd_run_with_and_without_args() -> None:
    assert gdb.cmd_run() == 'run'
    assert gdb.cmd_run(['--flag', 'x']) == 'run --flag x'


def test_cmd_next_step_count() -> None:
    assert gdb.cmd_next() == 'next'
    assert gdb.cmd_next(5) == 'next 5'
    assert gdb.cmd_step() == 'step'
    assert gdb.cmd_step(3) == 'step 3'


def test_cmd_breakpoint_set_with_and_without_condition() -> None:
    assert gdb.cmd_breakpoint_set('foo.c:12') == 'break foo.c:12'
    assert (
        gdb.cmd_breakpoint_set('main', 'x == 0')
        == 'break main if x == 0'
    )


def test_cmd_backtrace_depth() -> None:
    assert gdb.cmd_backtrace() == 'bt'
    assert gdb.cmd_backtrace(20) == 'bt 20'


def test_cmd_read_memory() -> None:
    assert gdb.cmd_read_memory('0x7fff0000', 16) == 'x/16bx 0x7fff0000'


def test_cmd_registers() -> None:
    assert gdb.cmd_registers() == 'info registers'
    assert gdb.cmd_registers('rax rbx') == 'info registers rax rbx'
