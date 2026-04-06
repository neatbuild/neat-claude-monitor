"""Session registry with project-level persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from neat_claude_monitor.models import STALE_SESSION_SECONDS, SessionInfo
from neat_claude_monitor.token_usage import (
    ProjectUsage,
    format_tokens,
    get_transcript_dir,
    parse_transcripts,
)
from neat_claude_monitor.utils import load_json, save_json

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

SAFE_TOOLS = frozenset(("Read", "Grep", "Glob", "Edit", "Write"))


class Registry:
    """Manages session registration, monitoring state, and persistence.

    All data is stored in a single ``project_settings.json`` file keyed by project path.
    Each project entry holds preferences (auto_approving, dangerous_excluded) and a nested
    sessions dict.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_path = data_dir / "project_settings.json"
        self._sessions: dict[str, SessionInfo] = {}
        self._projects: dict[str, dict] = self._load_projects()
        self._migrate_legacy_files()
        self._restore_sessions()
        self._token_usage: dict[str, ProjectUsage] = {}

    # -- persistence -----------------------------------------------------------

    def _load_projects(self) -> dict[str, dict]:
        # Migrate old filename
        old_path = self._data_dir / "claude_monitor.json"
        if old_path.exists() and not self._data_path.exists():
            old_path.rename(self._data_path)
        return load_json(self._data_path) or {}

    def _save_projects(self) -> None:
        save_json(self._data_path, self._projects)

    _VALID_PROJECT_KEYS = {"auto_approving", "dangerous_excluded", "sessions"}

    def _migrate_legacy_files(self) -> None:
        """One-time migration from old preferences.json + sessions.json."""
        old_prefs = self._data_dir / "preferences.json"
        old_sessions = self._data_dir / "sessions.json"

        changed = False
        prefs = load_json(old_prefs)
        if prefs is not None:
            for project_path, pref in prefs.items():
                proj = self._projects.setdefault(project_path, {})
                proj.setdefault("auto_approving", pref.get("monitored", False))
            changed = True
            old_prefs.unlink()

        sessions_data = load_json(old_sessions)
        if sessions_data is not None:
            for sid, sdata in sessions_data.items():
                pp = sdata.get("project_path", sdata.get("cwd", ""))
                proj = self._projects.setdefault(pp, {})
                proj.setdefault("sessions", {})[sid] = {
                    "transcript_path": sdata.get("transcript_path", ""),
                    "registered_at": sdata.get("registered_at",
                                                datetime.now(timezone.utc).isoformat()),
                }
            changed = True
            old_sessions.unlink()

        # Migrate renamed keys and strip stale ones
        for proj in self._projects.values():
            if "monitored" in proj and "auto_approving" not in proj:
                proj["auto_approving"] = proj["monitored"]
                changed = True
            stale = [k for k in proj if k not in self._VALID_PROJECT_KEYS]
            for k in stale:
                del proj[k]
                changed = True

        if changed:
            self._save_projects()

    def _restore_sessions(self) -> None:
        """Rebuild in-memory sessions from persisted project data."""
        now = datetime.now(timezone.utc)
        for project_path, project in self._projects.items():
            for sid, sdata in project.get("sessions", {}).items():
                session = SessionInfo(
                    session_id=sid,
                    project_path=project_path,
                    transcript_path=sdata.get("transcript_path", ""),
                    registered_at=datetime.fromisoformat(sdata["registered_at"]),
                    auto_approving=project.get("auto_approving", False),
                    dangerous_excluded=project.get("dangerous_excluded", True),
                    last_activity=now,
                )
                self._sessions[sid] = session

    def _sync_sessions_to_disk(self) -> None:
        """Write current session state into the projects dict and save."""
        # Rebuild sessions sub-dicts from in-memory state
        for proj in self._projects.values():
            proj["sessions"] = {}
        for sid, s in self._sessions.items():
            proj = self._projects.get(s.project_path)
            if proj is None:
                continue
            proj.setdefault("sessions", {})[sid] = {
                "transcript_path": s.transcript_path,
                "registered_at": s.registered_at.isoformat(),
            }
        self._save_projects()

    def has_project(self, project_path: str) -> bool:
        """Check if a project exists in the registry."""
        return project_path in self._projects

    @property
    def project_paths(self) -> set[str]:
        """Return the set of all known project paths."""
        return set(self._projects)

    # -- session management ----------------------------------------------------

    def register_session(
        self,
        session_id: str,
        cwd: str,
        transcript_path: str,
    ) -> SessionInfo:
        project_path = cwd
        proj = self._projects.setdefault(project_path, {})

        now = datetime.now(timezone.utc)
        session = SessionInfo(
            session_id=session_id,
            project_path=project_path,
            transcript_path=transcript_path,
            registered_at=now,
            auto_approving=proj.get("auto_approving", False),
            dangerous_excluded=proj.get("dangerous_excluded", True),
            last_activity=now,
        )
        self._sessions[session_id] = session
        self._sync_sessions_to_disk()
        return session

    def deregister_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._sync_sessions_to_disk()

    def get_session(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)

    def get_or_register(self, session_id: str, cwd: str) -> SessionInfo:
        session = self.get_session(session_id)
        if session is None:
            session = self.register_session(session_id, cwd, "")
        return session

    def touch_session(self, session_id: str) -> None:
        """Update last_activity timestamp for a session."""
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_activity = datetime.now(timezone.utc)

    def cleanup_stale(self) -> int:
        """Remove sessions that haven't had activity recently. Returns count removed."""
        now = datetime.now(timezone.utc)
        stale_ids = []
        for sid, session in self._sessions.items():
            if session.last_activity is None:
                continue
            elapsed = (now - session.last_activity).total_seconds()
            if elapsed > STALE_SESSION_SECONDS:
                stale_ids.append(sid)
        for sid in stale_ids:
            self._sessions.pop(sid, None)
        if stale_ids:
            self._sync_sessions_to_disk()
        return len(stale_ids)

    def list_sessions(self) -> list[SessionInfo]:
        return list(self._sessions.values())

    def list_grouped_sessions(self) -> list[dict]:
        """Return one entry per unique project_path with session count.

        Includes all persisted projects, even those with 0 active sessions.
        """
        groups: dict[str, dict] = {}

        # Start with all persisted projects (ensures 0-session projects appear)
        for pp, proj in self._projects.items():
            groups[pp] = {
                "project_path": pp,
                "auto_approving": proj.get("auto_approving", False),
                "dangerous_excluded": proj.get("dangerous_excluded", True),
                "session_count": 0,
                "last_activity": _EPOCH,
            }

        # Count active sessions per project and track most recent activity
        for session in self._sessions.values():
            pp = session.project_path
            if pp not in groups:
                groups[pp] = {
                    "project_path": pp,
                    "auto_approving": session.auto_approving,
                    "dangerous_excluded": session.dangerous_excluded,
                    "session_count": 0,
                    "last_activity": _EPOCH,
                }
            groups[pp]["session_count"] += 1
            activity = session.last_activity or _EPOCH
            if activity > groups[pp]["last_activity"]:
                groups[pp]["last_activity"] = activity

        # Enrich groups with token usage data
        for pp in groups:
            usage = self._token_usage.get(pp)
            if usage:
                groups[pp]["input_tokens"] = format_tokens(usage.input_tokens)
                groups[pp]["output_tokens"] = format_tokens(usage.output_tokens)
                groups[pp]["cost_usd"] = f"${usage.cost_usd:.2f}"
            else:
                groups[pp]["input_tokens"] = "\u2014"
                groups[pp]["output_tokens"] = "\u2014"
                groups[pp]["cost_usd"] = "\u2014"

        # Auto-approving first, then active before inactive, then by recency
        return sorted(
            groups.values(),
            key=lambda g: (
                not g["auto_approving"],
                g["session_count"] == 0,
                -g["last_activity"].timestamp(),
            ),
        )

    # -- toggles ---------------------------------------------------------------

    def toggle_mode(self, project_path: str) -> bool:
        """Toggle auto-approving on/off for a project."""
        proj = self._projects.get(project_path)
        if proj is None:
            return False
        new_state = not proj.get("auto_approving", False)
        proj["auto_approving"] = new_state
        # Reset dangerous_excluded to True when enabling auto-approving
        if new_state:
            proj["dangerous_excluded"] = True
        # Update all active sessions for this project
        for s in self._sessions.values():
            if s.project_path == project_path:
                s.auto_approving = new_state
                if new_state:
                    s.dangerous_excluded = True
        self._save_projects()
        return True

    def toggle_dangerous_excluded(self, project_path: str) -> bool:
        """Toggle whether dangerous tools (Bash, Agent) are excluded from auto-approving."""
        proj = self._projects.get(project_path)
        if proj is None:
            return False
        new_state = not proj.get("dangerous_excluded", True)
        proj["dangerous_excluded"] = new_state
        for s in self._sessions.values():
            if s.project_path == project_path:
                s.dangerous_excluded = new_state
        self._save_projects()
        return True

    def is_project_auto_approving(self, project_path: str) -> bool:
        proj = self._projects.get(project_path)
        return proj is not None and proj.get("auto_approving", False)

    def remove_project(self, project_path: str) -> bool:
        """Remove a project entry and its sessions."""
        if project_path not in self._projects:
            return False
        # Remove active sessions for this project
        for sid in list(self._sessions):
            if self._sessions[sid].project_path == project_path:
                del self._sessions[sid]
        del self._projects[project_path]
        self._save_projects()
        return True

    # -- queries ---------------------------------------------------------------

    def get_token_usage(self, project_path: str) -> ProjectUsage | None:
        return self._token_usage.get(project_path)

    def refresh_token_usage(self) -> None:
        """Re-scan all projects' transcript directories."""
        for project_path in self._projects:
            transcript_dir = get_transcript_dir(project_path)
            if transcript_dir is None:
                continue
            self._token_usage[project_path] = parse_transcripts(transcript_dir)

    def is_auto_approving(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return session is not None and session.auto_approving

    def should_auto_approve(self, session_id: str, tool_name: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        if not session.auto_approving:
            return False
        if session.dangerous_excluded and tool_name not in SAFE_TOOLS:
            return False
        return True
