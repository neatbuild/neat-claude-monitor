"""Token usage parsing and cost calculation for Claude Code transcripts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectUsage:
    """Aggregated token usage and cost for a project."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0


def format_tokens(count: int) -> str:
    """Format token count for display: 500, 1.2K, 74K, 1.2M, 117M."""
    if count < 1000:
        return str(count)
    if count < 10_000:
        return f"{count / 1000:.1f}K"
    if count < 1_000_000:
        return f"{count // 1000}K"
    if count < 10_000_000:
        return f"{count / 1_000_000:.1f}M"
    return f"{count // 1_000_000}M"


_DEFAULT_CLAUDE_DIR = Path.home() / ".claude" / "projects"


def get_transcript_dir(
    project_path: str,
    claude_dir: Path | None = None,
) -> Path | None:
    """Convert a project path to its Claude transcript directory.

    Claude Code encodes paths by replacing '/', '.', and '_' with '-'.
    Returns None if the directory does not exist.
    """
    if claude_dir is None:
        claude_dir = _DEFAULT_CLAUDE_DIR
    encoded = project_path.replace("/", "-").replace(".", "-").replace("_", "-")
    transcript_dir = claude_dir / encoded
    if transcript_dir.is_dir():
        return transcript_dir
    return None


# Pricing per 1M tokens: (input, output, cache_write, cache_read)
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus": (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku": (0.80, 4.0, 1.0, 0.08),
}
_DEFAULT_PRICING = _PRICING["claude-sonnet"]


def _get_pricing(model: str) -> tuple[float, float, float, float]:
    """Get pricing tuple for a model using prefix matching."""
    for prefix, pricing in _PRICING.items():
        if model.startswith(prefix):
            return pricing
    return _DEFAULT_PRICING


def _calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
) -> float:
    """Calculate cost in USD for a single message."""
    p_in, p_out, p_cw, p_cr = _get_pricing(model)
    return (
        input_tokens * p_in
        + output_tokens * p_out
        + cache_creation_tokens * p_cw
        + cache_read_tokens * p_cr
    ) / 1_000_000


def _iter_assistant_messages(f):
    """Yield (model, usage_dict) for each valid assistant message in a JSONL stream."""
    for line in f:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        if not msg.get("stop_reason"):
            continue
        model = msg.get("model", "")
        if model == "<synthetic>":
            continue
        yield model, msg.get("id", ""), msg.get("usage", {})


def get_last_message_usage(transcript_path: str) -> tuple[int, int]:
    """Read the last valid assistant message's token usage from a JSONL transcript.

    Returns (input_tokens, output_tokens). Returns (0, 0) if the file
    doesn't exist, is empty, or contains no valid messages.
    """
    if not transcript_path:
        return 0, 0
    path = Path(transcript_path)
    if not path.is_file():
        return 0, 0

    last_input = 0
    last_output = 0
    try:
        with open(path, "r") as f:
            for _model, _msg_id, u in _iter_assistant_messages(f):
                last_input = (u.get("input_tokens", 0)
                              + u.get("cache_creation_input_tokens", 0)
                              + u.get("cache_read_input_tokens", 0))
                last_output = u.get("output_tokens", 0)
    except OSError:
        return 0, 0
    return last_input, last_output


def parse_transcripts(transcript_dir: Path) -> ProjectUsage:
    """Read all JSONL transcript files and aggregate token usage."""
    usage = ProjectUsage()
    seen_ids: set[str] = set()

    for jsonl_file in transcript_dir.glob("*.jsonl"):
        with open(jsonl_file, "r") as f:
            for model, msg_id, u in _iter_assistant_messages(f):
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                input_tokens = u.get("input_tokens", 0)
                output_tokens = u.get("output_tokens", 0)
                cache_creation = u.get("cache_creation_input_tokens", 0)
                cache_read = u.get("cache_read_input_tokens", 0)

                usage.input_tokens += input_tokens
                usage.output_tokens += output_tokens
                usage.cache_creation_tokens += cache_creation
                usage.cache_read_tokens += cache_read
                usage.cost_usd += _calculate_cost(
                    model, input_tokens, output_tokens, cache_creation, cache_read,
                )

    return usage
