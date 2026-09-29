from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..security.paths import PathGuard
from .locks import WorkspaceWriteLock


@dataclass(slots=True)
class Snapshot:
    transaction_id: str
    target: Path
    existed: bool
    backup: Path | None


@dataclass(slots=True)
class TransactionGroup:
    transaction_id: str
    snapshots: list[Snapshot] = field(default_factory=list)


class StaleWriteError(RuntimeError):
    pass


class TransactionHistoryError(RuntimeError):
    pass


class TransactionManager:
    """Crash-recoverable, reversible local text transactions.

    RC1 keeps rc1's cross-process write lock and optimistic SHA-256 guards,
    and adds an explicit undo/redo history. Every new RC transaction captures
    both its before and after state. Undo/redo refuse to overwrite unexpected
    external edits: the current file fingerprints must match the transaction
    state being reversed/reapplied.

    RC1 transaction folders remain readable and recoverable, but transactions
    created before RC1 do not have after snapshots and are therefore reported as
    non-undoable rather than guessed at.
    """

    def __init__(self, state_dir: Path, guard: PathGuard) -> None:
        self.root = state_dir / "transactions"
        self.index = self.root / "index.jsonl"
        self.guard = guard
        self.write_lock = WorkspaceWriteLock(state_dir / "workspace-write.lock")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def sha256(path: Path) -> str | None:
        if not path.exists():
            return None
        if not path.is_file():
            raise IsADirectoryError(str(path))
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def fingerprint(self, target: str | Path) -> str | None:
        return self.sha256(self.guard.resolve(target, allow_root=False))

    def _write_meta(self, folder: Path, meta: dict) -> None:
        tmp = folder / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, folder / "meta.json")

    def _read_meta(self, folder: Path) -> dict:
        raw = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TransactionHistoryError(f"invalid transaction metadata: {folder.name}")
        return raw

    def _append_index(self, meta: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.index.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")

    def _check_expected(self, expected: dict[str, str | None] | None) -> None:
        if not expected:
            return
        for raw, wanted in expected.items():
            p = self.guard.resolve(raw, allow_root=False)
            actual = self.sha256(p)
            if actual != wanted:
                rel = p.relative_to(self.guard.root).as_posix()
                raise StaleWriteError(
                    f"stale write rejected for {rel}: expected sha256={wanted or '<missing>'}, "
                    f"current={actual or '<missing>'}"
                )

    def _invalidate_redo(self) -> None:
        if not self.root.exists():
            return
        for folder in self.root.iterdir():
            if not folder.is_dir() or not (folder / "meta.json").exists():
                continue
            try:
                meta = self._read_meta(folder)
            except Exception:
                continue
            if meta.get("status") == "undone":
                meta["status"] = "redo_invalidated"
                meta["redo_invalidated_at"] = self._now()
                self._write_meta(folder, meta)
                self._append_index(meta)

    def begin(self, target: str | Path) -> Snapshot:
        path = self.guard.resolve(target, allow_root=False)
        txid = "tx_" + uuid.uuid4().hex[:12]
        folder = self.root / txid
        folder.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        backup = folder / "before.bin" if existed else None
        before_sha = self.sha256(path) if existed else None
        if existed:
            if not path.is_file():
                raise IsADirectoryError(str(path))
            shutil.copy2(path, backup)
        meta = {
            "format": 2,
            "id": txid,
            "kind": "single",
            "target": path.relative_to(self.guard.root).as_posix(),
            "existed": existed,
            "backup": backup.name if backup else None,
            "before_sha256": before_sha,
            "created_at": self._now(),
            "status": "open",
        }
        self._write_meta(folder, meta)
        return Snapshot(txid, path, existed, backup)

    def begin_group(self, targets: Iterable[str | Path]) -> TransactionGroup:
        resolved: list[Path] = []
        seen: set[Path] = set()
        for target in targets:
            p = self.guard.resolve(target, allow_root=False)
            if p in seen:
                raise ValueError(f"duplicate transaction target: {p.relative_to(self.guard.root)}")
            seen.add(p)
            resolved.append(p)
        if not resolved:
            raise ValueError("transaction group has no targets")
        txid = "txg_" + uuid.uuid4().hex[:12]
        folder = self.root / txid
        folder.mkdir(parents=True, exist_ok=True)
        snapshots: list[Snapshot] = []
        targets_meta = []
        for idx, path in enumerate(resolved):
            existed = path.exists()
            backup = folder / f"before_{idx:04d}.bin" if existed else None
            before_sha = self.sha256(path) if existed else None
            if existed:
                if not path.is_file():
                    raise IsADirectoryError(str(path))
                shutil.copy2(path, backup)
            snapshots.append(Snapshot(txid, path, existed, backup))
            targets_meta.append({
                "target": path.relative_to(self.guard.root).as_posix(),
                "existed": existed,
                "backup": backup.name if backup else None,
                "before_sha256": before_sha,
            })
        meta = {
            "format": 2,
            "id": txid,
            "kind": "group",
            "targets": targets_meta,
            "created_at": self._now(),
            "status": "open",
        }
        self._write_meta(folder, meta)
        return TransactionGroup(txid, snapshots)

    def atomic_write(self, snap: Snapshot, content: str, *, encoding: str = "utf-8") -> None:
        snap.target.parent.mkdir(parents=True, exist_ok=True)
        tmp = snap.target.with_name(snap.target.name + f".{snap.transaction_id}.tmp")
        tmp.write_text(content, encoding=encoding)
        os.replace(tmp, snap.target)

    def _set_status(self, txid: str, status: str, **extra) -> dict:
        folder = self.root / txid
        meta = self._read_meta(folder)
        meta["status"] = status
        meta[f"{status}_at"] = self._now()
        meta.update(extra)
        self._write_meta(folder, meta)
        return meta

    def _capture_after_single(self, snap: Snapshot) -> dict:
        folder = self.root / snap.transaction_id
        meta = self._read_meta(folder)
        after = folder / "after.bin"
        after_exists = snap.target.exists()
        if after_exists:
            if not snap.target.is_file():
                raise IsADirectoryError(str(snap.target))
            shutil.copy2(snap.target, after)
        meta["after_exists"] = after_exists
        meta["after_backup"] = after.name if after_exists else None
        meta["after_sha256"] = self.sha256(snap.target) if after_exists else None
        meta["undoable"] = True
        self._write_meta(folder, meta)
        return meta

    def _capture_after_group(self, group: TransactionGroup) -> dict:
        folder = self.root / group.transaction_id
        meta = self._read_meta(folder)
        targets = list(meta.get("targets") or [])
        if len(targets) != len(group.snapshots):
            raise TransactionHistoryError("transaction group metadata/snapshot length mismatch")
        for idx, (item, snap) in enumerate(zip(targets, group.snapshots)):
            after = folder / f"after_{idx:04d}.bin"
            exists = snap.target.exists()
            if exists:
                if not snap.target.is_file():
                    raise IsADirectoryError(str(snap.target))
                shutil.copy2(snap.target, after)
            item["after_exists"] = exists
            item["after_backup"] = after.name if exists else None
            item["after_sha256"] = self.sha256(snap.target) if exists else None
        meta["targets"] = targets
        meta["undoable"] = True
        self._write_meta(folder, meta)
        return meta

    def commit(self, snap: Snapshot, *, invalidate_redo: bool = True) -> None:
        if invalidate_redo:
            self._invalidate_redo()
        self._capture_after_single(snap)
        meta = self._set_status(snap.transaction_id, "committed")
        self._append_index(meta)

    def commit_group(self, group: TransactionGroup, *, invalidate_redo: bool = True) -> None:
        if invalidate_redo:
            self._invalidate_redo()
        self._capture_after_group(group)
        meta = self._set_status(group.transaction_id, "committed")
        self._append_index(meta)

    def rollback(self, snap: Snapshot) -> None:
        if snap.existed and snap.backup and snap.backup.exists():
            snap.target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap.backup, snap.target)
        elif not snap.existed and snap.target.exists():
            snap.target.unlink()
        self._set_status(snap.transaction_id, "rolled_back")

    def rollback_group(self, group: TransactionGroup) -> None:
        errors: list[str] = []
        for snap in reversed(group.snapshots):
            try:
                if snap.existed and snap.backup and snap.backup.exists():
                    snap.target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(snap.backup, snap.target)
                elif not snap.existed and snap.target.exists():
                    snap.target.unlink()
            except Exception as exc:
                errors.append(f"{snap.target}: {exc}")
        self._set_status(group.transaction_id, "rolled_back")
        if errors:
            raise RuntimeError("group rollback incomplete: " + "; ".join(errors))

    def write_one(self, target: str | Path, content: str, *, expected_sha256: str | None | object = ...) -> str:
        p = self.guard.resolve(target, allow_root=False)
        rel = p.relative_to(self.guard.root).as_posix()
        expected = None if expected_sha256 is ... else {rel: expected_sha256}
        with self.write_lock.acquire():
            self._check_expected(expected)
            snap = self.begin(rel)
            try:
                self.atomic_write(snap, content)
                self.commit(snap)
                return snap.transaction_id
            except Exception:
                self.rollback(snap)
                raise

    def write_group(self, changes: list[tuple[str | Path, str]], *, expected: dict[str, str | None] | None = None) -> str:
        with self.write_lock.acquire():
            self._check_expected(expected)
            group = self.begin_group(path for path, _ in changes)
            by_path = {snap.target: snap for snap in group.snapshots}
            try:
                for target, content in changes:
                    p = self.guard.resolve(target, allow_root=False)
                    self.atomic_write(by_path[p], content)
                self.commit_group(group)
                return group.transaction_id
            except Exception:
                self.rollback_group(group)
                raise

    def _folders_by_activity(self) -> list[Path]:
        if not self.root.exists():
            return []
        folders = [p for p in self.root.iterdir() if p.is_dir() and (p / "meta.json").exists()]
        return sorted(folders, key=lambda p: (p / "meta.json").stat().st_mtime, reverse=True)

    def history(self, limit: int = 20) -> list[dict]:
        out: list[dict] = []
        for folder in self._folders_by_activity():
            try:
                meta = self._read_meta(folder)
            except Exception as exc:
                out.append({"id": folder.name, "status": "corrupt", "error": str(exc)})
                continue
            item = {
                "id": meta.get("id", folder.name),
                "kind": meta.get("kind"),
                "status": meta.get("status"),
                "undoable": bool(meta.get("undoable")),
                "created_at": meta.get("created_at"),
            }
            if meta.get("kind") == "single":
                item["paths"] = [meta.get("target")]
            else:
                item["paths"] = [x.get("target") for x in (meta.get("targets") or [])]
            out.append(item)
            if len(out) >= max(1, min(int(limit), 200)):
                break
        return out

    def _restore_entry(self, folder: Path, item: dict, *, which: str) -> str:
        if which not in {"before", "after"}:
            raise ValueError(which)
        target = self.guard.resolve(str(item["target"]), allow_root=False)
        if which == "before":
            exists = bool(item.get("existed"))
            backup_name = item.get("backup")
        else:
            exists = bool(item.get("after_exists"))
            backup_name = item.get("after_backup")
        if exists:
            if not backup_name:
                raise TransactionHistoryError(f"missing {which} snapshot for {item['target']}")
            backup = folder / str(backup_name)
            if not backup.exists():
                raise TransactionHistoryError(f"missing {which} snapshot file for {item['target']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + f".{folder.name}.{which}.tmp")
            shutil.copy2(backup, tmp)
            os.replace(tmp, target)
        elif target.exists():
            if not target.is_file():
                raise IsADirectoryError(str(target))
            target.unlink()
        return target.relative_to(self.guard.root).as_posix()

    def _entries(self, meta: dict) -> list[dict]:
        if meta.get("kind") == "single":
            return [{
                "target": meta.get("target"),
                "existed": meta.get("existed"),
                "backup": meta.get("backup"),
                "before_sha256": meta.get("before_sha256"),
                "after_exists": meta.get("after_exists"),
                "after_backup": meta.get("after_backup"),
                "after_sha256": meta.get("after_sha256"),
            }]
        return list(meta.get("targets") or [])

    def _assert_current_matches(self, entries: list[dict], *, which: str) -> None:
        for item in entries:
            target = self.guard.resolve(str(item["target"]), allow_root=False)
            if which == "after":
                wanted_exists = bool(item.get("after_exists"))
                wanted_sha = item.get("after_sha256")
            else:
                wanted_exists = bool(item.get("existed"))
                wanted_sha = item.get("before_sha256")
            actual_exists = target.exists()
            actual_sha = self.sha256(target) if actual_exists else None
            if actual_exists != wanted_exists or actual_sha != wanted_sha:
                raise StaleWriteError(
                    f"transaction history conflict for {item['target']}: current workspace no longer "
                    f"matches the expected {which} state"
                )

    def undo_last(self) -> tuple[str, list[str]]:
        with self.write_lock.acquire():
            for folder in self._folders_by_activity():
                meta = self._read_meta(folder)
                if meta.get("status") != "committed" or not meta.get("undoable"):
                    continue
                entries = self._entries(meta)
                self._assert_current_matches(entries, which="after")
                changed = [self._restore_entry(folder, item, which="before") for item in reversed(entries)]
                fixed = self._set_status(str(meta["id"]), "undone")
                self._append_index(fixed)
                return str(meta["id"]), list(reversed(changed))
        raise TransactionHistoryError("no undoable committed transaction")

    def redo_last(self) -> tuple[str, list[str]]:
        with self.write_lock.acquire():
            for folder in self._folders_by_activity():
                meta = self._read_meta(folder)
                if meta.get("status") != "undone" or not meta.get("undoable"):
                    continue
                entries = self._entries(meta)
                self._assert_current_matches(entries, which="before")
                changed = [self._restore_entry(folder, item, which="after") for item in entries]
                fixed = self._set_status(str(meta["id"]), "committed", redone_at=self._now())
                self._append_index(fixed)
                return str(meta["id"]), changed
        raise TransactionHistoryError("no redoable transaction")

    def integrity(self) -> tuple[bool, list[str]]:
        issues: list[str] = []
        if not self.root.exists():
            return True, issues
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            meta_path = folder / "meta.json"
            if not meta_path.exists():
                issues.append(f"{folder.name}: missing meta.json")
                continue
            try:
                meta = self._read_meta(folder)
                if meta.get("id") != folder.name:
                    issues.append(f"{folder.name}: id mismatch")
                if meta.get("status") not in {"open", "committed", "rolled_back", "undone", "redo_invalidated"}:
                    issues.append(f"{folder.name}: unknown status {meta.get('status')}")
                if meta.get("undoable"):
                    for item in self._entries(meta):
                        for key in ("target", "after_exists"):
                            if key not in item:
                                issues.append(f"{folder.name}: undo metadata missing {key}")
            except Exception as exc:
                issues.append(f"{folder.name}: {exc}")
        return not issues, issues

    def recover_open(self) -> list[str]:
        """Roll back transactions left open by a crashed/interrupted process."""
        recovered: list[str] = []
        if not self.root.exists():
            return recovered
        with self.write_lock.acquire():
            for folder in sorted(self.root.iterdir()):
                if not folder.is_dir():
                    continue
                meta_path = folder / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = self._read_meta(folder)
                except Exception:
                    continue
                if meta.get("status") != "open":
                    continue
                txid = str(meta.get("id") or folder.name)
                kind = meta.get("kind")
                try:
                    if kind == "single":
                        target = self.guard.resolve(str(meta["target"]), allow_root=False)
                        existed = bool(meta.get("existed"))
                        backup_name = meta.get("backup")
                        backup = folder / str(backup_name) if backup_name else None
                        snap = Snapshot(txid, target, existed, backup)
                        self.rollback(snap)
                    elif kind == "group":
                        snaps: list[Snapshot] = []
                        for item in meta.get("targets") or []:
                            target = self.guard.resolve(str(item["target"]), allow_root=False)
                            backup_name = item.get("backup")
                            backup = folder / str(backup_name) if backup_name else None
                            snaps.append(Snapshot(txid, target, bool(item.get("existed")), backup))
                        self.rollback_group(TransactionGroup(txid, snaps))
                    else:
                        continue
                    recovered.append(txid)
                    try:
                        fixed = self._read_meta(folder)
                        fixed["recovered_after_crash"] = True
                        self._write_meta(folder, fixed)
                    except Exception:
                        pass
                except Exception:
                    # Fail closed: leave OPEN for doctor/operator visibility.
                    continue
        return recovered
