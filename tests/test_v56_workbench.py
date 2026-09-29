import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from term5.state.artifacts import ArtifactStore
from term5.state.episodes import EpisodeStore
from term5.ui.web import _persistent_events, _journal_history_days, _journal_day
from term5.ui.web_assets import HTML, CSS, JS


def test_v56_workbench_surfaces_present():
    for text in ["History by day", "Chat", "Files", "Tool Logs", "Runs", "Screenshot archive", "historyDays", "screenshotArchive"]:
        assert text in HTML
    assert ".work-island" in CSS
    assert ".artifact-image" in CSS
    assert "/api/history-days" in JS
    assert "/api/artifacts" in JS
    assert "/api/tool-logs" in JS
    assert "/api/runs" in JS
    assert JS.count("setTimeout(poll") == 1
    assert "setInterval(" not in JS


def test_inline_artifact_markdown_is_rendered_as_image_reference():
    assert "artifact-image" in JS
    assert "art_[0-9a-f]{16}" in JS
    assert "artifactUrl(id)" in JS
    # Accepts the escaped parenthesis form observed in 5.5.1 responses.
    assert "\\\\?\\((art_" in JS


def test_artifact_store_lists_newest_and_marks_screenshots(tmp_path: Path):
    store = ArtifactStore(tmp_path / ".term5")
    text = store.put("hello", kind="tool", name="log")
    shot = store.put_bytes(b"\x89PNG\r\n\x1a\nabc", kind="screenshot", name="home", extension=".png")
    rows = store.list(limit=10)
    assert rows[0]["id"] == shot.id
    assert rows[0]["viewable"] is True
    assert rows[0]["textual"] is False
    assert rows[1]["id"] == text.id
    assert rows[1]["textual"] is True


def test_episode_calendar_includes_empty_days_and_day_lookup(tmp_path: Path):
    store = EpisodeStore(tmp_path / ".term5")
    ep = store.add("today prompt", "today outcome")
    days = store.history_days(days=3, offset_minutes=0)
    assert len(days) == 3
    assert days[0]["has_history"] is True
    assert any(x["has_history"] is False for x in days[1:])
    rows = store.on_date(days[0]["date"], offset_minutes=0)
    assert rows and rows[-1].id == ep.id


def test_persistent_tool_logs_are_bounded_and_reasoning_free(tmp_path: Path):
    class R:
        event_path = tmp_path / "events.jsonl"
    records = [
        {"ts":"2026-09-28T10:00:00+00:00","type":"tool.started","data":{"name":"browser_screenshot"}},
        {"ts":"2026-09-28T10:01:00+00:00","type":"vision.completed","data":{"preview":"looks good"}},
        {"ts":"2026-09-28T10:02:00+00:00","type":"reasoning_content","data":{"content":"hidden"}},
    ]
    R.event_path.write_text("\n".join(json.dumps(x) for x in records)+"\n")
    rows = _persistent_events(R(), day="2026-09-28", limit=10)
    assert [x["type"] for x in rows] == ["tool.started", "vision.completed"]


def test_daily_history_uses_full_journal_not_compact_episode(tmp_path: Path):
    class R:
        journal_path = tmp_path / "journal.jsonl"
    now = datetime.now(timezone.utc).isoformat()
    answer = "## Result\n\n![home](art_0123456789abcdef)\n\nFull markdown is preserved."
    R.journal_path.write_text(json.dumps({"ts": now, "prompt": "show me", "answer": answer, "reasoning": "high"}) + "\n")
    days = _journal_history_days(R(), days=2, offset_minutes=0)
    assert days[0]["has_history"] is True
    rows = _journal_day(R(), days[0]["date"], offset_minutes=0)
    assert rows[0]["content"] == "show me"
    assert rows[1]["content"] == answer
    assert "art_0123456789abcdef" in rows[1]["content"]
