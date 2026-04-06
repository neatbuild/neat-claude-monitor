from datetime import datetime, timezone
from neat_claude_monitor.models import SessionInfo, PendingRequest, HistoryEntry


class TestSessionInfo:
    def test_create_session(self):
        session = SessionInfo(
            session_id="sess_1",
            project_path="/Users/me/Projects/notepad",
            transcript_path="/Users/me/.claude/projects/hash/sess_1.jsonl",
            auto_approving=False,
            registered_at=datetime(2026, 3, 14, tzinfo=timezone.utc),
        )
        assert session.session_id == "sess_1"
        assert session.auto_approving is False

    def test_defaults(self):
        now = datetime(2026, 3, 14, tzinfo=timezone.utc)
        session = SessionInfo(
            session_id="sess_2",
            project_path="/projects/test",
            transcript_path="",
            registered_at=now,
        )
        assert session.auto_approving is False


class TestPendingRequest:
    def test_create_pending_request(self):
        req = PendingRequest(
            uuid="req_1",
            session_id="sess_1",
            tool_name="Bash",
            tool_input={"command": "npm test"},
            cwd="/projects/test",
            created_at=datetime(2026, 3, 14, tzinfo=timezone.utc),
        )
        assert req.uuid == "req_1"
        assert req.tool_name == "Bash"
        assert req.tool_input == {"command": "npm test"}
        assert req.future is None

    def test_tool_display_bash(self):
        req = PendingRequest(
            uuid="r1", session_id="s1", tool_name="Bash",
            tool_input={"command": "git status"},
            cwd="/p", created_at=datetime.now(timezone.utc),
        )
        assert req.tool_display == "git status"

    def test_tool_display_edit(self):
        req = PendingRequest(
            uuid="r1", session_id="s1", tool_name="Edit",
            tool_input={"file_path": "/src/main.py", "old_string": "a", "new_string": "b"},
            cwd="/p", created_at=datetime.now(timezone.utc),
        )
        assert req.tool_display == "/src/main.py"

    def test_tool_display_read(self):
        req = PendingRequest(
            uuid="r1", session_id="s1", tool_name="Read",
            tool_input={"file_path": "/src/main.py"},
            cwd="/p", created_at=datetime.now(timezone.utc),
        )
        assert req.tool_display == "/src/main.py"

    def test_tool_display_grep(self):
        req = PendingRequest(
            uuid="r1", session_id="s1", tool_name="Grep",
            tool_input={"pattern": "TODO", "path": "/src"},
            cwd="/p", created_at=datetime.now(timezone.utc),
        )
        assert req.tool_display == '"TODO" in /src'

    def test_tool_display_glob(self):
        req = PendingRequest(
            uuid="r1", session_id="s1", tool_name="Glob",
            tool_input={"pattern": "**/*.py"},
            cwd="/p", created_at=datetime.now(timezone.utc),
        )
        assert req.tool_display == "**/*.py"

    def test_tool_display_write(self):
        req = PendingRequest(
            uuid="r1", session_id="s1", tool_name="Write",
            tool_input={"file_path": "/src/new_file.py", "content": "hello"},
            cwd="/p", created_at=datetime.now(timezone.utc),
        )
        assert req.tool_display == "/src/new_file.py"

    def test_tool_display_unknown(self):
        req = PendingRequest(
            uuid="r1", session_id="s1", tool_name="CustomTool",
            tool_input={"some": "data"},
            cwd="/p", created_at=datetime.now(timezone.utc),
        )
        assert req.tool_display == "CustomTool"


class TestHistoryEntry:
    def test_create_history_entry(self):
        entry = HistoryEntry(
            uuid="req_1",
            session_id="sess_1",
            tool_name="Bash",
            tool_input={"command": "npm test"},
            cwd="/projects/test",
            decision="allow",
            decided_at=datetime(2026, 3, 14, tzinfo=timezone.utc),
        )
        assert entry.decision == "allow"


class TestHistoryStoreOrder:
    def test_items_sorted_by_most_recent_activity(self, tmp_path):
        from neat_claude_monitor.history import HistoryStore

        store = HistoryStore(tmp_path)
        # Add entries: project_a has older activity, project_b has newer
        store.add("u1", "s1", "Read", {}, "/project_a", "auto")
        import time; time.sleep(0.01)
        store.add("u2", "s2", "Read", {}, "/project_b", "auto")

        items = list(store.items())
        # project_b should come first (most recent activity)
        assert items[0][0] == "/project_b"
        assert items[1][0] == "/project_a"

    def test_items_sorted_after_new_entry_to_older_project(self, tmp_path):
        from neat_claude_monitor.history import HistoryStore

        store = HistoryStore(tmp_path)
        store.add("u1", "s1", "Read", {}, "/project_a", "auto")
        import time; time.sleep(0.01)
        store.add("u2", "s2", "Read", {}, "/project_b", "auto")
        time.sleep(0.01)
        # Now add a newer entry to project_a
        store.add("u3", "s1", "Edit", {}, "/project_a", "allow")

        items = list(store.items())
        # project_a should now come first (most recent activity)
        assert items[0][0] == "/project_a"
        assert items[1][0] == "/project_b"
