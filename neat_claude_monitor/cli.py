"""CLI entry point for Neat Claude Monitor."""

from __future__ import annotations

import argparse
import time
import webbrowser
from pathlib import Path

import urllib.request
import urllib.error


def get_settings_path() -> Path:
    """Get the path to Claude Code's settings.json."""
    return Path.home() / ".claude" / "settings.json"


def get_data_dir() -> Path:
    """Get the path to Neat Claude Monitor's data directory."""
    return Path(__file__).parent.parent / "data"


def wait_for_server(port: int, timeout: float = 5.0) -> bool:
    """Wait for the server health endpoint to respond."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=1
            )
            if req.status == 200:
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Neat Claude Monitor")
    parser.add_argument("--port", type=int, default=5123, help="Server port (default: 5123)")
    parser.add_argument("command", nargs="?", default="start", choices=["start", "uninstall"],
                        help="Command to run (default: start)")
    args = parser.parse_args()

    settings_path = get_settings_path()

    if args.command == "uninstall":
        from neat_claude_monitor.hooks import uninstall_hooks
        uninstall_hooks(settings_path)
        print("Neat Claude Monitor hooks removed from Claude Code settings.")
        return

    # Install hooks if needed
    from neat_claude_monitor.hooks import install_hooks
    install_hooks(settings_path)

    # Start server
    print(f"Starting Neat Claude Monitor on http://127.0.0.1:{args.port}")

    import uvicorn
    from neat_claude_monitor.server import create_app

    app = create_app(data_dir=get_data_dir())

    # Open browser after a short delay
    def open_browser():
        if wait_for_server(args.port):
            webbrowser.open(f"http://localhost:{args.port}")

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
