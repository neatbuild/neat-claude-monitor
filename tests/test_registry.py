import json
from datetime import datetime, timedelta, timezone
from neat_claude_monitor.registry import Registry
from neat_claude_monitor.models import STALE_SESSION_SECONDS


class TestRegistration:
    def test_register_session(self, tmp_path):
        reg = Registry(tmp_path)

        reg.register_session(
            session_id="s1",
            cwd="/projects/notepad",
            transcript_path="/path/to/transcript.jsonl",
        )

        session = reg.get_session("s1")
        assert session is not None
        assert session.project_path == "/projects/notepad"
        assert session.auto_approving is False

    def test_register_loads_saved_preferences(self, tmp_path):
        projects_file = tmp_path / "project_settings.json"
        projects_file.write_text(json.dumps({
            "/projects/notepad": {"auto_approving": True}
        }))
        reg = Registry(tmp_path)

        reg.register_session("s1", "/projects/notepad", "")

        session = reg.get_session("s1")
        assert session.auto_approving is True

    def test_deregister_session(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.deregister_session("s1")
        assert reg.get_session("s1") is None

    def test_deregister_unknown_session_no_error(self, tmp_path):
        reg = Registry(tmp_path)
        reg.deregister_session("unknown")  # should not raise

    def test_list_sessions(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p1", "")
        reg.register_session("s2", "/p2", "")
        sessions = reg.list_sessions()
        assert len(sessions) == 2

    def test_auto_register_on_unknown_request(self, tmp_path):
        reg = Registry(tmp_path)
        session = reg.get_or_register("s1", "/projects/test")
        assert session is not None
        assert session.auto_approving is False


class TestToggles:
    def test_toggle_mode_on(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")

        reg.toggle_mode("/projects/test")
        assert reg.get_session("s1").auto_approving is True

    def test_toggle_mode_off(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")

        reg.toggle_mode("/projects/test")  # On
        reg.toggle_mode("/projects/test")  # Off
        assert reg.get_session("s1").auto_approving is False

    def test_toggle_mode_on_resets_dangerous_excluded(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")

        reg.toggle_mode("/projects/test")  # On
        reg.toggle_dangerous_excluded("/projects/test")  # True -> False
        assert reg.get_session("s1").dangerous_excluded is False

        reg.toggle_mode("/projects/test")  # Off
        reg.toggle_mode("/projects/test")  # On again -> should reset dangerous_excluded to True
        assert reg.get_session("s1").dangerous_excluded is True

    def test_toggle_dangerous_excluded(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        assert reg.get_session("s1").dangerous_excluded is True  # default on

        reg.toggle_dangerous_excluded("/projects/test")
        assert reg.get_session("s1").dangerous_excluded is False

        reg.toggle_dangerous_excluded("/projects/test")
        assert reg.get_session("s1").dangerous_excluded is True

    def test_toggle_mode_saves_preferences(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.toggle_mode("/projects/test")

        saved = json.loads((tmp_path / "project_settings.json").read_text())
        assert saved["/projects/test"]["auto_approving"] is True

    def test_toggle_dangerous_excluded_saves_preferences(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.toggle_dangerous_excluded("/projects/test")  # default on -> off

        saved = json.loads((tmp_path / "project_settings.json").read_text())
        assert saved["/projects/test"]["dangerous_excluded"] is False

    def test_preferences_use_absolute_paths(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/Users/me/Projects/test", "")
        reg.toggle_mode("/Users/me/Projects/test")

        saved = json.loads((tmp_path / "project_settings.json").read_text())
        assert "/Users/me/Projects/test" in saved
        for key in saved:
            assert not key.startswith("~")


class TestGroupedSessions:
    def test_list_grouped_deduplicates_by_project(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.register_session("s2", "/projects/test", "")
        reg.register_session("s3", "/projects/other", "")

        groups = reg.list_grouped_sessions()
        assert len(groups) == 2
        by_path = {g["project_path"]: g for g in groups}
        assert by_path["/projects/test"]["session_count"] == 2
        assert by_path["/projects/other"]["session_count"] == 1

    def test_toggle_mode_applies_to_all_sessions_in_project(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.register_session("s2", "/projects/test", "")
        reg.register_session("s3", "/projects/other", "")

        reg.toggle_mode("/projects/test")

        assert reg.get_session("s1").auto_approving is True
        assert reg.get_session("s2").auto_approving is True
        assert reg.get_session("s3").auto_approving is False

    def test_toggle_dangerous_excluded_applies_to_all_sessions_in_project(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.register_session("s2", "/projects/test", "")

        reg.toggle_dangerous_excluded("/projects/test")  # default on -> off

        assert reg.get_session("s1").dangerous_excluded is False
        assert reg.get_session("s2").dangerous_excluded is False

    def test_grouped_includes_projects_with_zero_sessions(self, tmp_path):
        projects_file = tmp_path / "project_settings.json"
        projects_file.write_text(json.dumps({
            "/projects/old": {"auto_approving": True}
        }))
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/active", "")

        groups = reg.list_grouped_sessions()
        by_path = {g["project_path"]: g for g in groups}
        assert len(groups) == 2
        assert by_path["/projects/old"]["session_count"] == 0
        assert by_path["/projects/old"]["auto_approving"] is True
        assert by_path["/projects/active"]["session_count"] == 1

    def test_remove_project_with_no_sessions(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.deregister_session("s1")

        assert reg.remove_project("/projects/test") is True
        groups = reg.list_grouped_sessions()
        assert len(groups) == 0

    def test_remove_project_with_active_sessions(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        reg.toggle_mode("/projects/test")  # auto_approving on

        assert reg.remove_project("/projects/test") is True
        # Active sessions are removed along with the project
        assert reg.get_session("s1") is None

    def test_remove_unknown_project(self, tmp_path):
        reg = Registry(tmp_path)
        assert reg.remove_project("/nonexistent") is False

    def test_grouped_sorts_by_most_recent_activity(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/alpha", "")
        reg.register_session("s2", "/projects/charlie", "")
        reg.register_session("s3", "/projects/bravo", "")

        # Touch charlie most recently, then alpha — bravo stays oldest
        reg.touch_session("s2")  # charlie
        reg.touch_session("s1")  # alpha

        groups = reg.list_grouped_sessions()
        paths = [g["project_path"] for g in groups]
        # Most recently active first
        assert paths == ["/projects/alpha", "/projects/charlie", "/projects/bravo"]

    def test_grouped_sorts_auto_approving_before_non_auto_approving(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/alpha", "")
        reg.register_session("s2", "/projects/beta", "")
        reg.register_session("s3", "/projects/gamma", "")

        # alpha and gamma are auto-approving, beta is not
        reg.toggle_mode("/projects/alpha")
        reg.toggle_mode("/projects/gamma")

        # beta is most recently active (would sort first without grouping)
        reg.touch_session("s2")

        groups = reg.list_grouped_sessions()
        paths = [g["project_path"] for g in groups]
        # Auto-approving projects should come before non-auto-approving
        # Within auto-approving group, gamma was registered more recently
        assert paths == ["/projects/gamma", "/projects/alpha", "/projects/beta"]

    def test_grouped_sorts_active_before_inactive_then_by_recency(self, tmp_path):
        projects_file = tmp_path / "project_settings.json"
        projects_file.write_text(json.dumps({
            "/projects/old_inactive": {"auto_approving": False}
        }))
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/beta", "")
        reg.register_session("s2", "/projects/alpha", "")

        # beta is more recently active
        reg.touch_session("s1")

        groups = reg.list_grouped_sessions()
        paths = [g["project_path"] for g in groups]
        # Active projects by recency first, then inactive
        assert paths == ["/projects/beta", "/projects/alpha", "/projects/old_inactive"]


class TestAutoApprove:
    def test_should_auto_approve_all_tools(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p", "")
        reg.get_session("s1").auto_approving = True
        reg.get_session("s1").dangerous_excluded = False

        assert reg.should_auto_approve("s1", "Read") is True
        assert reg.should_auto_approve("s1", "Grep") is True
        assert reg.should_auto_approve("s1", "Glob") is True
        assert reg.should_auto_approve("s1", "Edit") is True
        assert reg.should_auto_approve("s1", "Bash") is True
        assert reg.should_auto_approve("s1", "Write") is True
        assert reg.should_auto_approve("s1", "Agent") is True

    def test_should_not_auto_approve_when_not_auto_approving(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p", "")

        assert reg.should_auto_approve("s1", "Read") is False

    def test_dangerous_excluded_blocks_non_safe_tools(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p", "")
        reg.get_session("s1").auto_approving = True
        reg.get_session("s1").dangerous_excluded = True

        # Safe tools still auto-approved
        assert reg.should_auto_approve("s1", "Read") is True
        assert reg.should_auto_approve("s1", "Grep") is True
        assert reg.should_auto_approve("s1", "Glob") is True
        assert reg.should_auto_approve("s1", "Edit") is True
        assert reg.should_auto_approve("s1", "Write") is True

        # Dangerous tools blocked
        assert reg.should_auto_approve("s1", "Bash") is False
        assert reg.should_auto_approve("s1", "Agent") is False

        # Unknown tools also blocked
        assert reg.should_auto_approve("s1", "CustomTool") is False

    def test_is_auto_approving(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p", "")
        assert reg.is_auto_approving("s1") is False

        reg.get_session("s1").auto_approving = True
        assert reg.is_auto_approving("s1") is True

    def test_is_auto_approving_unknown_session(self, tmp_path):
        reg = Registry(tmp_path)
        assert reg.is_auto_approving("unknown") is False


class TestStaleSessionCleanup:
    def test_cleanup_removes_stale_sessions(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p1", "")
        # Artificially age the session
        reg.get_session("s1").last_activity = datetime.now(timezone.utc) - timedelta(
            seconds=STALE_SESSION_SECONDS + 10
        )

        removed = reg.cleanup_stale()
        assert removed == 1
        assert reg.get_session("s1") is None

    def test_cleanup_keeps_active_sessions(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p1", "")
        # Just registered, so last_activity is now

        removed = reg.cleanup_stale()
        assert removed == 0
        assert reg.get_session("s1") is not None

    def test_touch_session_resets_staleness(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p1", "")
        # Age it
        reg.get_session("s1").last_activity = datetime.now(timezone.utc) - timedelta(
            seconds=STALE_SESSION_SECONDS + 10
        )
        # Touch it
        reg.touch_session("s1")

        removed = reg.cleanup_stale()
        assert removed == 0
        assert reg.get_session("s1") is not None

    def test_cleanup_mixed_sessions(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/p1", "")
        reg.register_session("s2", "/p1", "")
        reg.register_session("s3", "/p2", "")

        # Age s1 and s2, keep s3 active
        stale_time = datetime.now(timezone.utc) - timedelta(
            seconds=STALE_SESSION_SECONDS + 10
        )
        reg.get_session("s1").last_activity = stale_time
        reg.get_session("s2").last_activity = stale_time

        removed = reg.cleanup_stale()
        assert removed == 2
        assert reg.get_session("s1") is None
        assert reg.get_session("s2") is None
        assert reg.get_session("s3") is not None

    def test_touch_unknown_session_no_error(self, tmp_path):
        reg = Registry(tmp_path)
        reg.touch_session("unknown")  # should not raise


class TestSessionPersistence:
    def test_sessions_survive_restart(self, tmp_path):
        reg1 = Registry(tmp_path)
        reg1.register_session("s1", "/projects/test", "/transcript.jsonl")

        # Create a new registry (simulates server restart)
        reg2 = Registry(tmp_path)
        session = reg2.get_session("s1")
        assert session is not None
        assert session.project_path == "/projects/test"
        assert session.transcript_path == "/transcript.jsonl"

    def test_deregister_removes_from_disk(self, tmp_path):
        reg1 = Registry(tmp_path)
        reg1.register_session("s1", "/projects/test", "")
        reg1.deregister_session("s1")

        reg2 = Registry(tmp_path)
        assert reg2.get_session("s1") is None

    def test_persisted_sessions_get_fresh_last_activity(self, tmp_path):
        reg1 = Registry(tmp_path)
        reg1.register_session("s1", "/p", "")
        # Age the session
        reg1.get_session("s1").last_activity = datetime.now(timezone.utc) - timedelta(
            seconds=STALE_SESSION_SECONDS + 10
        )

        # Restart — persisted sessions should get fresh last_activity
        reg2 = Registry(tmp_path)
        session = reg2.get_session("s1")
        assert session is not None
        removed = reg2.cleanup_stale()
        assert removed == 0

    def test_persisted_sessions_load_preferences(self, tmp_path):
        reg1 = Registry(tmp_path)
        reg1.register_session("s1", "/projects/test", "")
        reg1.toggle_mode("/projects/test")

        reg2 = Registry(tmp_path)
        session = reg2.get_session("s1")
        assert session.auto_approving is True

    def test_persisted_dangerous_excluded_survives_restart(self, tmp_path):
        reg1 = Registry(tmp_path)
        reg1.register_session("s1", "/projects/test", "")
        reg1.toggle_dangerous_excluded("/projects/test")  # default True -> False

        reg2 = Registry(tmp_path)
        session = reg2.get_session("s1")
        assert session.dangerous_excluded is False

    def test_cleanup_stale_updates_disk(self, tmp_path):
        reg1 = Registry(tmp_path)
        reg1.register_session("s1", "/p", "")
        reg1.get_session("s1").last_activity = datetime.now(timezone.utc) - timedelta(
            seconds=STALE_SESSION_SECONDS + 10
        )
        reg1.cleanup_stale()

        reg2 = Registry(tmp_path)
        assert reg2.get_session("s1") is None


from unittest.mock import patch
from neat_claude_monitor.token_usage import ProjectUsage


class TestTokenUsage:
    def test_get_token_usage_returns_none_when_not_cached(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")
        assert reg.get_token_usage("/projects/test") is None

    def test_refresh_token_usage_populates_cache(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")

        mock_usage = ProjectUsage(
            input_tokens=100, output_tokens=50,
            cache_creation_tokens=200, cache_read_tokens=300,
            cost_usd=1.23,
        )
        with patch("neat_claude_monitor.registry.get_transcript_dir") as mock_dir, \
             patch("neat_claude_monitor.registry.parse_transcripts") as mock_parse:
            mock_dir.return_value = tmp_path
            mock_parse.return_value = mock_usage
            reg.refresh_token_usage()

        result = reg.get_token_usage("/projects/test")
        assert result is not None
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.cost_usd == 1.23

    def test_refresh_token_usage_skips_missing_dir(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")

        with patch("neat_claude_monitor.registry.get_transcript_dir") as mock_dir:
            mock_dir.return_value = None
            reg.refresh_token_usage()

        assert reg.get_token_usage("/projects/test") is None

    def test_list_grouped_includes_token_usage(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register_session("s1", "/projects/test", "")

        mock_usage = ProjectUsage(
            input_tokens=74000, output_tokens=568000,
            cache_creation_tokens=0, cache_read_tokens=0,
            cost_usd=2.30,
        )
        with patch("neat_claude_monitor.registry.get_transcript_dir") as mock_dir, \
             patch("neat_claude_monitor.registry.parse_transcripts") as mock_parse:
            mock_dir.return_value = tmp_path
            mock_parse.return_value = mock_usage
            reg.refresh_token_usage()

        groups = reg.list_grouped_sessions()
        group = groups[0]
        assert group["input_tokens"] == "74K"
        assert group["output_tokens"] == "568K"
        assert group["cost_usd"] == "$2.30"
