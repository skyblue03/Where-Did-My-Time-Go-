from timetrace.utils import sanitize_command, format_duration
from timetrace.categorize import categorize

def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(3) == "3s"
    assert format_duration(61) == "1m 01s"
    assert format_duration(3661) == "1h 01m 01s"

def test_sanitize_command_redacts():
    s = sanitize_command(["curl", "--token=abc1234567890abcdefghijklmnopqrstuvwxyzABCDE", "https://x"])
    assert "<redacted>" in s

def test_categorize():
    assert categorize("git status") == "git"
    assert categorize("pytest -q") == "testing"
    assert categorize("npm test") == "testing"
    assert categorize("npm run build") == "build"
