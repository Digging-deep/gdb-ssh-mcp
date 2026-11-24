"""Unit tests for gdb_ssh_mcp.tmux marker-sync and output extraction."""

from __future__ import annotations

from gdb_ssh_mcp.config import GDBConfig
from gdb_ssh_mcp.tmux import TmuxSession


def _session() -> TmuxSession:
    # _extract_output does not touch ssh, so it can be None here.
    return TmuxSession(ssh=None, config=GDBConfig())


def test_extract_output_stops_at_marker() -> None:
    session = _session()
    captured = (
        '(gdb) next\n'
        '42    int z = x + y;\n'
        '(gdb) echo __M__\n'
        '__M__\n'
        '(gdb)'
    )
    assert session._extract_output(captured, '__M__') == (
        '(gdb) next\n42    int z = x + y;'
    )


def test_extract_output_cleans_ansi() -> None:
    session = _session()
    captured = (
        '(gdb) p x\n'
        '\x1b[32m$1 = 42\x1b[0m\n'
        '(gdb) echo __M__\n'
        '__M__\n'
    )
    assert session._extract_output(captured, '__M__') == '(gdb) p x\n$1 = 42'


class _FakeSession(TmuxSession):
    """TmuxSession with scripted send_keys/capture_pane for offline tests."""

    def __init__(self, captures: list[str]) -> None:
        super().__init__(ssh=None, config=GDBConfig())
        self.sent: list[list[str]] = []
        self._captures = iter(captures)

    def send_keys(self, keys: list[str]) -> None:
        self.sent.append(list(keys))

    def capture_pane(self) -> str:
        return next(self._captures)


def test_run_gdb_command_marker_sync() -> None:
    captures = [
        '(gdb) next\n42    int z = x + y;',
        '(gdb) next\n42    int z = x + y;\n'
        '(gdb) echo __GDBSSH_DONE_0__\n__GDBSSH_DONE_0__\n(gdb)',
    ]
    session = _FakeSession(captures)
    result = session.run_gdb_command('next', timeout=5.0)

    assert result.running is False
    assert result.output == '(gdb) next\n42    int z = x + y;'
    assert session.sent == [
        ['C-l', 'next', 'Enter', 'echo __GDBSSH_DONE_0__', 'Enter']
    ]


def test_run_gdb_command_timeout_reports_running() -> None:
    # The marker never appears, so the command is reported as still running.
    session = _FakeSession(['(gdb) continue\n[Inferior running]'])
    result = session.run_gdb_command('continue', timeout=0.1)

    assert result.running is True
    assert 'running' in result.output or 'continue' in result.output
