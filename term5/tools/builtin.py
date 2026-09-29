from __future__ import annotations

import ast
import difflib
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from ..models import RiskLevel, ToolDefinition, ToolResult
from ..security.paths import PathGuard
from ..state.memory import MemoryStore
from ..state.transactions import TransactionManager
from ..workspace.graph import WorkspaceGraph
from ..workspace.verify import VerificationPipeline, verify_content
from .registry import ToolRegistry


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        d["required"] = required
    return d


def s(desc: str = "") -> dict[str, Any]:
    d = {"type": "string"}
    if desc:
        d["description"] = desc
    return d


def i(desc: str = "", minimum: int | None = None, maximum: int | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "integer"}
    if desc:
        d["description"] = desc
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


def register_builtin_tools(
    registry: ToolRegistry,
    guard: PathGuard,
    memory: MemoryStore,
    graph: WorkspaceGraph,
    tx: TransactionManager,
    verifier: VerificationPipeline | None = None,
) -> None:
    verifier = verifier or VerificationPipeline(guard.root, allow_tests=False)
    async def read_file(path: str, start_line: int = 1, max_lines: int = 400) -> ToolResult:
        p = guard.resolve(path, allow_root=False)
        if not p.is_file():
            return ToolResult(False, f"Not a file: {path}")
        max_lines = max(1, min(int(max_lines), 4000))
        start_line = max(1, int(start_line))
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        selected = lines[start_line - 1:start_line - 1 + max_lines]
        rendered = "\n".join(f"{start_line+n:>6} | {line}" for n, line in enumerate(selected))
        more = start_line - 1 + len(selected) < len(lines)
        suffix = f"\n[lines {start_line}-{start_line+len(selected)-1} of {len(lines)}{' ; more' if more else ''}]"
        return ToolResult(True, rendered + suffix)

    registry.register(ToolDefinition("read_file", "Read a UTF-8 text file with line numbers.",
        obj({"path": s("Workspace-relative path"), "start_line": i(minimum=1), "max_lines": i(minimum=1, maximum=4000)}, ["path"]),
        read_file, {"workspace.read"}, RiskLevel.LOW, True))

    async def list_dir(path: str = ".") -> ToolResult:
        p = guard.resolve(path)
        if not p.is_dir():
            return ToolResult(False, f"Not a directory: {path}")
        items = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                rel = child.resolve(strict=False).relative_to(guard.root)
            except ValueError:
                continue
            if guard.is_blocked_relative(rel):
                continue
            if child.name.startswith("."):
                continue
            marker = "/" if child.is_dir() else ""
            items.append(rel.as_posix() + marker)
        return ToolResult(True, "\n".join(items) if items else "(empty)")

    registry.register(ToolDefinition("list_dir", "List a workspace directory, excluding protected paths.",
        obj({"path": s()}, []), list_dir, {"workspace.read"}, RiskLevel.LOW, True))

    async def glob_files(pattern: str) -> ToolResult:
        hits = []
        for p, rel in guard.iter_files():
            if fnmatch.fnmatch(rel.as_posix(), pattern) or fnmatch.fnmatch(rel.name, pattern):
                hits.append(rel.as_posix())
                if len(hits) >= 500:
                    break
        return ToolResult(True, "\n".join(hits) if hits else "No matches.")

    registry.register(ToolDefinition("glob_files", "Find workspace files by glob pattern.",
        obj({"pattern": s("Example: **/*.py")}, ["pattern"]), glob_files, {"workspace.read"}, RiskLevel.LOW, True))

    async def search_text(query: str, path: str = ".", regex: bool = False, max_results: int = 100) -> ToolResult:
        root = guard.resolve(path)
        try:
            rx = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        except re.error as exc:
            return ToolResult(False, f"Invalid regex: {exc}")
        max_results = max(1, min(int(max_results), 500))
        hits = []
        for p, rel in guard.iter_files(root):
            try:
                if p.stat().st_size > 5_000_000:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel.as_posix()}:{n}: {line[:500]}")
                    if len(hits) >= max_results:
                        return ToolResult(True, "\n".join(hits) + "\n[result limit reached]")
        return ToolResult(True, "\n".join(hits) if hits else "No matches.")

    registry.register(ToolDefinition("search_text", "Search text or regex across workspace files.",
        obj({"query": s(), "path": s(), "regex": {"type": "boolean"}, "max_results": i(minimum=1, maximum=500)}, ["query"]),
        search_text, {"workspace.read"}, RiskLevel.LOW, True))

    async def file_info(path: str) -> ToolResult:
        p = guard.resolve(path, allow_root=False)
        if not p.exists():
            return ToolResult(False, "Path does not exist")
        st = p.stat()
        rel = p.relative_to(guard.root).as_posix()
        data = {"path": rel, "type": "dir" if p.is_dir() else "file", "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        if p.is_file():
            data["sha256"] = tx.fingerprint(rel)
        return ToolResult(True, json.dumps(data, indent=2), data=data)

    registry.register(ToolDefinition("file_info", "Get safe local metadata for a file or directory.",
        obj({"path": s()}, ["path"]), file_info, {"workspace.read"}, RiskLevel.LOW, True))

    async def tree(path: str = ".", depth: int = 3) -> ToolResult:
        root = guard.resolve(path)
        depth = max(0, min(int(depth), 8))
        lines = []
        base_parts = len(root.relative_to(guard.root).parts)
        for p in sorted(root.rglob("*")):
            try:
                rel = p.resolve(strict=False).relative_to(guard.root)
            except ValueError:
                continue
            if guard.is_blocked_relative(rel) or any(x.startswith(".") for x in rel.parts):
                continue
            d = len(rel.parts) - base_parts
            if d > depth:
                continue
            lines.append("  " * max(0, d - 1) + ("[D] " if p.is_dir() else "[F] ") + rel.name)
            if len(lines) >= 1000:
                lines.append("[tree limit reached]")
                break
        return ToolResult(True, "\n".join(lines) if lines else "(empty)")

    registry.register(ToolDefinition("tree", "Show a bounded workspace tree.", obj({"path": s(), "depth": i(minimum=0, maximum=8)}),
        tree, {"workspace.read"}, RiskLevel.LOW, True))

    async def sha256_file(path: str) -> ToolResult:
        p = guard.resolve(path, allow_root=False)
        if not p.is_file():
            return ToolResult(False, "Not a file")
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return ToolResult(True, h.hexdigest())

    registry.register(ToolDefinition("sha256_file", "Compute SHA-256 of a workspace file.", obj({"path": s()}, ["path"]),
        sha256_file, {"workspace.read"}, RiskLevel.LOW, True))

    async def code_outline(path: str) -> ToolResult:
        p = guard.resolve(path, allow_root=False)
        text = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix.lower() == ".py":
            try:
                root = ast.parse(text)
            except SyntaxError as exc:
                return ToolResult(False, f"SyntaxError: {exc}")
            lines = []
            for node in root.body:
                if isinstance(node, ast.ClassDef):
                    lines.append(f"class {node.name} @ {node.lineno}")
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            lines.append(f"  {'async ' if isinstance(child, ast.AsyncFunctionDef) else ''}def {child.name} @ {child.lineno}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines.append(f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name} @ {node.lineno}")
            return ToolResult(True, "\n".join(lines) if lines else "(no top-level symbols)")
        headings = [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1) if line.lstrip().startswith("#")]
        return ToolResult(True, "\n".join(f"{n}: {line}" for n, line in headings[:500]) or "No outline available.")

    registry.register(ToolDefinition("code_outline", "Show a structural outline of a source/text file.", obj({"path": s()}, ["path"]),
        code_outline, {"workspace.read"}, RiskLevel.LOW, True))

    async def check_file(path: str) -> ToolResult:
        p = guard.resolve(path, allow_root=False)
        content = p.read_text(encoding="utf-8", errors="replace")
        ok, msg = verify_content(p, content)
        return ToolResult(ok, msg, verification=[msg])

    registry.register(ToolDefinition("check_file", "Run a local syntax/parse check appropriate for a file type.", obj({"path": s()}, ["path"]),
        check_file, {"workspace.read"}, RiskLevel.LOW, True))

    async def write_file(path: str, content: str, expected_sha256: str = "") -> ToolResult:
        p = guard.resolve(path, allow_root=False)
        ok, verify = verify_content(p, content)
        if not ok:
            return ToolResult(False, verify, verification=[verify])
        rel = p.relative_to(guard.root).as_posix()
        try:
            if expected_sha256:
                txid = tx.write_one(rel, content, expected_sha256=expected_sha256)
            else:
                txid = tx.write_one(rel, content)
            graph.refresh()
            return ToolResult(True, f"Wrote {rel} in {txid}. {verify}", data={"transaction_id": txid}, changed_files=[rel], verification=[verify])
        except Exception as exc:
            return ToolResult(False, f"Write rejected: {exc}", verification=[verify])

    registry.register(ToolDefinition("write_file", "Create or replace a workspace text file transactionally after syntax validation. Supply expected_sha256 from file_info/sha256_file to reject stale overwrites.",
        obj({"path": s(), "content": s(), "expected_sha256": s()}, ["path", "content"]), write_file, {"workspace.write"}, RiskLevel.HIGH, False))

    async def write_files(changes: list[dict[str, str]]) -> ToolResult:
        if not changes:
            return ToolResult(False, "No changes supplied")
        if len(changes) > 32:
            return ToolResult(False, "At most 32 files may be changed in one atomic group")
        normalized: list[tuple[str, str]] = []
        expected: dict[str, str | None] = {}
        verification: list[str] = []
        changed: list[str] = []
        seen: set[str] = set()
        for item in changes:
            path = str(item.get("path") or "")
            content = str(item.get("content") if item.get("content") is not None else "")
            pth = guard.resolve(path, allow_root=False)
            rel = pth.relative_to(guard.root).as_posix()
            if rel in seen:
                return ToolResult(False, f"Duplicate path in batch: {rel}")
            seen.add(rel)
            ok, verify = verify_content(pth, content)
            verification.append(f"{rel}: {verify}")
            if not ok:
                return ToolResult(False, f"Batch rejected before write: {rel}: {verify}", verification=verification)
            if "expected_sha256" in item and item.get("expected_sha256") is not None:
                expected[rel] = str(item.get("expected_sha256") or "") or None
            normalized.append((rel, content))
            changed.append(rel)
        try:
            txid = tx.write_group(normalized, expected=expected or None)
        except Exception as exc:
            return ToolResult(False, f"Atomic batch rejected: {exc}", verification=verification)
        graph.refresh()
        return ToolResult(True, f"Atomically committed {len(changed)} file(s) in {txid}.",
                          data={"transaction_id": txid}, changed_files=changed, verification=verification)

    registry.register(ToolDefinition(
        "write_files",
        "Atomically replace/create up to 32 text files. All candidates are validated first; optional expected_sha256 fields reject stale overwrites; any write failure rolls the whole group back.",
        obj({"changes": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
            "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "expected_sha256": {"type": "string"}},
            "required": ["path", "content"], "additionalProperties": False
        }}}, ["changes"]),
        write_files, {"workspace.write"}, RiskLevel.HIGH, False, True,
    ))

    async def replace_text(path: str, old: str, new: str, count: int = 1) -> ToolResult:
        p = guard.resolve(path, allow_root=False)
        current = p.read_text(encoding="utf-8", errors="replace")
        occurrences = current.count(old)
        if occurrences == 0:
            return ToolResult(False, "Exact old text was not found")
        if count == 1 and occurrences != 1:
            return ToolResult(False, f"Expected one occurrence but found {occurrences}; provide a more specific old string")
        count = max(1, int(count))
        candidate = current.replace(old, new, count)
        expected = tx.fingerprint(path)
        return await write_file(path, candidate, expected_sha256=expected or "")

    registry.register(ToolDefinition("replace_text", "Replace exact text in one file; fails on ambiguous single replacement.",
        obj({"path": s(), "old": s(), "new": s(), "count": i(minimum=1, maximum=100)}, ["path", "old", "new"]),
        replace_text, {"workspace.write"}, RiskLevel.HIGH, False))

    async def transaction_history(limit: int = 20) -> ToolResult:
        rows = tx.history(limit=limit)
        return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), data=rows)

    registry.register(ToolDefinition(
        "transaction_history",
        "Show recent local write transactions and whether they are undoable/redoable.",
        obj({"limit": i(minimum=1, maximum=200)}),
        transaction_history, {"workspace.read"}, RiskLevel.LOW, True,
    ))

    async def undo_last() -> ToolResult:
        try:
            txid, changed = tx.undo_last()
            graph.refresh()
            return ToolResult(True, f"Undid {txid}: {', '.join(changed)}",
                              data={"transaction_id": txid}, changed_files=changed)
        except Exception as exc:
            return ToolResult(False, f"Undo rejected: {exc}")

    registry.register(ToolDefinition(
        "undo_last",
        "Undo the latest RC-era committed file transaction. Refuses if files changed externally after the commit.",
        obj({}), undo_last, {"workspace.write"}, RiskLevel.HIGH, False, True,
    ))

    async def redo_last() -> ToolResult:
        try:
            txid, changed = tx.redo_last()
            graph.refresh()
            return ToolResult(True, f"Redid {txid}: {', '.join(changed)}",
                              data={"transaction_id": txid}, changed_files=changed)
        except Exception as exc:
            return ToolResult(False, f"Redo rejected: {exc}")

    registry.register(ToolDefinition(
        "redo_last",
        "Redo the latest undone RC-era file transaction. Refuses if files changed since undo.",
        obj({}), redo_last, {"workspace.write"}, RiskLevel.HIGH, False, True,
    ))

    async def workspace_brief(refresh: bool = False) -> ToolResult:
        if refresh or not graph.data.get("files"):
            graph.refresh()
        return ToolResult(True, graph.brief())

    registry.register(ToolDefinition("workspace_brief", "Return the local workspace summary and refresh it on request.",
        obj({"refresh": {"type": "boolean"}}), workspace_brief, {"workspace.read"}, RiskLevel.LOW, True))

    async def space_find(query: str) -> ToolResult:
        if not graph.data.get("files"):
            graph.refresh()
        hits = graph.find_files(query)
        return ToolResult(True, "\n".join(hits) if hits else "No indexed file matches.")

    registry.register(ToolDefinition("space_find", "Find file paths in the persistent workspace index.", obj({"query": s()}, ["query"]),
        space_find, {"workspace.read"}, RiskLevel.LOW, True))

    async def find_symbol(name: str) -> ToolResult:
        if not graph.data.get("files"):
            graph.refresh()
        hits = graph.find_symbol(name)
        return ToolResult(True, json.dumps(hits, indent=2) if hits else "No indexed symbol matches.")

    registry.register(ToolDefinition("find_symbol", "Find indexed Python/JavaScript/TypeScript symbols in the workspace graph.", obj({"name": s()}, ["name"]),
        find_symbol, {"workspace.read"}, RiskLevel.LOW, True))

    async def file_relations(path: str) -> ToolResult:
        if not graph.data.get("files"):
            graph.refresh()
        try:
            data = graph.file_relations(path)
        except Exception as exc:
            return ToolResult(False, str(exc))
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition("file_relations", "Show imports, imported-by edges, symbols and likely tests for an indexed file.",
        obj({"path": s()}, ["path"]), file_relations, {"workspace.read"}, RiskLevel.LOW, True))

    async def impact_map(path: str, depth: int = 2) -> ToolResult:
        if not graph.data.get("files"):
            graph.refresh()
        try:
            data = graph.impact_map(path, depth=depth)
        except Exception as exc:
            return ToolResult(False, str(exc))
        return ToolResult(True, json.dumps(data, ensure_ascii=False, indent=2), data=data)

    registry.register(ToolDefinition("impact_map", "Trace bounded imported-by impact and likely tests before changing a file.",
        obj({"path": s(), "depth": i(minimum=0, maximum=5)}, ["path"]), impact_map, {"workspace.read"}, RiskLevel.LOW, True))

    async def diff_files(path_a: str, path_b: str, context: int = 3) -> ToolResult:
        a = guard.resolve(path_a, allow_root=False)
        b = guard.resolve(path_b, allow_root=False)
        if not a.is_file() or not b.is_file():
            return ToolResult(False, "Both paths must be files")
        ta = a.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        tb = b.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        n = max(0, min(int(context), 20))
        diff = "".join(difflib.unified_diff(ta, tb, fromfile=path_a, tofile=path_b, n=n))
        if len(diff) > 60000:
            diff = diff[:30000] + "\n...[diff truncated]...\n" + diff[-30000:]
        return ToolResult(True, diff or "No differences.")

    registry.register(ToolDefinition("diff_files", "Compare two workspace text files with a unified diff.",
        obj({"path_a": s(), "path_b": s(), "context": i(minimum=0, maximum=20)}, ["path_a", "path_b"]),
        diff_files, {"workspace.read"}, RiskLevel.LOW, True))

    async def verify_changes(paths: list[str]) -> ToolResult:
        if not paths:
            return ToolResult(False, "No paths supplied")
        if len(paths) > 64:
            return ToolResult(False, "At most 64 paths may be verified")
        resolved = [guard.resolve(p, allow_root=False) for p in paths]
        report = verifier.files(resolved)
        return ToolResult(report.ok, report.text(), verification=report.checks)

    registry.register(ToolDefinition("verify_changes", "Run deterministic local parse/syntax checks across changed files.",
        obj({"paths": {"type": "array", "minItems": 1, "maxItems": 64, "items": {"type": "string"}}}, ["paths"]),
        verify_changes, {"workspace.read"}, RiskLevel.LOW, True))

    async def run_tests(targets: list[str] | None = None) -> ToolResult:
        report = verifier.tests(targets or [])
        return ToolResult(report.ok, report.text(), verification=report.checks)

    registry.register(ToolDefinition("run_tests", "Run pytest (or unittest fallback) with optional bounded targets. Project-code execution is opt-in.",
        obj({"targets": {"type": "array", "maxItems": 32, "items": {"type": "string"}}}),
        run_tests, {"process.tests"}, RiskLevel.HIGH, False, True))

    async def sqlite_query(path: str, query: str, max_rows: int = 200) -> ToolResult:
        q = query.strip()
        if not re.match(r"^(SELECT|WITH|EXPLAIN)\b", q, re.I):
            return ToolResult(False, "Beta1 sqlite_query is read-only: SELECT/WITH/EXPLAIN only")
        pth = guard.resolve(path, allow_root=False)
        if not pth.is_file():
            return ToolResult(False, "SQLite path is not a file")
        max_rows = max(1, min(int(max_rows), 1000))
        uri = f"file:{pth.as_posix()}?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            cur = con.execute(q)
            rows = [dict(r) for r in cur.fetchmany(max_rows + 1)]
            con.close()
        except Exception as exc:
            return ToolResult(False, f"SQLite error: {exc}")
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        text = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        if truncated:
            text += "\n[row limit reached]"
        return ToolResult(True, text, data=rows)

    registry.register(ToolDefinition("sqlite_query", "Execute a read-only SELECT/WITH/EXPLAIN query against a workspace SQLite database.",
        obj({"path": s(), "query": s(), "max_rows": i(minimum=1, maximum=1000)}, ["path", "query"]),
        sqlite_query, {"workspace.read"}, RiskLevel.MEDIUM, True))

    async def zip_list(path: str) -> ToolResult:
        pth = guard.resolve(path, allow_root=False)
        if not pth.is_file():
            return ToolResult(False, "ZIP path is not a file")
        try:
            with zipfile.ZipFile(pth, "r") as zf:
                rows = [{"name": x.filename, "size": x.file_size, "compressed": x.compress_size} for x in zf.infolist()[:2000]]
            return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), data=rows)
        except Exception as exc:
            return ToolResult(False, f"ZIP error: {exc}")

    registry.register(ToolDefinition("zip_list", "List entries in a workspace ZIP archive without extracting it.",
        obj({"path": s()}, ["path"]), zip_list, {"workspace.read"}, RiskLevel.LOW, True))

    async def memory_store(text: str, tags: list[str] | None = None, source_path: str = "") -> ToolResult:
        source = {}
        fingerprint = None
        if source_path:
            pth = guard.resolve(source_path, allow_root=False)
            if not pth.is_file():
                return ToolResult(False, f"Memory source is not a file: {source_path}")
            rel = pth.relative_to(guard.root).as_posix()
            source = {"kind": "workspace_file", "path": rel}
            fingerprint = memory.file_fingerprint(pth)
        rec = memory.add(text, tags or [], source=source, source_fingerprint=fingerprint)
        suffix = f" linked to {source.get('path')}" if source else ""
        return ToolResult(True, f"Stored {rec.id}{suffix}.")

    registry.register(ToolDefinition("memory_store", "Store a durable local semantic memory. Optionally link it to a workspace file so rc1 can mark it stale when that source changes.",
        obj({"text": s(), "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 20}, "source_path": s()}, ["text"]),
        memory_store, {"memory.write"}, RiskLevel.MEDIUM, False, True))

    async def memory_recall(query: str, k: int = 5) -> ToolResult:
        status = memory.recall(query, max(1, min(int(k), 20)))
        if status.state != "ok":
            return ToolResult(True, f"[{status.state}] {status.message}")
        lines = [f"[{status.state}] {status.message}"]
        for rec in status.records:
            lines.append(f"- {rec.id}: {rec.text}" + (" [STALE]" if rec.stale else ""))
        return ToolResult(True, "\n".join(lines))

    registry.register(ToolDefinition("memory_recall", "Recall relevant durable local memory and distinguish empty/no-match/unavailable states.",
        obj({"query": s(), "k": i(minimum=1, maximum=20)}, ["query"]), memory_recall, {"memory.read"}, RiskLevel.LOW, True))

    async def memory_list(limit: int = 30) -> ToolResult:
        limit = max(1, min(int(limit), 200))
        if memory.load_error:
            return ToolResult(False, "Memory unavailable: " + memory.load_error)
        rows = memory.records[-limit:][::-1]
        return ToolResult(True, "\n".join(f"{r.id}: {r.text[:200]}" for r in rows) if rows else "[empty] memory store is empty")

    registry.register(ToolDefinition("memory_list", "List recent local memory records.", obj({"limit": i(minimum=1, maximum=200)}),
        memory_list, {"memory.read"}, RiskLevel.LOW, True))

    async def memory_forget(match: str) -> ToolResult:
        n = memory.forget(match)
        return ToolResult(True, f"Forgot {n} matching memory record(s).")

    registry.register(ToolDefinition("memory_forget", "Delete matching local memory records by id or text substring.", obj({"match": s()}, ["match"]),
        memory_forget, {"memory.write"}, RiskLevel.HIGH, False, True))

    def git_run(args: list[str]) -> ToolResult:
        proc = subprocess.run(["git", *args], cwd=guard.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        return ToolResult(proc.returncode == 0, proc.stdout.strip() or "(no output)")

    async def git_status() -> ToolResult:
        return git_run(["status", "--short", "--branch"])

    registry.register(ToolDefinition("git_status", "Show read-only Git status.", obj({}), git_status, {"git.read"}, RiskLevel.LOW, True))

    async def git_diff(path: str = "") -> ToolResult:
        args = ["diff", "--"] + ([path] if path else [])
        return git_run(args)

    registry.register(ToolDefinition("git_diff", "Show unstaged Git diff, optionally for one path.", obj({"path": s()}), git_diff, {"git.read"}, RiskLevel.LOW, True))

    async def git_log(limit: int = 20) -> ToolResult:
        return git_run(["log", f"-{max(1,min(int(limit),100))}", "--oneline", "--decorate"])

    registry.register(ToolDefinition("git_log", "Show recent Git commits read-only.", obj({"limit": i(minimum=1, maximum=100)}), git_log, {"git.read"}, RiskLevel.LOW, True))
