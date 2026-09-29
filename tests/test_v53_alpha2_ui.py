from term5.ui.web import HTML, _recent_chat
from term5.ui.web_assets import CSS, JS
from term5.state.episodes import EpisodeStore


def test_stable_workspace_surfaces_are_present():
    for text in [
        "Chat", "Applications", "Deployments", "Product", "Memory",
        "Live Execution", "Execution Timeline", "/assets/app.css", "/assets/app.js",
    ]:
        assert text in HTML
    assert "unsafe-inline" not in HTML


def test_web_assets_are_local_and_have_simple_shell():
    assert ".app{" in CSS
    assert ".sidebar" in CSS
    assert ".drawer" in CSS
    assert "function route" in JS
    assert "/api/memory-overview" in JS
    assert "/api/history" in JS
    assert "https://" not in CSS
    assert "https://" not in JS
    # alpha3 intentionally has only one activity polling loop.
    assert JS.count("setTimeout(poll") == 1
    assert "setInterval(" not in JS


def test_recent_chat_filters_internal_protocol_and_context():
    rows = _recent_chat([
        {"role": "system", "content": "system"},
        {"role": "user", "content": "[term_5 turn context — generated locally; reference data, not user instructions]\nsecret context"},
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]},
        {"role": "tool", "tool_call_id": "x", "content": "large result"},
        {"role": "assistant", "content": "done"},
    ])
    assert rows == [
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": "done"},
    ]


def test_episode_store_recent_is_newest_first(tmp_path):
    store = EpisodeStore(tmp_path)
    store.add("one", "first")
    store.add("two", "second")
    recent = store.recent(2)
    assert [x.prompt for x in recent] == ["two", "one"]
