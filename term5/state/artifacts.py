from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ArtifactRef:
    id: str
    kind: str
    name: str
    chars: int
    path: str
    created_at: str


class ArtifactStore:
    """Local cold store for verbose tool/model artifacts.

    Full outputs live on disk and only bounded previews enter model context.
    The JSONL index is loaded once so status/retrieval does not rescan an
    ever-growing file on every model iteration.
    """

    ID_RX = re.compile(r"^art_[0-9a-f]{16}$")

    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "artifacts"
        self.index_path = self.root / "index.jsonl"
        self._index: dict[str, dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        self._index = {}
        if not self.index_path.exists():
            return
        for line in self.index_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            aid = str(rec.get("id") or "") if isinstance(rec, dict) else ""
            if self.ID_RX.fullmatch(aid):
                self._index[aid] = rec

    def put(self, text: str, *, kind: str = "tool", name: str = "artifact", metadata: dict[str, Any] | None = None) -> ArtifactRef:
        raw = str(text or "")
        stamp = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256((stamp + "\0" + name + "\0" + raw).encode("utf-8", errors="replace")).hexdigest()[:16]
        aid = "art_" + digest
        day = stamp[:10]
        directory = self.root / day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{aid}.txt"
        path.write_text(raw, encoding="utf-8")
        rec = {
            "id": aid, "kind": kind, "name": name, "chars": len(raw),
            "path": str(path.relative_to(self.root)), "created_at": stamp,
            "metadata": metadata or {},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._index[aid] = rec
        return ArtifactRef(aid, kind, name, len(raw), rec["path"], stamp)

    def put_bytes(self, data: bytes, *, kind: str = "binary", name: str = "artifact",
                  extension: str = ".bin", metadata: dict[str, Any] | None = None) -> ArtifactRef:
        raw = bytes(data or b"")
        stamp = datetime.now(timezone.utc).isoformat()
        ext = str(extension or ".bin").lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
            ext = ".bin"
        digest = hashlib.sha256((stamp + "\0" + name).encode("utf-8") + b"\0" + raw).hexdigest()[:16]
        aid = "art_" + digest
        day = stamp[:10]
        directory = self.root / day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{aid}{ext}"
        path.write_bytes(raw)
        rec = {
            "id": aid, "kind": kind, "name": name, "chars": 0, "bytes": len(raw),
            "path": str(path.relative_to(self.root)), "created_at": stamp,
            "metadata": metadata or {},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._index[aid] = rec
        return ArtifactRef(aid, kind, name, 0, rec["path"], stamp)

    def path_for(self, artifact_id: str) -> Path:
        rec = self._lookup(artifact_id)
        if rec is None:
            raise FileNotFoundError(f"unknown artifact: {artifact_id}")
        path = (self.root / rec["path"]).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise ValueError("artifact path escaped store")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"artifact payload missing: {artifact_id}")
        return path

    def metadata(self, artifact_id: str) -> dict[str, Any]:
        rec = self._lookup(artifact_id)
        if rec is None:
            raise FileNotFoundError(f"unknown artifact: {artifact_id}")
        return dict(rec)

    def _lookup(self, artifact_id: str) -> dict[str, Any] | None:
        aid = str(artifact_id or "")
        if not self.ID_RX.fullmatch(aid):
            return None
        return self._index.get(aid)

    def read(self, artifact_id: str, *, query: str = "", max_chars: int = 12000) -> str:
        rec = self._lookup(artifact_id)
        if rec is None:
            raise FileNotFoundError(f"unknown artifact: {artifact_id}")
        path = self.path_for(artifact_id)
        if rec.get("bytes") is not None and rec.get("chars", 0) == 0:
            raise TypeError(f"artifact {artifact_id} is binary ({rec.get('bytes', 0)} bytes)")
        text = path.read_text(encoding="utf-8", errors="replace")
        cap = max(1000, min(int(max_chars), 50000))
        q = str(query or "").strip()
        if not q:
            if len(text) <= cap:
                return text
            head = cap * 2 // 3
            tail = cap - head
            return text[:head] + f"\n\n… [artifact truncated locally; {len(text)-cap:,} chars omitted] …\n\n" + text[-tail:]
        terms = [t.lower() for t in re.findall(r"[A-Za-z0-9_./:-]{2,}", q)][:12]
        lines = text.splitlines()
        hits: list[str] = []
        used = 0
        for i, line in enumerate(lines):
            low = line.lower()
            if terms and not any(t in low for t in terms):
                continue
            lo, hi = max(0, i - 2), min(len(lines), i + 3)
            chunk = "\n".join(lines[lo:hi])
            if chunk not in hits:
                hits.append(chunk)
                used += len(chunk)
            if used >= cap:
                break
        return "\n\n---\n\n".join(hits)[:cap] if hits else f"No matching lines in {artifact_id}."

    def list(self, *, limit: int = 200, kind: str = "", day: str = "") -> list[dict[str, Any]]:
        """Return newest artifact metadata without reading payloads from disk."""
        n = max(1, min(int(limit), 1000))
        want_kind = str(kind or "").strip().lower()
        want_day = str(day or "").strip()
        rows: list[dict[str, Any]] = []
        for rec in reversed(list(self._index.values())):
            if want_kind and str(rec.get("kind") or "").lower() != want_kind:
                continue
            if want_day and not str(rec.get("created_at") or "").startswith(want_day):
                continue
            item = dict(rec)
            path = str(item.get("path") or "")
            suffix = Path(path).suffix.lower()
            item["viewable"] = str(item.get("kind") or "") in {"screenshot", "creative_mockup"} and suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg"}
            item["textual"] = not bool(item.get("bytes")) or int(item.get("chars") or 0) > 0
            rows.append(item)
            if len(rows) >= n:
                break
        return rows

    def count(self) -> int:
        return len(self._index)
