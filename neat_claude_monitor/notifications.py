"""macOS notifications via osascript."""

from __future__ import annotations

import asyncio
import subprocess


def _escape_applescript(s: str) -> str:
    """Escape a string for safe use in AppleScript double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _notify_sync(title: str, message: str) -> None:
    safe_title = _escape_applescript(title)
    safe_message = _escape_applescript(message)
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{safe_message}" with title "{safe_title}"',
            ],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pass


async def notify(title: str, message: str) -> None:
    """Send a macOS notification without blocking the event loop.

    Fails silently if osascript is not available.
    """
    await asyncio.to_thread(_notify_sync, title, message)
