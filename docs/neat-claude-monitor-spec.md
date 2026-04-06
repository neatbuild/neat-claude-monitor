# Neat Claude Monitor — Specification

A web-based tool for monitoring and controlling permissions across running Claude Code sessions from a single browser tab.

## Architecture

```
Claude Code Sessions                    Neat Claude Monitor (single process)
+--------------+                       +------------------------------+
| Session 1    |--hook curl---------->|  FastAPI server               |
| (VS Code)    |<-----JSON response---|  (127.0.0.1:5123)            |
+--------------+                       |                              |
| Session 2    |--hook curl---------->|  /health        health check  |
| (terminal)   |<-----JSON response---|  /session/start session reg   |
+--------------+                       |  /session/end   session dereg|
                                       |  /request       hook POST    |
                                       |  /respond/:id   browser POST |
                                       |  /events        SSE stream   |
                                       |  /              serves UI    |
                                       +----------+-------------------+
                                                  |
                                    +-------------+----------------+
                                    |  Browser (localhost:5123)    |
                                    |  HTMX + Tailwind + Jinja2   |
                                    |  SSE for real-time updates   |
                                    +------------------------------+
```

## Data Models

### SessionInfo

| Field | Type | Description |
|-------|------|-------------|
| session_id | str | Unique Claude Code session identifier |
| cwd | str | Working directory |
| project_path | str | Absolute project path (derived from cwd) |
| transcript_path | str | Path to session transcript |
| auto_approving | bool | Whether auto-approve is enabled (default: False) |
| dangerous_excluded | bool | Whether dangerous tools are excluded from auto-approve (default: True) |
| registered_at | datetime | When session was registered |
| last_activity | datetime | Updated on each PreToolUse request |

### PendingRequest

| Field | Type | Description |
|-------|------|-------------|
| uuid | str | Unique request ID |
| session_id | str | Owning session |
| tool_name | str | Tool being requested (Bash, Edit, etc.) |
| tool_input | dict | Tool parameters |
| cwd | str | Working directory |
| created_at | datetime | When request arrived |
| future | asyncio.Future | Resolved when user responds or session ends |

### HistoryEntry

| Field | Type | Description |
|-------|------|-------------|
| uuid | str | Unique entry ID |
| session_id | str | Owning session |
| tool_name | str | Tool name |
| tool_input | dict | Tool parameters |
| cwd | str | Working directory |
| decision | str | "allow", "deny", or "auto" |
| decided_at | datetime | When decision was made |
| input_tokens | int | Input tokens for the API turn (from transcript, default 0) |
| output_tokens | int | Output tokens for the API turn (from transcript, default 0) |

### ProjectUsage

| Field | Type | Description |
|-------|------|-------------|
| input_tokens | int | Total input tokens (all-time) |
| output_tokens | int | Total output tokens (all-time) |
| cache_creation_tokens | int | Total cache creation tokens (all-time) |
| cache_read_tokens | int | Total cache read tokens (all-time) |
| cost_usd | float | Estimated cost in USD |

### Constants

- `MAX_HISTORY_PER_PROJECT`: 20 entries per project
- `STALE_SESSION_SECONDS`: 14400 (4 hours)

## Auto-Approve Logic

### Tool Categories

| Category | Tools | Auto-approved when enabled |
|----------|-------|---------------------------|
| Safe | Read, Grep, Glob, Edit, Write | Always (when auto-approving is on) |
| Dangerous | Bash, Agent, all others | Only when `dangerous_excluded` is Off |

### Decision Flow

1. Unknown `session_id` -> auto-register with defaults, return `{}` (passthrough)
2. `auto_approving` is Off -> return `{}` (passthrough to Claude Code's normal prompt)
3. `auto_approving` is On + tool is safe -> return allow, log as "auto"
4. `auto_approving` is On + `dangerous_excluded` is Off -> return allow for all tools
5. Otherwise -> add to pending queue, send macOS notification, block until user responds

## Token Usage

Neat Claude Monitor calculates per-project token usage and estimated costs by parsing Claude Code transcript JSONL files.

### Data Source

Transcripts are stored in `~/.claude/projects/<encoded-path>/`, where `<encoded-path>` is the project path with `/`, `.`, and `_` characters replaced with `-`.

### Parsing Logic

- Only completed messages (with `stop_reason`) are counted
- Messages are deduplicated by `message.id`
- Messages with model `<synthetic>` are skipped
- Token counts are summed across all messages: `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`

### Pricing

Per-model rates using prefix matching:

| Model Prefix | Input (MTok) | Output (MTok) | Cache Write (MTok) | Cache Read (MTok) |
|--------------|--------------|---------------|--------------------|-------------------|
| Opus | $15.00 | $75.00 | $18.75 | $1.50 |
| Sonnet | $3.00 | $15.00 | $3.75 | $0.30 |
| Haiku | $0.80 | $4.00 | $1.00 | $0.08 |

Unknown models fall back to Sonnet pricing.

### Token Display Format

Tokens are formatted using `format_tokens()`:
- `500` → "500"
- `1,234` → "1.2K"
- `74,000` → "74K"
- `1,234,567` → "1.2M"
- `117,000,000` → "117M"

### Background Refresh

Token usage is recalculated every 60 seconds in a background task. Updates are broadcast via SSE with event type `token_usage_update`.

## HTTP Endpoints

### Hook Endpoints (called by Claude Code)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/session/start` | POST | Register session. Body: `{session_id, cwd, transcript_path}`. Broadcasts SSE. |
| `/session/end` | POST | Deregister session. Cancels pending futures (deny). Broadcasts SSE. |
| `/request` | POST | Tool permission request. **May block** until resolved. Body: `{session_id, tool_name, tool_input, cwd}`. |
| `/health` | GET | Returns `{"status": "running"}`. |

### Browser Endpoints (called by HTMX)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serves the main HTML page with sessions, pending, and history. |
| `/respond/{uuid}/{decision}` | POST | Resolve a pending request. Decision: "allow" or "deny". Returns HTML fragment. |
| `/toggle-mode/{project_path}` | POST | Toggle auto-approving on/off for a project. Returns updated session row HTML. |
| `/toggle-dangerous-excluded/{project_path}` | POST | Toggle dangerous tool exclusion. Returns updated session row HTML. |
| `/remove-project/{project_path}` | POST | Remove project and its sessions. Releases pending futures. Returns empty HTML. |
| `/history/{project_path}` | DELETE | Clear history for a project path without unregistering the project. Returns empty HTML. |
| `/events` | GET | SSE stream. Events: `session_update`, `new_request`, `history_update`. |

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sessions` | GET | Returns grouped sessions as JSON. |
| `/api/pending` | GET | Returns pending requests as JSON. |

## Hook Response Format

Allow:
```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}
```

Deny:
```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Denied by Neat Claude Monitor"}}
```

Passthrough (unmonitored): `{}` — Claude Code falls through to its normal terminal prompt.

## Claude Code Hooks

Three hooks installed in `~/.claude/settings.json` via the `install_hooks` function. All use inline `curl` commands (no external scripts). Identified by a `# neat-claude-monitor` marker comment.

| Event | Behavior | Timeout | Async |
|-------|----------|---------|-------|
| SessionStart | POST to `/session/start`, fire-and-forget | 10s | Yes |
| SessionEnd | POST to `/session/end`, fire-and-forget | 10s | Yes |
| PreToolUse | POST to `/request`, blocks until response | 300s | No |

Port is configurable via `NEAT_CLAUDE_MONITOR_PORT` env var (default: 5123).

## Persistence

### Data Directory

Runtime data is stored in the `data/` directory (git-ignored). This directory is created automatically on first run.

### project_settings.json

Single file in the `data/` directory. Keyed by absolute project path.

```json
{
  "/Users/me/project": {
    "auto_approving": true,
    "dangerous_excluded": true,
    "sessions": {
      "session-uuid": {
        "transcript_path": "/path/to/transcript",
        "registered_at": "2026-03-15T09:00:00+00:00"
      }
    }
  }
}
```

Sessions are restored from this file on server restart.

### project_history.json

History entries persisted per project path, capped at `MAX_HISTORY_PER_PROJECT` (20) per project. Filtered on load to only include projects still in the registry.

## Registry

The `Registry` class manages all session and project state.

### Key Behaviors

- **Session grouping**: UI shows one row per project path with session count. Toggling settings applies to all sessions in the project.
- **Stale cleanup**: Background task runs every 60 seconds, removes sessions inactive for 4+ hours.
- **Legacy migration**: Auto-migrates from old `claude_monitor.json`, `preferences.json`, and `sessions.json` formats.
- **Remove project**: Deletes project entry and all associated sessions.

## Web UI

### Layout

Two-column grid:
- **Left**: Projects list + Pending requests
- **Right**: History grouped by project (scrollable)

### Projects List

Sorted by: auto-approving projects first, then non-auto-approving projects. Within each group, projects with active sessions come before inactive ones, ordered by most recent activity (descending). Activity is determined by the most recent `last_activity` timestamp among a project's active sessions.

Each project row shows:
- Status indicator (green = auto-approving, gray = off)
- Project folder name + session count badge
- Token usage badges: input tokens (all-time), output tokens (all-time)
- Estimated cost badge (all-time)
- Remove button (with confirmation modal)
- Toggle buttons: Auto-Approving (on/off), Dangerous Execution Excluded (on/off, only visible when auto-approving is on)

### Pending Requests

Cards showing: tool name, project path, tool details, timestamp, Approve/Deny buttons.

### History

Grouped by project, each in a collapsible `<details>` block. Sorted by most recent activity (descending), matching the projects list order. Only the first project is expanded by default; all others are collapsed. Each group header includes a trash icon button to clear all history for that project path (without unregistering the project). Individual entries show: decision badge (OK/DENY/AUTO), tool name, category tag (safe/mutating/dangerous), per-turn input/output token counts (read from the session transcript at the time the entry is recorded), and timestamp. Expandable detail view per tool type.

### Tool Detail Rendering

| Tool | Detail shown |
|------|-------------|
| Bash | Command text |
| Edit | File path + old/new string diff (red/green) |
| Write | File path + content preview (truncated 500 chars) |
| Read | File path |
| Grep/Glob | Pattern + search path |
| Agent | Description + prompt (truncated 500 chars) |
| Other | Generic key/value display |

### Real-time Updates

- SSE events trigger HTMX re-fetches of relevant sections (not HTML fragments in events)
- Idiomorph extension for smooth DOM morphing
- `<details>` open state preserved across swaps (except history project groups, which re-apply server-rendered "first only" rule on reorder)
- Tab title shows pending count: `(2) Neat Claude Monitor`
- SSE reconnect triggers full page reload

### Notifications

macOS notifications via `osascript` when manual approval is needed. Runs in a thread to avoid blocking the event loop. Fails silently if unavailable.

## CLI

```bash
neat-claude-monitor              # install hooks + start server + open browser
neat-claude-monitor --port 5200  # custom port
neat-claude-monitor uninstall    # remove hooks from settings.json
```

### Startup Sequence

1. Install/update hooks in `~/.claude/settings.json` (merges, preserves existing hooks)
2. Start FastAPI server on `127.0.0.1:{port}`
3. Wait for `/health` to respond (5s timeout)
4. Open browser to `http://localhost:{port}`

## Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| Monitor not running | curl fails silently, Claude works normally |
| Session not auto-approving | Returns `{}`, Claude shows normal prompt |
| No response (5 min timeout) | curl times out, falls back to terminal prompt |
| Session ends while request pending | Pending future resolved with "deny" |
| Auto-approving toggled off while pending | Pending futures resolved with "allow" |
| Browser disconnected | SSE reconnect triggers full page reload |
| Unknown session_id | Auto-registered with defaults (not auto-approving) |

## Project Structure

```
neat-claude-monitor/
  pyproject.toml
  CLAUDE.md
  data/                    # git-ignored, created at runtime
    project_settings.json
    project_history.json
  neat_claude_monitor/
    __init__.py
    cli.py              # CLI entry point, hook install, server start
    server.py           # FastAPI app, all endpoints, SSE, request blocking
    registry.py         # Session/project management, persistence, auto-approve
    models.py           # SessionInfo, PendingRequest, HistoryEntry dataclasses
    hooks.py            # Hook install/uninstall in ~/.claude/settings.json
    notifications.py    # macOS notifications via osascript
    token_usage.py      # Token usage calculation from Claude Code transcripts
    templates/
      index.html        # Main page: HTMX + Tailwind + Jinja2 + delete modal
      fragments/
        session_row.html    # Project row with toggles + remove
        request_card.html   # Pending request card with approve/deny
        history_card.html   # Collapsible history entry
        _tool_detail.html   # Tool-specific detail rendering
  tests/
    test_server.py
    test_registry.py
    test_hooks.py
    test_models.py
    test_token_usage.py
```

## Dependencies

**Runtime**: FastAPI, uvicorn, Jinja2
**Frontend (CDN)**: HTMX 2.0, HTMX SSE extension, Idiomorph, Tailwind CSS, Lucide icons
**Dev**: pytest, pytest-asyncio, httpx
