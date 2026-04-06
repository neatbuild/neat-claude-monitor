"""Hook installation and uninstallation for Claude Code."""

from __future__ import annotations

from pathlib import Path

from neat_claude_monitor.utils import load_json, save_json

HOOK_MARKER = "neat-claude-monitor"
_PORT_VAR = "${NEAT_CLAUDE_MONITOR_PORT:-5123}"

HOOK_COMMANDS: dict[str, dict] = {
    "SessionStart": {
        "command": (
            f"curl -s -X POST http://localhost:{_PORT_VAR}/session/start"
            " -H 'Content-Type: application/json' -d @- >/dev/null 2>&1"
            f" # {HOOK_MARKER}"
        ),
        "timeout": 10,
        "async": True,
    },
    "SessionEnd": {
        "command": (
            f"curl -s -X POST http://localhost:{_PORT_VAR}/session/end"
            " -H 'Content-Type: application/json' -d @- >/dev/null 2>&1"
            f" # {HOOK_MARKER}"
        ),
        "timeout": 10,
        "async": True,
    },
    "PreToolUse": {
        "command": (
            f"R=$(curl -s --max-time 300 -X POST http://localhost:{_PORT_VAR}/request"
            " -H 'Content-Type: application/json' -d @- 2>/dev/null)"
            ' && [ -n "$R" ] && echo "$R"'
            f" # {HOOK_MARKER}"
        ),
        "timeout": 300,
    },
}


def install_hooks(settings_path: Path) -> None:
    """Install Neat Claude Monitor hooks into Claude Code settings.

    Merges with existing settings, preserving other hooks.
    """
    settings = load_json(settings_path) or {}

    hooks = settings.setdefault("hooks", {})

    for event_name, config in HOOK_COMMANDS.items():
        hook_obj: dict = {
            "type": "command",
            "command": config["command"],
            "timeout": config["timeout"],
        }
        if config.get("async"):
            hook_obj["async"] = True

        hook_entry = {"matcher": "", "hooks": [hook_obj]}

        existing = hooks.get(event_name, [])
        existing = [h for h in existing if not _is_our_hook(h)]
        existing.append(hook_entry)
        hooks[event_name] = existing

    save_json(settings_path, settings)


def uninstall_hooks(settings_path: Path) -> None:
    """Remove Neat Claude Monitor hooks from Claude Code settings."""
    settings = load_json(settings_path)
    if settings is None:
        return

    hooks = settings.get("hooks", {})

    for event_name in HOOK_COMMANDS:
        if event_name in hooks:
            hooks[event_name] = [
                h for h in hooks[event_name] if not _is_our_hook(h)
            ]
            if not hooks[event_name]:
                del hooks[event_name]

    save_json(settings_path, settings)


def _is_our_hook(hook_entry: dict) -> bool:
    """Check if a hook entry belongs to neat-claude-monitor."""
    for h in hook_entry.get("hooks", []):
        cmd = h.get("command", "")
        if HOOK_MARKER in cmd or "neat_claude_monitor/scripts/" in cmd:
            return True
    return False
