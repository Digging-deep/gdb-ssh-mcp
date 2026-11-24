"""Command-line entry point for the gdb-ssh MCP server."""

from __future__ import annotations

import argparse
import sys

from gdb_ssh_mcp.config import ConfigError, load_config
from gdb_ssh_mcp.server import create_server
from gdb_ssh_mcp.ssh import SSHClient
from gdb_ssh_mcp.tmux import TmuxSession
from gdb_ssh_mcp.tools import GdbTools

_DEFAULT_CONFIG_PATH = '~/.config/gdb-ssh-mcp/config.yaml'


def _build_parser() -> argparse.ArgumentParser:
    """Builds the command-line argument parser.

    Returns:
        An :class:`argparse.ArgumentParser` with a single ``--config`` option.

    Examples:
        >>> parser = _build_parser()
        >>> args = parser.parse_args(['--config', '/tmp/cfg.yaml'])
        >>> args.config
        '/tmp/cfg.yaml'
    """
    parser = argparse.ArgumentParser(
        prog='gdb-ssh-mcp',
        description='MCP server that drives a remote gdb over SSH.',
    )
    parser.add_argument(
        '--config',
        default=_DEFAULT_CONFIG_PATH,
        help='path to the YAML config file (default: %(default)s)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Loads config, wires the server, and runs it over stdio.

    Args:
        argv: Command-line arguments. ``None`` reads from ``sys.argv``.

    Returns:
        0 on normal exit, 1 if the config could not be loaded.

    Examples:
        ``main()`` with a valid ``--config`` returns ``0`` after the server
        exits normally. If the config file is missing or invalid, it prints
        ``gdb-ssh-mcp: ...`` to stderr and returns ``1``.
    """
    args = _build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f'gdb-ssh-mcp: {exc}', file=sys.stderr)
        return 1

    ssh = SSHClient(config.ssh)
    tmux = TmuxSession(ssh, config.gdb)
    tools = GdbTools(ssh, tmux, config)
    server = create_server(tools)
    server.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
