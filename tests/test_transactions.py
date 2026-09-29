from pathlib import Path

from term5.security.paths import PathGuard
from term5.state.transactions import TransactionManager


def test_transaction_rollback_restores_file(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("before", encoding="utf-8")
    tx = TransactionManager(tmp_path / ".term5", PathGuard(tmp_path))
    snap = tx.begin("a.txt")
    tx.atomic_write(snap, "after")
    assert p.read_text() == "after"
    tx.rollback(snap)
    assert p.read_text() == "before"


def test_transaction_rollback_removes_new_file(tmp_path: Path):
    p = tmp_path / "new.txt"
    tx = TransactionManager(tmp_path / ".term5", PathGuard(tmp_path))
    snap = tx.begin("new.txt")
    tx.atomic_write(snap, "new")
    tx.rollback(snap)
    assert not p.exists()
