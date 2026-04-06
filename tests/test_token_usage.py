# tests/test_token_usage.py
import json
from neat_claude_monitor.token_usage import format_tokens, get_transcript_dir, get_last_message_usage, parse_transcripts, ProjectUsage, _calculate_cost


def _write_jsonl(path, entries):
    """Helper: write a list of dicts as JSONL."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _make_assistant_msg(msg_id, model, usage, stop_reason="end_turn"):
    """Helper: create an assistant-type JSONL entry."""
    return {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "model": model,
            "stop_reason": stop_reason,
            "usage": usage,
        },
    }


class TestFormatTokens:
    def test_zero(self):
        assert format_tokens(0) == "0"

    def test_hundreds(self):
        assert format_tokens(500) == "500"

    def test_thousands(self):
        assert format_tokens(1000) == "1.0K"

    def test_thousands_rounded(self):
        assert format_tokens(1550) == "1.6K"

    def test_ten_thousands(self):
        assert format_tokens(74000) == "74K"

    def test_hundred_thousands(self):
        assert format_tokens(568000) == "568K"

    def test_millions(self):
        assert format_tokens(1200000) == "1.2M"

    def test_large_millions(self):
        assert format_tokens(117000000) == "117M"

    def test_small_thousands(self):
        assert format_tokens(999) == "999"


class TestGetTranscriptDir:
    def test_encodes_path(self, tmp_path):
        encoded = tmp_path / "-Users-ji-li-my-project"
        encoded.mkdir()
        result = get_transcript_dir("/Users/ji.li/my_project", claude_dir=tmp_path)
        assert result == encoded

    def test_replaces_slashes(self, tmp_path):
        encoded = tmp_path / "-a-b-c"
        encoded.mkdir()
        result = get_transcript_dir("/a/b/c", claude_dir=tmp_path)
        assert result == encoded

    def test_replaces_dots(self, tmp_path):
        encoded = tmp_path / "-Users-ji-li-proj"
        encoded.mkdir()
        result = get_transcript_dir("/Users/ji.li/proj", claude_dir=tmp_path)
        assert result == encoded

    def test_replaces_underscores(self, tmp_path):
        encoded = tmp_path / "-my-project"
        encoded.mkdir()
        result = get_transcript_dir("/my_project", claude_dir=tmp_path)
        assert result == encoded

    def test_returns_none_when_dir_missing(self, tmp_path):
        result = get_transcript_dir("/nonexistent/path", claude_dir=tmp_path)
        assert result is None

    def test_default_claude_dir(self):
        result = get_transcript_dir("/definitely/nonexistent/project/path")
        assert result is None


class TestParseTranscripts:
    def test_basic_usage(self, tmp_path):
        _write_jsonl(tmp_path / "session1.jsonl", [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 300,
            }),
        ])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.cache_creation_tokens == 200
        assert result.cache_read_tokens == 300
        # Verify cost is calculated (Sonnet: 100*3 + 50*15 + 200*3.75 + 300*0.30) / 1M
        expected_cost = (300 + 750 + 750 + 90) / 1_000_000
        assert abs(result.cost_usd - expected_cost) < 0.0001

    def test_multiple_messages(self, tmp_path):
        _write_jsonl(tmp_path / "session1.jsonl", [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
            _make_assistant_msg("msg2", "claude-sonnet-4-6", {
                "input_tokens": 200, "output_tokens": 100,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 300
        assert result.output_tokens == 150

    def test_multiple_files(self, tmp_path):
        _write_jsonl(tmp_path / "s1.jsonl", [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        _write_jsonl(tmp_path / "s2.jsonl", [
            _make_assistant_msg("msg2", "claude-sonnet-4-6", {
                "input_tokens": 200, "output_tokens": 100,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 300
        assert result.output_tokens == 150

    def test_deduplicates_by_message_id(self, tmp_path):
        msg = _make_assistant_msg("msg1", "claude-sonnet-4-6", {
            "input_tokens": 100, "output_tokens": 50,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        })
        _write_jsonl(tmp_path / "s1.jsonl", [msg, msg])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_skips_entries_without_stop_reason(self, tmp_path):
        _write_jsonl(tmp_path / "s1.jsonl", [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }, stop_reason=None),
            _make_assistant_msg("msg2", "claude-sonnet-4-6", {
                "input_tokens": 200, "output_tokens": 75,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 200
        assert result.output_tokens == 75

    def test_skips_synthetic_model(self, tmp_path):
        _write_jsonl(tmp_path / "s1.jsonl", [
            _make_assistant_msg("msg1", "<synthetic>", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
            _make_assistant_msg("msg2", "claude-sonnet-4-6", {
                "input_tokens": 200, "output_tokens": 75,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 200
        assert result.output_tokens == 75

    def test_skips_non_assistant_entries(self, tmp_path):
        _write_jsonl(tmp_path / "s1.jsonl", [
            {"type": "user", "message": {"content": "hello"}},
            {"type": "progress", "data": {"type": "hook_progress"}},
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 100

    def test_handles_missing_usage_fields(self, tmp_path):
        _write_jsonl(tmp_path / "s1.jsonl", [
            {
                "type": "assistant",
                "message": {
                    "id": "msg1", "model": "claude-sonnet-4-6",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ])
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 100
        assert result.cache_creation_tokens == 0
        assert result.cache_read_tokens == 0

    def test_empty_directory(self, tmp_path):
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cost_usd == 0.0

    def test_malformed_json_lines_skipped(self, tmp_path):
        with open(tmp_path / "s1.jsonl", "w") as f:
            f.write("not json\n")
            f.write(json.dumps(_make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            })) + "\n")
        result = parse_transcripts(tmp_path)
        assert result.input_tokens == 100


class TestGetLastMessageUsage:
    def test_returns_last_assistant_message_usage(self, tmp_path):
        transcript = tmp_path / "session.jsonl"
        _write_jsonl(transcript, [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
            _make_assistant_msg("msg2", "claude-sonnet-4-6", {
                "input_tokens": 5000, "output_tokens": 800,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        inp, out = get_last_message_usage(str(transcript))
        assert inp == 5000
        assert out == 800

    def test_includes_cache_tokens_in_input(self, tmp_path):
        """Input should be sum of input + cache_creation + cache_read tokens."""
        transcript = tmp_path / "session.jsonl"
        _write_jsonl(transcript, [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 1,
                "output_tokens": 170,
                "cache_creation_input_tokens": 295,
                "cache_read_input_tokens": 119387,
            }),
        ])
        inp, out = get_last_message_usage(str(transcript))
        assert inp == 1 + 295 + 119387
        assert out == 170

    def test_skips_entries_without_stop_reason(self, tmp_path):
        transcript = tmp_path / "session.jsonl"
        _write_jsonl(transcript, [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 100, "output_tokens": 50,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
            _make_assistant_msg("msg2", "claude-sonnet-4-6", {
                "input_tokens": 9999, "output_tokens": 9999,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }, stop_reason=None),
        ])
        inp, out = get_last_message_usage(str(transcript))
        assert inp == 100
        assert out == 50

    def test_skips_synthetic_model(self, tmp_path):
        transcript = tmp_path / "session.jsonl"
        _write_jsonl(transcript, [
            _make_assistant_msg("msg1", "claude-sonnet-4-6", {
                "input_tokens": 50, "output_tokens": 50,
                "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30,
            }),
            _make_assistant_msg("msg2", "<synthetic>", {
                "input_tokens": 9999, "output_tokens": 9999,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }),
        ])
        inp, out = get_last_message_usage(str(transcript))
        assert inp == 50 + 20 + 30
        assert out == 50

    def test_returns_zeros_for_missing_file(self):
        inp, out = get_last_message_usage("/nonexistent/path.jsonl")
        assert inp == 0
        assert out == 0

    def test_returns_zeros_for_empty_file(self, tmp_path):
        transcript = tmp_path / "empty.jsonl"
        transcript.write_text("")
        inp, out = get_last_message_usage(str(transcript))
        assert inp == 0
        assert out == 0

    def test_returns_zeros_for_empty_path(self):
        inp, out = get_last_message_usage("")
        assert inp == 0
        assert out == 0


class TestCalculateCost:
    def test_opus_pricing(self):
        cost = _calculate_cost("claude-opus-4-6", 1_000_000, 0, 0, 0)
        assert abs(cost - 15.0) < 0.001

    def test_opus_output_pricing(self):
        cost = _calculate_cost("claude-opus-4-6", 0, 1_000_000, 0, 0)
        assert abs(cost - 75.0) < 0.001

    def test_opus_cache_write_pricing(self):
        cost = _calculate_cost("claude-opus-4-6", 0, 0, 1_000_000, 0)
        assert abs(cost - 18.75) < 0.001

    def test_opus_cache_read_pricing(self):
        cost = _calculate_cost("claude-opus-4-6", 0, 0, 0, 1_000_000)
        assert abs(cost - 1.50) < 0.001

    def test_sonnet_pricing(self):
        cost = _calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000, 0, 0)
        assert abs(cost - 18.0) < 0.001

    def test_sonnet_version_suffix(self):
        cost = _calculate_cost("claude-sonnet-4-5-20250929", 1_000_000, 0, 0, 0)
        assert abs(cost - 3.0) < 0.001

    def test_haiku_pricing(self):
        cost = _calculate_cost("claude-haiku-4-5", 1_000_000, 1_000_000, 0, 0)
        assert abs(cost - 4.80) < 0.001

    def test_unknown_model_uses_sonnet(self):
        cost = _calculate_cost("unknown-model", 1_000_000, 0, 0, 0)
        assert abs(cost - 3.0) < 0.001

    def test_combined_cost(self):
        cost = _calculate_cost("claude-sonnet-4-6", 100, 50, 0, 0)
        expected = (100 * 3 + 50 * 15) / 1_000_000
        assert abs(cost - expected) < 0.0001
