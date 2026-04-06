"""FastAPI server for Neat Claude Monitor."""

from __future__ import annotations

import asyncio
import uuid as uuid_mod
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from neat_claude_monitor.history import HistoryStore
from neat_claude_monitor.models import Decision, PendingRequest
from neat_claude_monitor.notifications import notify
from neat_claude_monitor.registry import Registry
from neat_claude_monitor.token_usage import format_tokens, get_last_message_usage

DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(
    data_dir: Path | None = None,
    templates_dir: Path | None = None,
) -> FastAPI:
    # Registry is created early so the lifespan can reference it
    if data_dir is None:
        data_dir = Path(__file__).parent.parent / "data"

    registry = Registry(data_dir)
    sse_queues: list[asyncio.Queue] = []

    async def broadcast_sse(event_type: str, data: str) -> None:
        if not sse_queues:
            return
        msg = f"event: {event_type}\ndata: {data}\n\n"
        for queue in list(sse_queues):
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                sse_queues.remove(queue)

    async def _cleanup_stale_sessions() -> None:
        """Periodically remove sessions that haven't had activity."""
        while True:
            await asyncio.sleep(60)
            removed = registry.cleanup_stale()
            if removed:
                await broadcast_sse("session_update", "refresh")

    async def _flush_history() -> None:
        """Periodically flush dirty history to disk."""
        while True:
            await asyncio.sleep(2)
            history.flush()

    async def _refresh_token_usage() -> None:
        """Periodically refresh token usage data and broadcast updates."""
        while True:
            registry.refresh_token_usage()
            await broadcast_sse("session_update", "refresh")
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cleanup_task = asyncio.create_task(_cleanup_stale_sessions())
        flush_task = asyncio.create_task(_flush_history())
        token_task = asyncio.create_task(_refresh_token_usage())
        app.state.cleanup_task = cleanup_task
        yield
        token_task.cancel()
        flush_task.cancel()
        cleanup_task.cancel()
        history.flush()

    app = FastAPI(title="Neat Claude Monitor", lifespan=lifespan)

    if templates_dir is None:
        templates_dir = DEFAULT_TEMPLATES_DIR

    history = HistoryStore(data_dir)
    history.retain_projects(registry.project_paths)

    pending: dict[str, PendingRequest] = {}

    templates = Jinja2Templates(directory=str(templates_dir))

    def _make_allow_response() -> dict:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": Decision.ALLOW,
        }}

    def _make_deny_response(reason: str = "Denied by Neat Claude Monitor") -> dict:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": Decision.DENY,
                "permissionDecisionReason": reason,
            }
        }

    def _release_pending(
        *,
        session_id: str | None = None,
        project_path: str | None = None,
        decision: str = Decision.ALLOW,
    ) -> None:
        """Resolve pending futures matching session or project."""
        for req in list(pending.values()):
            if not req.future or req.future.done():
                continue
            if session_id is not None and req.session_id == session_id:
                req.future.set_result(decision)
            elif project_path is not None:
                req_session = registry.get_session(req.session_id)
                req_project = req_session.project_path if req_session else req.cwd
                if req_project == project_path:
                    req.future.set_result(decision)

    @app.get("/health")
    async def health():
        return {"status": "running"}

    @app.post("/session/start")
    async def session_start(request: Request):
        body = await request.json()
        session_id = body.get("session_id", "")
        cwd = body.get("cwd", "")
        transcript_path = body.get("transcript_path", "")

        if session_id:
            registry.register_session(session_id, cwd, transcript_path)
            await broadcast_sse("session_update", "refresh")

        return JSONResponse({})

    @app.post("/session/end")
    async def session_end(request: Request):
        body = await request.json()
        session_id = body.get("session_id", "")

        _release_pending(session_id=session_id, decision=Decision.DENY)

        if session_id:
            registry.deregister_session(session_id)
            await broadcast_sse("session_update", "refresh")

        return JSONResponse({})

    def _get_session_tokens(session_id: str) -> tuple[int, int]:
        """Get token usage from the session's transcript file."""
        session = registry.get_session(session_id)
        if session and session.transcript_path:
            return get_last_message_usage(session.transcript_path)
        return 0, 0

    @app.post("/request")
    async def tool_request(request: Request):
        body = await request.json()
        session_id = body.get("session_id", "")
        tool_name = body.get("tool_name", "")
        tool_input = body.get("tool_input", {})
        cwd = body.get("cwd", "")

        if not session_id:
            return JSONResponse({})

        # Auto-register unknown sessions
        registry.get_or_register(session_id, cwd)
        registry.touch_session(session_id)

        # Not auto_approving -> pass through
        if not registry.is_auto_approving(session_id):
            return JSONResponse({})

        # Auto-approve safe tools
        if registry.should_auto_approve(session_id, tool_name):
            inp, out = _get_session_tokens(session_id)
            history.add(str(uuid_mod.uuid4()), session_id, tool_name, tool_input, cwd, Decision.AUTO,
                        input_tokens=inp, output_tokens=out)
            await broadcast_sse("history_update", "refresh")
            return JSONResponse(_make_allow_response())

        # Manual approval needed
        req_uuid = str(uuid_mod.uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        req = PendingRequest(
            uuid=req_uuid,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            cwd=cwd,
            created_at=datetime.now(timezone.utc),
            future=future,
        )
        pending[req_uuid] = req

        # Notify
        session = registry.get_session(session_id)
        project = session.project_path if session else cwd
        asyncio.create_task(notify("Neat Claude Monitor", f"{tool_name} requested in {project}"))
        await broadcast_sse("new_request", "refresh")

        # Block until resolved
        try:
            decision = await future
        except asyncio.CancelledError:
            decision = Decision.DENY
        finally:
            pending.pop(req_uuid, None)

        # Log to history
        inp, out = _get_session_tokens(session_id)
        history.add(req_uuid, session_id, tool_name, tool_input, cwd, decision,
                    input_tokens=inp, output_tokens=out)
        await broadcast_sse("history_update", "refresh")

        if decision == Decision.ALLOW:
            return JSONResponse(_make_allow_response())
        return JSONResponse(_make_deny_response())

    @app.post("/respond/{req_uuid}/{decision}")
    async def respond(req_uuid: str, decision: str):
        req = pending.get(req_uuid)
        if req is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        if decision not in (Decision.ALLOW, Decision.DENY):
            return JSONResponse({"error": "invalid decision"}, status_code=400)

        if req.future and not req.future.done():
            req.future.set_result(decision)

        await broadcast_sse("new_request", "refresh")

        if decision == Decision.ALLOW:
            label, color = "Approved", "emerald"
        else:
            label, color = "Denied", "red"
        return HTMLResponse(
            f'<div class="text-xs text-{color}-400 text-center py-2'
            f' animate-pulse">{label}</div>'
        )

    def _return_project_row(request: Request, project_path: str):
        for group in registry.list_grouped_sessions():
            if group["project_path"] == project_path:
                return templates.TemplateResponse(
                    request,
                    "fragments/session_row.html",
                    {"session": group},
                )
        return JSONResponse({"status": "ok"})

    @app.post("/toggle-mode/{project_path:path}")
    async def toggle_mode(request: Request, project_path: str):
        project_path = "/" + project_path
        was_auto_approving = registry.is_project_auto_approving(project_path)
        if not registry.toggle_mode(project_path):
            return JSONResponse({"error": "not found"}, status_code=404)

        # When monitoring is turned OFF, release any pending requests
        # so the Claude Code session isn't left hanging.
        if was_auto_approving and not registry.is_project_auto_approving(project_path):
            _release_pending(project_path=project_path)

        await broadcast_sse("session_update", "refresh")
        return _return_project_row(request, project_path)

    @app.post("/toggle-dangerous-excluded/{project_path:path}")
    async def toggle_dangerous_excluded(request: Request, project_path: str):
        project_path = "/" + project_path
        if not registry.toggle_dangerous_excluded(project_path):
            return JSONResponse({"error": "not found"}, status_code=404)
        await broadcast_sse("session_update", "refresh")
        return _return_project_row(request, project_path)

    @app.post("/remove-project/{project_path:path}")
    async def remove_project(project_path: str):
        project_path = "/" + project_path
        _release_pending(project_path=project_path)
        if not registry.remove_project(project_path):
            return JSONResponse({"error": "not found"}, status_code=400)
        history.remove_project(project_path)
        await broadcast_sse("session_update", "refresh")
        await broadcast_sse("history_update", "refresh")
        return HTMLResponse("")

    @app.delete("/history/{project_path:path}")
    async def clear_history(project_path: str):
        project_path = "/" + project_path
        history.remove_project(project_path)
        await broadcast_sse("history_update", "refresh")
        return HTMLResponse("")

    @app.get("/api/pending")
    async def get_pending():
        return JSONResponse([
            {
                "uuid": req.uuid,
                "session_id": req.session_id,
                "tool_name": req.tool_name,
                "tool_input": req.tool_input,
                "cwd": req.cwd,
                "created_at": req.created_at.isoformat(),
            }
            for req in pending.values()
        ])

    @app.get("/api/sessions")
    async def get_sessions():
        return JSONResponse(registry.list_grouped_sessions())

    @app.get("/events")
    async def sse_events():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        sse_queues.append(queue)

        async def event_generator():
            try:
                while True:
                    data = await queue.get()
                    yield data
            except asyncio.CancelledError:
                pass
            finally:
                sse_queues.remove(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "sessions": registry.list_grouped_sessions(),
                "pending": list(pending.values()),
                "history": history,
                "format_tokens": format_tokens,
            },
        )

    # Store references for testing
    app.state.registry = registry
    app.state.pending = pending
    app.state.history = history

    return app
