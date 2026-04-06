"""History persistence for Neat Claude Monitor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from neat_claude_monitor.models import HistoryEntry, MAX_HISTORY_PER_PROJECT
from neat_claude_monitor.utils import load_json, save_json


class HistoryStore:
    """Manages per-project history entries with JSON file persistence.

    Writes are debounced: ``add()`` marks the store dirty without writing
    to disk.  Call ``flush()`` periodically (or on shutdown) to persist.
    Destructive operations (``remove_project``, ``retain_projects``) flush
    immediately.
    """

    def __init__(self, data_dir: Path) -> None:
        self._file = data_dir / "project_history.json"
        self._entries: dict[str, list[HistoryEntry]] = self._load()
        self._dirty = False

    def _load(self) -> dict[str, list[HistoryEntry]]:
        raw = load_json(self._file)
        if raw is None:
            return {}
        result: dict[str, list[HistoryEntry]] = {}
        for project, entries in raw.items():
            result[project] = [
                HistoryEntry(
                    uuid=e["uuid"],
                    session_id=e["session_id"],
                    tool_name=e["tool_name"],
                    tool_input=e["tool_input"],
                    cwd=e["cwd"],
                    decision=e["decision"],
                    decided_at=datetime.fromisoformat(e["decided_at"]),
                    input_tokens=e.get("input_tokens", 0),
                    output_tokens=e.get("output_tokens", 0),
                )
                for e in entries
            ]
        return result

    def _save(self) -> None:
        raw: dict[str, list[dict]] = {}
        for project, entries in self._entries.items():
            raw[project] = [
                {
                    "uuid": e.uuid,
                    "session_id": e.session_id,
                    "tool_name": e.tool_name,
                    "tool_input": e.tool_input,
                    "cwd": e.cwd,
                    "decision": e.decision,
                    "decided_at": e.decided_at.isoformat(),
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                }
                for e in entries
            ]
        save_json(self._file, raw)
        self._dirty = False

    def flush(self) -> None:
        """Write to disk if there are unsaved changes."""
        if self._dirty:
            self._save()

    def add(
        self,
        uuid: str,
        session_id: str,
        tool_name: str,
        tool_input: dict,
        cwd: str,
        decision: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        entry = HistoryEntry(
            uuid=uuid,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            cwd=cwd,
            decision=decision,
            decided_at=datetime.now(timezone.utc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        project_entries = self._entries.setdefault(cwd, [])
        project_entries.insert(0, entry)
        if len(project_entries) > MAX_HISTORY_PER_PROJECT:
            project_entries.pop()
        self._dirty = True

    def remove_project(self, project_path: str) -> None:
        if self._entries.pop(project_path, None) is not None:
            self._dirty = True
            self.flush()

    def retain_projects(self, valid: set[str]) -> None:
        """Remove history for projects not in the valid set."""
        to_remove = [p for p in self._entries if p not in valid]
        if to_remove:
            for p in to_remove:
                del self._entries[p]
            self._dirty = True
            self.flush()

    def items(self):
        """Return items sorted by most recent entry's decided_at (descending)."""
        return sorted(
            self._entries.items(),
            key=lambda kv: kv[1][0].decided_at if kv[1] else datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __getitem__(self, key: str) -> list[HistoryEntry]:
        return self._entries[key]
