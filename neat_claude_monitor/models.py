"""Data models for Neat Claude Monitor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

MAX_HISTORY_PER_PROJECT = 20


STALE_SESSION_SECONDS = 14400  # 4 hours without activity => stale


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    AUTO = "auto"


@dataclass
class SessionInfo:
    """A registered Claude Code session."""

    session_id: str
    project_path: str
    transcript_path: str
    registered_at: datetime
    auto_approving: bool = False
    dangerous_excluded: bool = True
    last_activity: datetime | None = None


@dataclass
class PendingRequest:
    """A tool permission request waiting for user response."""

    uuid: str
    session_id: str
    tool_name: str
    tool_input: dict
    cwd: str
    created_at: datetime
    future: asyncio.Future | None = field(default=None, repr=False)

    @property
    def tool_display(self) -> str:
        """Human-readable summary of the tool call."""
        if self.tool_name == "Bash":
            return self.tool_input.get("command", "Bash")
        elif self.tool_name in ("Edit", "Write", "Read"):
            return self.tool_input.get("file_path", self.tool_name)
        elif self.tool_name == "Grep":
            pattern = self.tool_input.get("pattern", "")
            path = self.tool_input.get("path", "")
            return f'"{pattern}" in {path}' if pattern else "Grep"
        elif self.tool_name == "Glob":
            return self.tool_input.get("pattern", "Glob")
        return self.tool_name


@dataclass
class HistoryEntry:
    """A resolved permission request."""

    uuid: str
    session_id: str
    tool_name: str
    tool_input: dict
    cwd: str
    decision: str  # "allow", "deny", "auto"
    decided_at: datetime
    input_tokens: int = 0
    output_tokens: int = 0
