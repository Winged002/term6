from pathlib import Path

from term5.state.memory import MemoryStore


def test_memory_states_and_recall(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.json")
    assert store.recall("anything").state == "empty"
    rec = store.add("Project authentication uses signed session tokens", ["auth", "session"])
    result = store.recall("authentication session")
    assert result.state == "ok"
    assert result.records[0].id == rec.id
    assert store.recall("quantum bananas").state == "no-match"
