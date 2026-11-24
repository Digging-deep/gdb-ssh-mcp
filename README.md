# gdb-ssh-mcp

把一个跑在内网机器上的交互式 gdb 封装成 MCP server，让 Claude Code 像调工具一样
远程驱动调试：下断点、单步、打印、看内存/寄存器、读调用栈。

架构一句话：**本机 AI ↔ SSH ↔ 内网机器 tmux 里的常驻 gdb**。所有 ssh 走系统
OpenSSH（复用 `~/.ssh/config` 的 key / ProxyJump / known_hosts），gdb 跑在 tmux
会话里，所以 ssh 断线、多次工具调用之间，gdb 的断点与状态都不会丢。

## 前提

- **本机（跑 MCP server 的地方）**：Python 3.10+、能 `ssh user@host` 免密登录目标机。
- **目标机（内网）**：装有 `gdb` 和 `tmux`（无需外网，也无需装任何 LLM/MCP 组件）。

## 安装

```bash
cd gdb-ssh-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 配置

复制示例并按需修改：

```bash
mkdir -p ~/.config/gdb-ssh-mcp
cp config.example.yaml ~/.config/gdb-ssh-mcp/config.yaml
vim ~/.config/gdb-ssh-mcp/config.yaml
```

完整字段见 [config.example.yaml](config.example.yaml)。也可以不写文件，直接用
`GDBSSH_*` 环境变量覆盖文件值（方便 `claude mcp add` 时内联）：

| 环境变量 | 对应配置 |
|---|---|
| `GDBSSH_HOST` | `ssh.host` |
| `GDBSSH_USER` | `ssh.user` |
| `GDBSSH_PORT` | `ssh.port` |
| `GDBSSH_KEY` | `ssh.key` |
| `GDBSSH_PROXY_JUMP` | `ssh.proxy_jump` |
| `GDBSSH_PROGRAM` | `target.program` |
| `GDBSSH_TMUX_SESSION` | `gdb.tmux_session` |

## 接入 Claude Code

```bash
claude mcp add gdb-ssh -- gdb-ssh-mcp --config ~/.config/gdb-ssh-mcp/config.yaml
```

或在 `.mcp.json` 里写：

```json
{
  "mcpServers": {
    "gdb-ssh": {
      "command": "gdb-ssh-mcp",
      "args": ["--config", "~/.config/gdb-ssh-mcp/config.yaml"]
    }
  }
}
```

## 工具清单

| 分类 | 工具 |
|---|---|
| 会话 | `session_start` `session_status` `session_stop` |
| 执行 | `run` `continue` `next` `step` `finish` `interrupt` |
| 断点 | `breakpoint_set` `breakpoint_list` `breakpoint_delete` |
| 检查 | `backtrace` `frame` `print` `locals` `args` `read_memory` `registers` `source` `disassemble` |
| 逃生舱 | `execute`（透传任意 gdb 命令） |
| 批处理 | `run_batch`（`gdb -batch` 一次性崩溃分析，无状态） |

## 工作原理

- 一次工具调用 = 往 tmux 会话里的 gdb 发命令 → 轮询 `capture-pane` 抓屏 → 清洗
  ANSI、截断 → 返回。
- **命令完成判定用 marker 而非提示符**：发命令后紧跟 `echo <唯一marker>`，只有前一
  条命令跑完 gdb 才会执行到 echo，所以 marker 一出现即代表完成，避免提示符轮询的
  竞态。超时未出现 marker 则返回 `running: true`（程序仍在跑，可 `interrupt`）。
- gdb 启动时已关闭分页（`set pagination off`）、确认（`set confirm off`）和颜色
  （`set style enabled off`），保证输出可机读。

## 测试

```bash
pytest
```

- `tests/test_config.py`、`tests/test_gdb.py`、`tests/test_tmux.py`：纯逻辑单测，无需环境。
- `tests/test_local.py`：本地集成测试（用真 gdb + tmux，不走 ssh）。本机没有 gdb/tmux
  时自动跳过。

## 安全说明

- 复用系统 OpenSSH，不新增密钥存储；配置里的 host/user/key 只在本机，不进版本库。
- `execute` 透传意味着 AI 可发任意 gdb 命令（含 gdb 的 `shell`），这是刻意保留的
  能力——你在自己的内网机器上调自己的程序。如需收紧，可在 gdb init 里禁用 `shell`。
- MCP server 只监听 stdio，不开放网络端口。
