"""Local integration test driving a real gdb through tmux without ssh.

Requires gdb, tmux, and a C compiler on the local machine; it skips when any
of those is missing. The ssh layer is replaced by a local subprocess runner,
so this exercises the full tmux -> gdb -> marker-sync path end to end.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap

import pytest

from gdb_ssh_mcp.config import Config, GDBConfig, SSHConfig, TargetConfig
from gdb_ssh_mcp.ssh import SSHError, SSHResult
from gdb_ssh_mcp.tmux import TmuxSession
from gdb_ssh_mcp.tools import GdbTools

_GDB = shutil.which('gdb')
_TMUX = shutil.which('tmux')
_CC = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

pytestmark = pytest.mark.skipif(
    not (_GDB and _TMUX and _CC),
    reason='requires gdb, tmux, and a C compiler',
)


class _LocalRunner:
    """Runs commands locally, matching the SSHClient interface."""

    def run(self, argv, *, timeout=None):
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout
        )
        return SSHResult(proc.stdout, proc.stderr, proc.returncode)

    def run_checked(self, argv, *, timeout=None):
        result = self.run(argv, timeout=timeout)
        if result.returncode != 0:
            raise SSHError(f'command failed: {argv!r}')
        return result.stdout


def _compile_prog(tmp_path) -> str:
    source = tmp_path / 'prog.c'
    binary = tmp_path / 'prog'
    source.write_text(
        textwrap.dedent(
            """\
            int main(void) {
                int x = 1;
                int y = 2;
                int z = x + y;
                return z;
            }
            """
        ),
        encoding='utf-8',
    )
    subprocess.run(
        [_CC, '-g', '-O0', '-o', str(binary), str(source)], check=True
    )
    return str(binary)


def test_full_flow(tmp_path) -> None:
    binary = _compile_prog(tmp_path)
    config = Config(
        ssh=SSHConfig(host='unused', user='unused'),
        target=TargetConfig(program=binary, gdb=_GDB),
        gdb=GDBConfig(tmux_session=f'gdb-ssh-test-{os.getpid()}'),
    )
    runner = _LocalRunner()
    tools = GdbTools(runner, TmuxSession(runner, config.gdb), config)
    try:
        assert 'started' in tools.session_start()['output']
        assert 'Breakpoint' in tools.breakpoint_set('main')['output']
        assert 'Breakpoint' in tools.run()['output']
        assert '= 2' in tools.print_expression('1 + 1')['output']
        assert 'main' in tools.backtrace()['output']
    finally:
        tools.session_stop()
