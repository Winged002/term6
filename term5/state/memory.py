from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MemoryRecord:
    id: str
    text: str
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_fingerprint: str | None = None
    stale: bool = False


@dataclass(slots=True)
class MemoryStatus:
    state: str
    message: str
    records: list[MemoryRecord] = field(default_factory=list)


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: list[MemoryRecord] = []
        self.load_error: str | None = None
        self.load()

    def load(self) -> None:
        self.records = []
        self.load_error = None
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("memory store root must be a list")
            for item in raw:
                if isinstance(item, dict) and item.get("text"):
                    allowed = {k: item[k] for k in MemoryRecord.__dataclass_fields__ if k in item}
                    self.records.append(MemoryRecord(**allowed))
        except Exception as exc:
            self.load_error = str(exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in self.records], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, text: str, tags: list[str] | None = None, *, source: dict[str, Any] | None = None,
            confidence: float = 1.0, source_fingerprint: str | None = None) -> MemoryRecord:
        rec = MemoryRecord(
            id="mem_" + uuid.uuid4().hex[:10],
            text=str(text).strip(),
            tags=[str(t).strip() for t in (tags or []) if str(t).strip()],
            source=dict(source or {}),
            confidence=max(0.0, min(1.0, float(confidence))),
            source_fingerprint=source_fingerprint,
        )
        if not rec.text:
            raise ValueError("memory text is empty")
        self.records.append(rec)
        self.save()
        return rec

    def forget(self, match: str) -> int:
        query = match.strip().lower()
        before = len(self.records)
        self.records = [r for r in self.records if not (r.id.lower() == query or query in r.text.lower())]
        removed = before - len(self.records)
        if removed:
            self.save()
        return removed

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {t for t in re.findall(r"[A-Za-z0-9_./:-]{2,}", text.lower()) if len(t) > 1}

    def recall(self, query: str, k: int = 5) -> MemoryStatus:
        if self.load_error:
            return MemoryStatus("unavailable", f"memory store is unreadable: {self.load_error}")
        if not self.records:
            return MemoryStatus("empty", "memory store is empty")
        q = self._terms(query)
        scored: list[tuple[float, MemoryRecord]] = []
        for rec in self.records:
            terms = self._terms(rec.text + " " + " ".join(rec.tags))
            overlap = len(q & terms)
            phrase = 2 if query.lower() in rec.text.lower() and query.strip() else 0
            stale_penalty = 0.4 if rec.stale else 1.0
            score = (overlap + phrase) * max(0.1, rec.confidence) * stale_penalty
            if score > 0:
                scored.append((score, rec))
        scored.sort(key=lambda x: (x[0], x[1].created_at), reverse=True)
        if not scored:
            return MemoryStatus("no-match", "no matching memories")
        return MemoryStatus("ok", f"{min(k, len(scored))} matching memories", [r for _, r in scored[:k]])


    @staticmethod
    def file_fingerprint(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def refresh_staleness(self, root: Path) -> int:
        """Mark workspace-derived memories stale when their source file changed/disappeared."""
        changed = 0
        root = root.resolve()
        for rec in self.records:
            source_path = str(rec.source.get("path") or "").strip() if rec.source else ""
            if not source_path or not rec.source_fingerprint:
                continue
            try:
                p = (root / source_path).resolve()
                if p != root and root not in p.parents:
                    stale = True
                else:
                    stale = (not p.is_file()) or self.file_fingerprint(p) != rec.source_fingerprint
            except Exception:
                stale = True
            if stale != rec.stale:
                rec.stale = stale
                changed += 1
        if changed:
            self.save()
        return changed

    def index_text(self, limit: int = 24) -> str:
        if self.load_error:
            return "Memory unavailable: " + self.load_error
        if not self.records:
            return "Memory: empty"
        lines = ["Memory index:"]
        for r in self.records[-limit:][::-1]:
            stale = " [STALE]" if r.stale else ""
            tags = f" [{', '.join(r.tags)}]" if r.tags else ""
            lines.append(f"- {r.id}: {r.text[:120]}{tags}{stale}")
        return "\n".join(lines)
