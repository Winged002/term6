from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class Episode:
    id: str
    ts: str
    prompt: str
    outcome: str
    reasoning: str = ""
    tools: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def compact_text(self) -> str:
        lines = [f"{self.id} · {self.ts[:16]} · request: {self.prompt[:600]}"]
        if self.outcome:
            lines.append("outcome: " + self.outcome[:1800])
        if self.changed_files:
            lines.append("changed: " + ", ".join(self.changed_files[:24]))
        if self.tools:
            lines.append("tools: " + ", ".join(self.tools[:24]))
        if self.failures:
            lines.append("failures: " + " | ".join(self.failures[:6]))
        return "\n".join(lines)


class EpisodeStore:
    """Compact completed-task memory, separate from raw chat history.

    The compact store is held in memory after startup; append operations update
    both memory and JSONL. This avoids O(n) disk rescans on each turn/status poll.
    """

    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "episodes.jsonl"
        self._episodes: list[Episode] = []
        self._by_id: dict[str, Episode] = {}
        self._load()

    @staticmethod
    def _stable_id(prompt: str, outcome: str) -> str:
        digest = hashlib.sha256((prompt + "\0" + outcome).encode("utf-8", errors="replace")).hexdigest()[:10]
        return "ep_" + digest

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {x for x in re.findall(r"[A-Za-z0-9_./:-]{2,}", str(text).lower()) if len(x) > 1}

    def _accept(self, ep: Episode) -> None:
        if not ep.id or ep.id in self._by_id:
            return
        self._episodes.append(ep)
        self._by_id[ep.id] = ep

    def _load(self) -> None:
        self._episodes = []
        self._by_id = {}
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                # v5.2 journal compatibility: {ts,prompt,answer,reasoning}.
                if "outcome" not in obj and "answer" in obj:
                    prompt = str(obj.get("prompt") or "")
                    outcome = str(obj.get("answer") or "")
                    self._accept(Episode(
                        id=self._stable_id(prompt, outcome), ts=str(obj.get("ts") or ""),
                        prompt=prompt, outcome=outcome,
                        reasoning=str(obj.get("reasoning") or "legacy"),
                    ))
                    continue
                prompt = str(obj.get("prompt") or "")
                outcome = str(obj.get("outcome") or "")
                ep = Episode(
                    id=str(obj.get("id") or self._stable_id(prompt, outcome)),
                    ts=str(obj.get("ts") or ""), prompt=prompt, outcome=outcome,
                    reasoning=str(obj.get("reasoning") or ""),
                    tools=list(obj.get("tools") or []),
                    changed_files=list(obj.get("changed_files") or []),
                    failures=list(obj.get("failures") or []),
                )
                self._accept(ep)
            except Exception:
                continue

    def add(self, prompt: str, outcome: str, *, reasoning: str = "", tools: list[str] | None = None,
            changed_files: list[str] | None = None, failures: list[str] | None = None) -> Episode:
        clean_prompt = " ".join(str(prompt or "").split())[:4000]
        clean_outcome = " ".join(str(outcome or "").split())[:8000]
        eid = self._stable_id(clean_prompt, clean_outcome)
        existing = self._by_id.get(eid)
        if existing is not None:
            return existing
        rec = Episode(
            id=eid,
            ts=datetime.now(timezone.utc).isoformat(),
            prompt=clean_prompt,
            outcome=clean_outcome,
            reasoning=str(reasoning or "")[:32],
            tools=list(dict.fromkeys(str(x) for x in (tools or []) if str(x)))[:64],
            changed_files=list(dict.fromkeys(str(x) for x in (changed_files or []) if str(x)))[:128],
            failures=[" ".join(str(x).split())[:600] for x in (failures or []) if str(x)][:16],
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        self._accept(rec)
        return rec

    def recall(self, query: str, k: int = 5, max_chars: int = 8000) -> str:
        if not self._episodes:
            return ""
        q = self._terms(query)
        scored: list[tuple[float, Episode]] = []
        total = len(self._episodes)
        for idx, ep in enumerate(self._episodes):
            hay = self._terms(ep.prompt + " " + ep.outcome + " " + " ".join(ep.changed_files) + " " + " ".join(ep.tools))
            overlap = len(q & hay)
            if overlap <= 0:
                continue
            recency = (idx + 1) / max(1, total)
            scored.append((overlap * 3.0 + recency, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        lines = ["Relevant completed-task episodes (compact; inspect files/Git for current truth):"]
        used = len(lines[0])
        count = 0
        for _, ep in scored[:max(1, int(k))]:
            block = "- " + ep.compact_text().replace("\n", "\n  ")
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
            count += 1
        return "\n".join(lines) if count else ""

    def recent(self, limit: int = 20) -> list[Episode]:
        """Return newest compact episodes without rescanning the JSONL store."""
        n = max(1, min(int(limit), 200))
        return list(reversed(self._episodes[-n:]))

    @staticmethod
    def _shifted_date(ts: str, offset_minutes: int = 0) -> str:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            from datetime import timedelta
            # JS Date.getTimezoneOffset(): UTC - local. Local time is UTC - offset.
            dt = dt.astimezone(timezone.utc) - timedelta(minutes=int(offset_minutes or 0))
            return dt.date().isoformat()
        except Exception:
            return str(ts)[:10]

    def history_days(self, *, days: int = 60, offset_minutes: int = 0) -> list[dict[str, object]]:
        """Compact calendar counts for recent completed turns, including empty days."""
        from datetime import timedelta
        span = max(1, min(int(days), 366))
        now = datetime.now(timezone.utc) - timedelta(minutes=int(offset_minutes or 0))
        counts: dict[str, int] = {}
        for ep in self._episodes:
            key = self._shifted_date(ep.ts, offset_minutes)
            counts[key] = counts.get(key, 0) + 1
        out: list[dict[str, object]] = []
        for i in range(span):
            key = (now.date() - timedelta(days=i)).isoformat()
            out.append({"date": key, "count": counts.get(key, 0), "has_history": counts.get(key, 0) > 0})
        return out

    def on_date(self, date_key: str, *, offset_minutes: int = 0, limit: int = 200) -> list[Episode]:
        key = str(date_key or "").strip()
        rows = [ep for ep in self._episodes if self._shifted_date(ep.ts, offset_minutes) == key]
        return rows[-max(1, min(int(limit), 500)):]

    def count(self) -> int:
        return len(self._episodes)
