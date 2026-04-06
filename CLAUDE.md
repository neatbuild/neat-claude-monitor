# Neat Claude Monitor

## Workflow

- **MUST apply TDD**: Write/update tests first, then implement. Run `pytest` to verify before and after changes.
- **MUST update spec**: After changes are made, update `docs/neat-claude-monitor-spec.md` to reflect the new behavior. The codebase is the source of truth.
- **MUST update README**: After updating the spec, update `README.md` to reflect the changes. The spec is the source of truth.
- **MUST restart app**: After applying any code changes. This is critical — skipping this causes stale behavior. Kill port 5123 and run `python -m neat_claude_monitor.cli start --port 5123` in background.

## Tech Stack

- Python 3.11+, FastAPI, Uvicorn, Jinja2
- Frontend: HTMX, Tailwind CSS (CDN), Lucide icons, SSE for live updates
- Testing: pytest, pytest-asyncio, httpx (async test client)

## Commands

- Run tests: `pytest`
- Start app: `python -m neat_claude_monitor.cli start --port 5123`
