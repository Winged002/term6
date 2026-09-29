from __future__ import annotations

from term5.ui.web_assets import CSS, JS


def test_ui4_inspector_has_live_feedback_for_every_selection_kind():
    assert 'function inspectorFeedback(sel)' in JS
    assert 'Live feedback' in JS
    assert 'live-indicator' in CSS
    assert 'feedback-stream' in CSS
    # All inspector branches mount the same live feedback component.
    assert JS.count('inspectorFeedback(s)') >= 4


def test_ui4_activity_stream_updates_inspector_immediately():
    assert 'function ingestInspectorEvent(e)' in JS
    assert 'addEvent(e);ingestInspectorEvent(e);state.seq=' in JS
    assert "if(state.route==='team')renderWorkspaceInspector()" in JS
    # Do not add a second activity poll loop just for Inspector feedback.
    assert JS.count("/api/activity?after=") == 1


def test_ui4_feedback_covers_operational_and_response_feedback():
    for marker in [
        'Task started', 'Task completed', 'Task failed', 'Next action',
        'Delegated to owners', 'responseFeedbackForSelection', 'message-response',
    ]:
        assert marker in JS


def test_ui4_feedback_is_selection_scoped():
    for marker in [
        "sel.kind==='central'", "sel.kind==='project'", "sel.kind==='task'",
        "sel.kind==='worker'", "sel.kind==='objective'", 'taskIds', 'objective_id',
    ]:
        assert marker in JS
