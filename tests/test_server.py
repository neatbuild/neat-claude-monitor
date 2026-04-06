import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
from neat_claude_monitor.server import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(data_dir=tmp_path)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "running"}


@pytest.mark.asyncio
class TestSessionEndpoints:
    async def test_register_session(self, client):
        resp = await client.post("/session/start", json={
            "session_id": "s1",
            "cwd": "/projects/test",
            "permission_mode": "default",
            "transcript_path": "/path/to/transcript.jsonl",
        })
        assert resp.status_code == 200

    async def test_deregister_session(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        resp = await client.post("/session/end", json={"session_id": "s1"})
        assert resp.status_code == 200

    async def test_deregister_unknown_session(self, client):
        resp = await client.post("/session/end", json={"session_id": "unknown"})
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestRequestEndpoint:
    async def test_non_auto_approving_session_returns_empty(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        resp = await client.post("/request", json={
            "session_id": "s1", "tool_name": "Bash",
            "tool_input": {"command": "ls"}, "cwd": "/p",
            "hook_event_name": "PreToolUse",
        })
        assert resp.status_code == 200
        assert resp.json() == {}

    async def test_unknown_session_returns_empty(self, client):
        resp = await client.post("/request", json={
            "session_id": "unknown", "tool_name": "Bash",
            "tool_input": {"command": "ls"}, "cwd": "/p",
            "hook_event_name": "PreToolUse",
        })
        assert resp.status_code == 200
        assert resp.json() == {}

    async def test_auto_approve_safe_tool(self, client, app):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        resp = await client.post("/request", json={
            "session_id": "s1", "tool_name": "Read",
            "tool_input": {"file_path": "/src/main.py"}, "cwd": "/p",
            "hook_event_name": "PreToolUse",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["hookSpecificOutput"]["permissionDecision"] == "allow"

    async def test_auto_approve_edit_tool(self, client, app):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        resp = await client.post("/request", json={
            "session_id": "s1", "tool_name": "Edit",
            "tool_input": {"file_path": "/src/main.py", "old_string": "a", "new_string": "b"},
            "cwd": "/p", "hook_event_name": "PreToolUse",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["hookSpecificOutput"]["permissionDecision"] == "allow"

    async def test_dangerous_excluded_blocks_then_respond(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        # Start request in background (it will block)
        async def make_request():
            return await client.post("/request", json={
                "session_id": "s1", "tool_name": "Bash",
                "tool_input": {"command": "npm test"}, "cwd": "/p",
                "hook_event_name": "PreToolUse",
            })

        task = asyncio.create_task(make_request())

        # Give the server time to register the pending request
        await asyncio.sleep(0.1)

        # Get pending requests to find UUID
        resp = await client.get("/api/pending")
        pending = resp.json()
        assert len(pending) == 1
        req_uuid = pending[0]["uuid"]

        # Respond
        respond_resp = await client.post(f"/respond/{req_uuid}/allow")
        assert respond_resp.status_code == 200
        assert "Approved" in respond_resp.text

        # Original request should now complete
        result = await task
        assert result.status_code == 200
        body = result.json()
        assert body["hookSpecificOutput"]["permissionDecision"] == "allow"

    async def test_deny_request(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        async def make_request():
            return await client.post("/request", json={
                "session_id": "s1", "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"}, "cwd": "/p",
                "hook_event_name": "PreToolUse",
            })

        task = asyncio.create_task(make_request())
        await asyncio.sleep(0.1)

        resp = await client.get("/api/pending")
        req_uuid = resp.json()[0]["uuid"]
        respond_resp = await client.post(f"/respond/{req_uuid}/deny")
        assert respond_resp.status_code == 200
        assert "Denied" in respond_resp.text

        result = await task
        body = result.json()
        assert body["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
class TestToggleEndpoints:
    async def test_toggle_mode(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        resp = await client.post("/toggle-mode/p")
        assert resp.status_code == 200

    async def test_toggle_dangerous_excluded(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        resp = await client.post("/toggle-dangerous-excluded/p")
        assert resp.status_code == 200

    async def test_remove_project_no_sessions(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/session/end", json={"session_id": "s1"})
        resp = await client.post("/remove-project/p")
        assert resp.status_code == 200

    async def test_remove_project_with_active_sessions(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        resp = await client.post("/remove-project/p")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestClearHistory:
    async def test_clear_history_removes_entries(self, client):
        """DELETE /history/{path} removes history for that path without unregistering the project."""
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        # Generate a history entry
        await client.post("/request", json={
            "session_id": "s1", "tool_name": "Read",
            "tool_input": {"file_path": "/src/main.py"}, "cwd": "/p",
            "hook_event_name": "PreToolUse",
        })

        # History should have entries
        assert len(client._transport.app.state.history["/p"]) > 0

        # Clear history
        resp = await client.delete("/history/p")
        assert resp.status_code == 200

        # History should be empty
        history = client._transport.app.state.history
        assert "/p" not in history._entries

        # Project should still be registered
        registry = client._transport.app.state.registry
        assert "/p" in registry.project_paths

    async def test_clear_history_nonexistent_path(self, client):
        """DELETE /history/{path} returns 200 even if no history exists for that path."""
        resp = await client.delete("/history/nonexistent")
        assert resp.status_code == 200

    async def test_clear_history_button_in_ui(self, client):
        """History group summary should contain a clear-history button."""
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        await client.post("/request", json={
            "session_id": "s1", "tool_name": "Read",
            "tool_input": {"file_path": "/src/main.py"}, "cwd": "/p",
            "hook_event_name": "PreToolUse",
        })

        resp = await client.get("/")
        assert resp.status_code == 200
        assert "clear-history" in resp.text.lower() or "hx-delete" in resp.text


@pytest.mark.asyncio
class TestHistoryCap:
    async def test_history_capped_per_project(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        # Send 30 auto-approved Read requests
        for i in range(30):
            await client.post("/request", json={
                "session_id": "s1", "tool_name": "Read",
                "tool_input": {"file_path": f"/file_{i}.py"}, "cwd": "/p",
                "hook_event_name": "PreToolUse",
            })

        # History should be capped at 20 per project
        assert len(client._transport.app.state.history["/p"]) <= 20


@pytest.mark.asyncio
class TestIndexPage:
    async def test_index_returns_html(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Neat Claude Monitor" in resp.text

    async def test_history_entry_shows_token_usage(self, client, app, tmp_path):
        """Each history entry should display input/output tokens from the transcript."""
        import json

        # Create a fake transcript file with token usage
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg1",
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1234, "output_tokens": 567},
            },
        }) + "\n")

        # Register session with the transcript path
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default",
            "transcript_path": str(transcript),
        })
        await client.post("/toggle-mode/p")

        # Auto-approve a safe tool -> creates history entry with token data
        await client.post("/request", json={
            "session_id": "s1", "tool_name": "Read",
            "tool_input": {"file_path": "/src/main.py"}, "cwd": "/p",
            "hook_event_name": "PreToolUse",
        })

        resp = await client.get("/")
        assert resp.status_code == 200
        # Per-entry output token info should appear as a badge
        assert "567 tokens" in resp.text


@pytest.mark.asyncio
class TestSessionEndCancelsPending:
    async def test_session_end_cancels_pending_request(self, client):
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        async def make_request():
            return await client.post("/request", json={
                "session_id": "s1", "tool_name": "Bash",
                "tool_input": {"command": "test"}, "cwd": "/p",
                "hook_event_name": "PreToolUse",
            })

        task = asyncio.create_task(make_request())
        await asyncio.sleep(0.1)

        # End session -> should cancel pending
        await client.post("/session/end", json={"session_id": "s1"})

        result = await task
        body = result.json()
        assert body["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
class TestToggleModeReleasesPending:
    async def test_toggle_mode_off_releases_pending_requests(self, client):
        """When monitoring is turned OFF, pending requests should be allowed so sessions don't hang."""
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        async def make_request():
            return await client.post("/request", json={
                "session_id": "s1", "tool_name": "Bash",
                "tool_input": {"command": "echo hi"}, "cwd": "/p",
                "hook_event_name": "PreToolUse",
            })

        task = asyncio.create_task(make_request())
        await asyncio.sleep(0.1)

        # Verify the request is pending
        resp = await client.get("/api/pending")
        assert len(resp.json()) == 1

        # Toggle mode OFF -> should release pending request with "allow"
        await client.post("/toggle-mode/p")

        result = await task
        body = result.json()
        assert body["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.asyncio
class TestDetailsPersistAcrossRefresh:
    """Details elements must have stable IDs so idiomorph preserves open state on morph swaps."""

    async def test_history_card_has_stable_id(self, client):
        """History <details> elements render with id='history-{uuid}' for morph stability."""
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        # Generate a history entry via auto-approve
        await client.post("/request", json={
            "session_id": "s1", "tool_name": "Read",
            "tool_input": {"file_path": "/src/main.py"}, "cwd": "/p",
            "hook_event_name": "PreToolUse",
        })

        # Fetch the page twice and verify IDs are stable
        resp1 = await client.get("/")
        resp2 = await client.get("/")

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # Extract the history details ID
        import re
        ids1 = re.findall(r'id="history-([^"]+)"', resp1.text)
        ids2 = re.findall(r'id="history-([^"]+)"', resp2.text)

        assert len(ids1) >= 1, "History card should render with an id"
        assert ids1 == ids2, "History card IDs should be stable across re-renders"

    async def test_pending_card_has_stable_id(self, client):
        """Pending request cards render with id='req-{uuid}' for morph stability."""
        await client.post("/session/start", json={
            "session_id": "s1", "cwd": "/p", "permission_mode": "default", "transcript_path": "",
        })
        await client.post("/toggle-mode/p")

        # Create a pending request (don't resolve it)
        async def make_request():
            return await client.post("/request", json={
                "session_id": "s1", "tool_name": "Bash",
                "tool_input": {"command": "echo test"}, "cwd": "/p",
                "hook_event_name": "PreToolUse",
            })

        task = asyncio.create_task(make_request())
        await asyncio.sleep(0.1)

        # Fetch the page twice and verify IDs are stable
        resp1 = await client.get("/")
        resp2 = await client.get("/")

        import re
        ids1 = re.findall(r'id="req-([0-9a-f-]+)"', resp1.text)
        ids2 = re.findall(r'id="req-([0-9a-f-]+)"', resp2.text)

        assert len(ids1) >= 1, "Pending card should render with an id"
        assert ids1 == ids2, "Pending card IDs should be stable across re-renders"

        # Clean up: resolve the pending request
        pending = (await client.get("/api/pending")).json()
        await client.post(f"/respond/{pending[0]['uuid']}/allow")
        await task

    async def test_page_includes_details_open_preservation(self, client):
        """Page must include JS that preserves <details> open state across idiomorph swaps."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "htmx:beforeSwap" in resp.text
        assert "_openDetailIds" in resp.text

    async def test_history_project_details_excluded_from_open_preservation(self, client):
        """History project <details> should NOT be preserved across swaps so server-rendered 'first only' rule applies."""
        resp = await client.get("/")
        assert resp.status_code == 200
        # The JS should filter out history-project IDs from preservation
        assert "history-project" in resp.text  # filter reference exists
        # The filter should exclude, not include, history-project IDs
        assert ".filter(" in resp.text or ".filter((" in resp.text
