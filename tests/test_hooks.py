import json
from pathlib import Path
from neat_claude_monitor.hooks import install_hooks, uninstall_hooks, HOOK_MARKER


class TestInstallHooks:
    def test_install_to_empty_settings(self, tmp_path):
        settings_file = tmp_path / "settings.json"

        install_hooks(settings_file)

        settings = json.loads(settings_file.read_text())
        assert "hooks" in settings
        assert "SessionStart" in settings["hooks"]
        assert "SessionEnd" in settings["hooks"]
        assert "PreToolUse" in settings["hooks"]

    def test_install_preserves_existing_settings(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "permissions": {"allow": ["Read"]},
            "hooks": {
                "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "echo done"}]}]
            }
        }))

        install_hooks(settings_file)

        settings = json.loads(settings_file.read_text())
        assert settings["permissions"] == {"allow": ["Read"]}
        assert "PostToolUse" in settings["hooks"]
        assert "PreToolUse" in settings["hooks"]

    def test_install_commands_contain_curl(self, tmp_path):
        settings_file = tmp_path / "settings.json"

        install_hooks(settings_file)

        settings = json.loads(settings_file.read_text())
        pre_tool_cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "curl" in pre_tool_cmd
        assert HOOK_MARKER in pre_tool_cmd

    def test_install_sets_correct_timeouts(self, tmp_path):
        settings_file = tmp_path / "settings.json"

        install_hooks(settings_file)

        settings = json.loads(settings_file.read_text())
        pre_tool = settings["hooks"]["PreToolUse"][0]["hooks"][0]
        assert pre_tool["timeout"] == 300

        session_start = settings["hooks"]["SessionStart"][0]["hooks"][0]
        assert session_start["timeout"] == 10
        assert session_start.get("async") is True

    def test_install_is_idempotent(self, tmp_path):
        settings_file = tmp_path / "settings.json"

        install_hooks(settings_file)
        install_hooks(settings_file)

        settings = json.loads(settings_file.read_text())
        # Should have exactly one hook entry per event, not duplicates
        assert len(settings["hooks"]["SessionStart"]) == 1
        assert len(settings["hooks"]["PreToolUse"]) == 1


class TestUninstallHooks:
    def test_uninstall_removes_hooks(self, tmp_path):
        settings_file = tmp_path / "settings.json"

        install_hooks(settings_file)
        uninstall_hooks(settings_file)

        settings = json.loads(settings_file.read_text())
        assert "SessionStart" not in settings.get("hooks", {})
        assert "SessionEnd" not in settings.get("hooks", {})
        assert "PreToolUse" not in settings.get("hooks", {})

    def test_uninstall_preserves_other_hooks(self, tmp_path):
        settings_file = tmp_path / "settings.json"

        install_hooks(settings_file)

        # Add another hook manually
        settings = json.loads(settings_file.read_text())
        settings["hooks"]["PostToolUse"] = [{"matcher": "", "hooks": [{"type": "command", "command": "echo"}]}]
        settings_file.write_text(json.dumps(settings))

        uninstall_hooks(settings_file)

        settings = json.loads(settings_file.read_text())
        assert "PostToolUse" in settings["hooks"]

    def test_uninstall_missing_file_no_error(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        uninstall_hooks(settings_file)  # should not raise
