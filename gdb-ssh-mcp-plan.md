# SSH + GDB MCP Server 方案

> 目标：把「本机 AI ↔ SSH ↔ 内网机器 gdb」封装成一个 MCP server，让 Claude Code 能像调工具一样驱动远程 GDB 调试。

## 0. 命名

| 层级 | 名称 | 用途 |
|---|---|---|
| MCP 注册名 | `gdb-ssh` | `claude mcp add gdb-ssh -- ...`，Claude Code 里显示的名字 |
| 项目 / 包名 | `gdb-ssh-mcp` | 仓库名、pip 包名 |
| Python 模块名 | `gdb_ssh_mcp` | `python -m gdb_ssh_mcp` 的导入路径 |
| tmux 会话名 | `gdb-ssh` | 内网机器上的常驻会话 |
| 配置目录 | `~/.config/gdb-ssh-mcp/` | 配置文件位置 |

命名理由：`gdb-ssh` 一眼能看出「gdb + 走 ssh」，短、好输入，也能和 GitHub 上已有的本地 `gdb-mcp` 项目区分开。
备选：`remote-gdb`（强调远程）、`gdb-bridge`（强调 AI↔gdb 桥接）。

## 1. 目标与边界

- **做什么**：一个跑在本机的 MCP server（stdio 协议），通过 SSH 连接内网机器，在那台机器上操控 gdb，把断点/单步/打印/内存/寄存器等操作暴露成 MCP 工具给 Claude Code 使用。
- **不做什么**：
  - 不在内网机器上装任何 LLM / MCP 组件（内网通常无外网）。
  - 不重新实现一个调试器，只做 gdb 的「远程遥控层」。
  - 不做多用户/鉴权/审计（单机自用）。

## 2. 约束与前提

1. 内网机器**不能访问外网**，所以 LLM 调用、MCP 协议都只在本机发生。
2. 本机已能通过 `ssh user@host` 免密（或 key）登录内网机器。
3. 内网机器上已有 gdb 和 tmux（tmux 用于维持常驻会话，见 §7）。

## 3. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  本机                                                         │
│  ┌──────────────┐  MCP(stdio, JSON-RPC)  ┌────────────────┐  │
│  │ Claude Code   │ ─────────────────────▶ │ gdb-ssh-mcp    │  │
│  │  (MCP client) │ ◀───────────────────── │  server(Python)│  │
│  └──────────────┘                         └───────┬────────┘  │
│                                                   │ SSH        │
│                              (ControlMaster 复用连接)         │
└───────────────────────────────────────────────────┼───────────┘
                                                     ▼
┌─────────────────────────────────────────────────────────────┐
│  内网机器（无外网）                                           │
│  ┌────────────────────────────────────────────┐              │
│  │ tmux 会话 "gdb-ssh"                          │              │
│  │   └─ gdb ./apexdb  （常驻，状态跨调用保留）   │              │
│  └────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

- **数据流**：Claude Code 调 MCP 工具 → server 把工具映射成 gdb 命令 → `ssh ... tmux send-keys` 发命令 → `tmux capture-pane` 抓屏 → server 清洗 ANSI/截断 → 返回给 Claude Code。
- 所有 ssh 命令走 OpenSSH 的 `ControlMaster` 连接复用，避免每条命令重新认证。

## 4. 技术选型

| 项 | 选择 | 理由 | 备选 |
|---|---|---|---|
| 语言 | Python 3.10+ | 与 gdb/gdb-dashboard 生态一致，你熟悉；异步 subprocess 成熟 | TypeScript/Node |
| MCP SDK | `mcp` 官方 Python SDK（或 `FastMCP`） | 官方、stdio 开箱即用、类型清晰 | 手写 JSON-RPC |
| 传输 | stdio | Claude Code 标准接入方式 | SSE（需要常驻端口，本机场景无必要） |
| SSH 方式 | 系统 OpenSSH（subprocess），**不用 paramiko** | 直接复用已有 `~/.ssh/config` 的 key、ProxyJump、known_hosts | paramiko（需重配认证） |
| gdb 会话 | tmux 常驻会话（主模式）+ `gdb -batch`（快速模式） | tmux 天然提供真 pty，Ctrl-C/断点状态都能保留，ssh 断了 gdb 也不死 | 长驻 `ssh -tt host gdb` 子进程（更直接但更脆） |
| 输出同步 | 轮询 `(gdb)` 提示符出现来判断命令结束 | 比固定 sleep 可靠，能区分「停住」和「还在跑」 | 固定延时 |

## 5. MCP 工具清单

分为四类：会话管理、执行控制、断点、检查。

### 5.1 会话管理
| 工具 | 参数 | 说明 |
|---|---|---|
| `session_status` | — | 返回 gdb 是否在运行、当前 file:line、当前线程、会话是否存活 |
| `session_start` | `program`, `args?`, `cwd?` | 建 tmux 会话并启动 gdb（幂等，已存在则复用） |
| `session_stop` | — | 结束 gdb 与 tmux 会话 |

### 5.2 执行控制
| 工具 | 参数 | 说明 |
|---|---|---|
| `run` | `args?` | 启动/重启被调试程序 |
| `continue` | — | 继续执行 |
| `next` | `count=1` | 单步跳过（step over） |
| `step` | `count=1` | 单步进入（step into） |
| `finish` | — | 执行到当前函数返回 |
| `interrupt` | — | 给正在运行的程序发中断（Ctrl-C），使其停下 |

### 5.3 断点
| 工具 | 参数 | 说明 |
|---|---|---|
| `breakpoint_set` | `location`, `condition?` | 下断点：`file:line` 或函数名 |
| `breakpoint_list` | — | 列出所有断点 |
| `breakpoint_delete` | `number` | 按编号删断点 |

### 5.4 检查（读状态，不改程序）
| 工具 | 参数 | 说明 |
|---|---|---|
| `backtrace` | `depth=20` | 调用栈 |
| `frame` | `level?` | 选择并查看栈帧 |
| `print` | `expression` | 求值并打印表达式 |
| `locals` | — | 当前帧局部变量 |
| `args` | — | 当前帧函数参数 |
| `read_memory` | `address`, `length` | 读内存（hex + ascii） |
| `registers` | `names?` | 读寄存器（默认全部通用寄存器） |
| `source` | `location` | 取源码片段（`file:line` 或当前行周围） |
| `disassemble` | `function?` | 反汇编当前函数或指定函数 |
| `execute` | `command` | **原始 gdb 命令透传**（逃生舱，任何上面没封装的命令都能用） |

> `execute` 是最重要的逃生舱：不必给每个 gdb 命令都写一个工具，AI 需要 `watch`、`x/i`、`info threads` 等直接透传即可。

## 6. 单次工具调用的时序（tmux 模式）

```
Claude Code → tool: next
  server: ssh host "tmux send-keys -t gdb-ssh 'next' Enter"
  server: 轮询 ssh host "tmux capture-pane -p -t gdb-ssh"（最多 N 秒）
          - 若出现 "(gdb)" 提示符 → 程序已停，命令完成
          - 若超时无提示符 → 程序仍在运行 → 返回 {running: true}
  server: 清洗 ANSI、去边界、截断到 max_output_chars
  server → Claude Code: {output, running, location?}
```

**关键点：用 `(gdb)` 提示符做同步**。gdb 停住时才会显示提示符，所以：
- 抓到提示符 = 命令执行完、程序处于停止态；
- 抓不到 = 程序还在跑，AI 应调用 `interrupt` 或等待。

## 7. 两种 gdb 模式

1. **tmux 常驻（主模式）**：状态跨调用保留，支持单步/断点/Ctrl-C，是真正的交互式调试。
2. **batch 快速模式（可选）**：对「一次性崩溃分析」这类无需状态的任务，直接用
   `ssh host "gdb -batch -ex b ... -ex run -ex bt -ex 'info locals' ./prog"`。
   实现为一个独立工具 `run_batch(commands: list[str])`，跑完即返回，干净无状态。

两种模式共享同一套 SSH 连接管理。

## 8. 输出清洗与解析

- gdb 启动时设 `set pagination off`、`set confirm off`，避免输出分页卡住。
- 关闭 gdb 颜色（`set style enabled off`，若版本支持），减少 ANSI。
- server 端仍做兜底 ANSI 正则清洗（`\x1b\[[0-9;]*[A-Za-z]`）。
- 输出截断：默认 `max_output_chars = 20000`，超长时保留头尾并标注截断位置。
- 从 capture-pane 里剥离 tmux 边框/滚动残留（取提示符到屏尾之间的一段）。

## 9. 配置

配置文件 `~/.config/gdb-ssh-mcp/config.yaml`（路径通过 MCP 启动参数传入）：

```yaml
ssh:
  host: 10.0.0.5
  user: wmz
  port: 22
  key: ~/.ssh/id_ed25519          # 可选，缺省走 ssh 默认
  proxy_jump: jump.corp.com       # 可选，内网需要跳板机时
  control_persist: 600            # 连接复用时长（秒）

target:
  program: /path/to/apexdb        # 远程机器上的二进制路径
  args: ["--config", "xx.conf"]
  cwd: /path/to/workspace
  gdb: gdb                        # 可选，默认 gdb

gdb:
  tmux_session: gdb-ssh
  max_output_chars: 20000
  command_timeout_sec: 30         # 等 (gdb) 提示符的超时
```

也支持用环境变量覆盖（`GDBSSH_HOST` 等），方便 `claude mcp add` 时直接内联。

## 10. 目录结构

```
gdb-ssh-mcp/
├── pyproject.toml
├── README.md
└── src/gdb_ssh_mcp/
    ├── __init__.py
    ├── __main__.py        # python -m gdb_ssh_mcp 入口
    ├── server.py          # MCP server：注册工具、分发调用
    ├── ssh.py             # OpenSSH 子进程封装 + ControlMaster
    ├── tmux.py            # tmux 会话：send-keys / capture-pane / prompt 轮询
    ├── gdb.py             # gdb 命令构造、输出清洗、截断
    ├── tools.py           # 各工具的实现逻辑
    └── config.py          # 配置加载与校验
tests/
    ├── test_gdb.py        # 输出清洗、命令构造单测
    └── test_local.py      # 本地 gdb（不经 ssh）集成测试
```

## 11. 安全与权限

- 复用系统 OpenSSH：key、known_hosts、ProxyJump 全部沿用现有配置，不新增秘密存储。
- `execute` 透传意味着 AI 可发任意 gdb 命令（含 gdb 的 `shell`/Python），**这是刻意保留的能力**——你在自己的内网机器上调自己的程序，换取最大调试自由度；如果将来要收紧，可在 gdb init 里禁用 `shell` 或用受限 gdb。
- MCP server 只监听 stdio，不开放网络端口，无远程攻击面。
- 敏感信息（host/user/key）只存在本机配置文件，且不进版本库（`.gitignore`）。

## 12. 分阶段实施（里程碑）

| 阶段 | 交付物 | 验收标准 |
|---|---|---|
| **M1 最小可用** | stdio MCP 骨架 + `execute` 透传 + `run_batch`（batch 模式） | Claude Code 里能 `claude mcp add` 成功，`execute("bt")` 返回远程输出 |
| **M2 常驻会话** | tmux 会话管理 + `session_start/status/stop` + `run/continue/next/step/finish/interrupt` | 能跨多次调用保持断点状态，`next` 后 `print` 能读到新值 |
| **M3 结构化工具** | 断点类 + 检查类工具、prompt 同步、ANSI 清洗与截断 | `breakpoint_set` 命中、`read_memory` 返回 hex、`source` 返回源码 |
| **M4 连接与配置** | ControlMaster 复用、ProxyJump、超时/重连、config.yaml | 连续 20 条工具调用无需重新认证；断网重连后会话仍可复用 |
| **M5 打磨** | 单测 + 本地集成测试 + README + `.mcp.json` 接入模板 | 本地 gdb 集成测试全绿；文档可按步骤复现接入 |

## 13. 测试

- **单测**（无需环境）：ANSI 清洗、命令构造、输出截断、prompt 判定逻辑。
- **本地集成**（不连 ssh）：在本机 gdb 上跑一个几行的 C 程序，验证 `run/break/next/print/bt` 全链路（把 `ssh` 层换成空实现）。
- **远程冒烟**：对真实内网机器跑一遍 M1~M3 的核心工具。
- 每阶段结束用 `claude mcp` 或一个最小 MCP client 脚本做一次端到端验证。

## 14. 风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| capture-pane 抓屏有噪音 | tmux 屏里可能残留历史滚动 | 每次命令前清屏 + 只取提示符之后的文本 |
| prompt 轮询误判 | 程序输出里恰好包含 `(gdb)` 字样 | 用「行首提示符 + 短暂稳定」判定，而非子串匹配 |
| gdb 版本差异 | 不同 gdb 对 `set style` 等支持不一 | 命令都用兼容写法，失败时 fallback |
| 长输出超时 | 大对象 pretty-print 很慢 | `max_output_chars` + `command_timeout_sec`，超时返回「仍在运行」 |
| 断网/重连 | ssh 断开后 tmux 会话仍在但连接层失效 | ControlMaster 自动重建；gdb 会话由 tmux 保住 |

## 15. 后续可选增强（不在首版）

- `watch`（硬件/写监视点）结构化工具。
- 多会话：同时管理多个 gdb 目标。
- 把 gdb 输出 diff 化，只返回「相对上次的变化」，省 token。
- 与本地 workspace 源码联动：自动把 `file:line` 映射到本机源码文件供 AI 直接读。
