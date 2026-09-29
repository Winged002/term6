from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

from ..config import Term5Config
from ..models import ToolResult
from ..providers.base import ModelProvider
from ..security.paths import PathGuard
from ..state.transactions import TransactionManager
from ..workspace.graph import WorkspaceGraph
from ..workspace.verify import verify_content


class FimEditor:
    def __init__(self, config: Term5Config, provider: ModelProvider, guard: PathGuard,
                 transactions: TransactionManager, graph: WorkspaceGraph) -> None:
        self.config = config
        self.provider = provider
        self.guard = guard
        self.transactions = transactions
        self.graph = graph

    @staticmethod
    def _instruction_prefix(path: Path, instruction: str) -> str:
        ext = path.suffix.lower()
        safe = " ".join(instruction.split())[:1000]
        if ext in {".py", ".sh", ".rb", ".yaml", ".yml", ".toml"}:
            return f"# term_5 FIM objective: {safe}\n# Complete only the missing region below.\n"
        if ext in {".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".swift"}:
            return f"// term_5 FIM objective: {safe}\n// Complete only the missing region below.\n"
        if ext in {".html", ".xml"}:
            return f"<!-- term_5 FIM objective: {safe}; complete only the missing region below. -->\n"
        return f"# term_5 FIM objective: {safe}\n"

    @staticmethod
    def _strip_fences(text: str) -> str:
        t = text.strip("\r\n")
        if t.startswith("```"):
            lines = t.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines)
        return text

    async def candidate(self, path: str, start_line: int, end_line: int, instruction: str,
                        max_tokens: int | None = None) -> tuple[Path, str, str, dict, str, str | None]:
        p = self.guard.resolve(path, allow_root=False)
        if not p.is_file():
            raise FileNotFoundError(f"Not a file: {path}")
        expected_sha256 = self.transactions.fingerprint(p)
        original = p.read_text(encoding="utf-8", errors="replace")
        lines = original.splitlines(keepends=True)
        start = int(start_line)
        end = int(end_line)
        if start < 1 or end < start or end > len(lines):
            raise ValueError(f"Invalid line range {start}:{end}; file has {len(lines)} lines")
        actual_prefix = "".join(lines[:start - 1])
        actual_suffix = "".join(lines[end:])
        prefix_context = actual_prefix[-24_000:]
        suffix_context = actual_suffix[:24_000]
        tokens = int(max_tokens or self.config.fim.default_max_output_tokens)
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        objective = instruction
        last_verify = "verification not run"
        for attempt in range(self.config.fim.repair_attempts + 1):
            if attempt:
                objective = (
                    f"{instruction}. Previous FIM completion failed local verification: {last_verify}. "
                    "Repair the missing region only; do not add markdown fences or unrelated code."
                )
            prompt = self._instruction_prefix(p, objective) + prefix_context
            insertion, usage = await self.provider.fim(
                prompt, suffix_context, max_tokens=tokens,
                temperature=self.config.model.temperature,
            )
            total_usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            total_usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            insertion = self._strip_fences(insertion)
            if not insertion:
                last_verify = "empty completion"
                continue
            if actual_prefix and not actual_prefix.endswith(("\n", "\r")) and not insertion.startswith(("\n", "\r")):
                insertion = "\n" + insertion
            if actual_suffix and not insertion.endswith(("\n", "\r")):
                insertion += "\n"
            candidate = actual_prefix + insertion + actual_suffix
            ok, last_verify = verify_content(p, candidate)
            if ok:
                return p, original, candidate, total_usage, last_verify, expected_sha256
        raise ValueError(f"FIM candidate rejected locally after repair attempts. {last_verify}")

    async def preview(self, path: str, start_line: int, end_line: int, instruction: str,
                      max_tokens: int | None = None) -> ToolResult:
        try:
            p, original, candidate, usage, verify, expected_sha256 = await self.candidate(
                path, start_line, end_line, instruction, max_tokens
            )
        except Exception as exc:
            return ToolResult(False, str(exc))
        rel = p.relative_to(self.guard.root).as_posix()
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True), candidate.splitlines(keepends=True),
            fromfile=rel, tofile=rel + " (FIM candidate)", n=3,
        ))
        if len(diff) > 40_000:
            diff = diff[:20_000] + "\n...[diff truncated]...\n" + diff[-20_000:]
        return ToolResult(True, f"FIM preview for {rel}. {verify}\n\n{diff}",
                          data={"usage": usage, "candidate": candidate, "expected_sha256": expected_sha256}, verification=[verify])

    async def edit(self, path: str, start_line: int, end_line: int, instruction: str,
                   max_tokens: int | None = None) -> ToolResult:
        try:
            p, original, candidate, usage, verify, expected_sha256 = await self.candidate(
                path, start_line, end_line, instruction, max_tokens
            )
        except Exception as exc:
            return ToolResult(False, str(exc))
        rel = p.relative_to(self.guard.root).as_posix()
        try:
            txid = self.transactions.write_one(rel, candidate, expected_sha256=expected_sha256)
            self.graph.refresh()
        except Exception as exc:
            return ToolResult(False, f"FIM commit rejected: {exc}", data={"usage": usage})
        return ToolResult(True, f"FIM edit committed to {rel} in {txid}. {verify}",
                          data={"usage": usage, "inserted_chars": len(candidate) - len(original), "transaction_id": txid},
                          changed_files=[rel], verification=[verify])
    async def batch(self, changes: list[dict[str, Any]], *, commit: bool = False) -> ToolResult:
        """Generate independent FIM candidates concurrently.

        Each item must target a unique file. All candidates are generated and
        locally validated before any write occurs. With commit=True the group is
        committed through one TransactionManager.write_group rollback boundary.
        """
        if not changes:
            return ToolResult(False, "No FIM changes supplied")
        limit = max(1, min(int(self.config.fim.max_parallel), 32))
        if len(changes) > limit:
            return ToolResult(False, f"Too many parallel FIM changes; max is {limit}")
        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(changes):
            path = str(raw.get("path") or "").strip()
            if not path:
                return ToolResult(False, f"FIM change {idx+1} has no path")
            rel = self.guard.relative(path)
            if rel in seen:
                return ToolResult(False, f"Parallel FIM requires unique files; duplicate: {rel}")
            seen.add(rel)
            try:
                start_line = int(raw.get("start_line"))
                end_line = int(raw.get("end_line"))
            except Exception:
                return ToolResult(False, f"FIM change {idx+1} has invalid line range")
            instruction = str(raw.get("instruction") or "").strip()
            if not instruction:
                return ToolResult(False, f"FIM change {idx+1} has no instruction")
            normalized.append({
                "path": rel,
                "start_line": start_line,
                "end_line": end_line,
                "instruction": instruction,
                "max_tokens": raw.get("max_tokens"),
            })

        sem = asyncio.Semaphore(limit)

        async def one(item: dict[str, Any]):
            async with sem:
                return await self.candidate(
                    item["path"], item["start_line"], item["end_line"],
                    item["instruction"], item.get("max_tokens"),
                )

        results = await asyncio.gather(*(one(x) for x in normalized), return_exceptions=True)
        failures: list[str] = []
        built = []
        for item, result in zip(normalized, results):
            if isinstance(result, Exception):
                failures.append(f"{item['path']}: {type(result).__name__}: {result}")
                continue
            pth, original, candidate, usage, verify, expected_sha256 = result
            built.append((item, pth, original, candidate, usage, verify, expected_sha256))
        if failures:
            return ToolResult(False, "Parallel FIM rejected before commit:\n" + "\n".join(failures))

        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        previews = []
        for item, _pth, original, candidate, u, verify, expected_sha256 in built:
            usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
            diff = "".join(difflib.unified_diff(
                original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                fromfile=item["path"], tofile=item["path"] + " (FIM candidate)", n=3,
            ))
            previews.append({"path": item["path"], "verify": verify, "diff": diff[:20000], "expected_sha256": expected_sha256})

        if not commit:
            body = "\n\n".join(
                f"### {x['path']}\n{x['verify']}\n{x['diff']}" for x in previews
            )
            return ToolResult(
                True,
                f"Parallel FIM preview: {len(previews)} candidate(s) validated.\n\n{body}",
                data={"usage": usage, "previews": previews},
                verification=[x["verify"] for x in previews],
            )

        txid = self.transactions.write_group([
            (item["path"], candidate) for item, _, _, candidate, _, _, _ in built
        ], expected={item["path"]: expected_sha256 for item, _, _, _, _, _, expected_sha256 in built})
        self.graph.refresh()
        changed = [item["path"] for item, *_ in built]
        return ToolResult(
            True,
            f"Parallel FIM atomically committed {len(changed)} file(s) in {txid}.",
            data={"usage": usage, "transaction_id": txid},
            changed_files=changed,
            verification=[entry[5] for entry in built],
        )
