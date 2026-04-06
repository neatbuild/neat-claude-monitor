"""Shared utilities for Neat Claude Monitor."""

from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path) -> dict | list | None:
    """Load and parse a JSON file. Returns None if missing or invalid."""
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_json(path: Path, data: dict | list) -> None:
    """Write data as formatted JSON. Creates parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
