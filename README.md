# Neat Claude Monitor

A web-based tool for monitoring and controlling permissions across running Claude Code sessions from a single browser tab.

![Neat Claude Monitor](docs/images/neat-claude-monitor.png)

## What it does

Neat Claude Monitor sits between Claude Code and your terminal, intercepting tool-use permission requests via [Claude Code hooks](https://docs.anthropic.com/en/docs/claude-code/hooks). Instead of approving each action in every terminal, you manage all sessions from one browser dashboard.

- **Auto-approve safe tools** (Read, Grep, Glob, Edit, Write) per project
- **Review dangerous tools** (Bash, Agent, etc.) with approve/deny from the browser
- **Monitor multiple sessions** across VS Code and terminal simultaneously
- **Real-time updates** via SSE — pending requests appear instantly with macOS notifications

## Architecture

```
Claude Code Sessions                    Neat Claude Monitor (single process)
+--------------+                       +------------------------------+
| Session 1    |--hook curl---------->|  FastAPI server               |
| (VS Code)    |<-----JSON response---|  (127.0.0.1:5123)            |
+--------------+                       |                              |
| Session 2    |--hook curl---------->|  /health        health check  |
| (terminal)   |<-----JSON response---|  /request       hook POST    |
+--------------+                       |  /              serves UI    |
                                       |  /events        SSE stream   |
                                       +----------+-------------------+
                                                  |
                                    +-------------+----------------+
                                    |  Browser (localhost:5123)    |
                                    |  HTMX + Tailwind + Jinja2   |
                                    |  SSE for real-time updates   |
                                    +------------------------------+
```

## Installation

Requires Python 3.11+.

```bash
pip install -e .
```

## Usage

```bash
neat-claude-monitor              # install hooks + start server + open browser
neat-claude-monitor --port 5200  # custom port
neat-claude-monitor uninstall    # remove hooks from ~/.claude/settings.json
```

On startup, Neat Claude Monitor:

1. Installs hooks into `~/.claude/settings.json` (merges with existing hooks)
2. Starts a FastAPI server on `127.0.0.1:5123`
3. Opens the dashboard in your browser

## Dashboard

Two-column layout:

- **Left** — Projects list with toggle controls + pending approval requests
- **Right** — History of past decisions, grouped by project (each entry shows per-turn input/output tokens). Each group can be cleared individually via a trash icon.

Each project displays:

- Session count
- Token usage (input/output, all-time)
- Estimated cost (all-time, calculated from Claude Code transcripts)
- Auto-approval controls

### Per-project controls

| Control | Effect |
|---------|--------|
| Auto-Approving | When on, safe tools are automatically approved |
| Dangerous Excluded | When on (default), dangerous tools still require manual approval even with auto-approving enabled |

### Tool categories

| Category | Tools | Auto-approved |
|----------|-------|---------------|
| Safe | Read, Grep, Glob, Edit, Write | Yes (when auto-approving is on) |
| Dangerous | Bash, Agent, all others | Only when Dangerous Excluded is off |

## Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| Monitor not running | Claude Code works normally (curl fails silently) |
| Auto-approving off | Returns passthrough, Claude shows normal terminal prompt |
| No response (5 min) | curl times out, falls back to terminal prompt |
| Session ends while pending | Pending request auto-denied |

## Development

```bash
# Run tests
pytest

# Start the server manually
python -m neat_claude_monitor.cli start --port 5123
```

## Tech Stack

- **Backend**: Python, FastAPI, Uvicorn, Jinja2
- **Frontend**: HTMX, Tailwind CSS (CDN), Lucide icons, SSE
- **Testing**: pytest, pytest-asyncio, httpx
