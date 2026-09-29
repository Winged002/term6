from __future__ import annotations

import ast
import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..security.paths import PathGuard


_JS_IMPORT_RX = re.compile(
    r"(?:\bimport\s+(?:[^;]*?\s+from\s+)?|\bexport\s+[^;]*?\s+from\s+|\brequire\s*\(|\bimport\s*\()"
    r"[\"']([^\"']+)[\"']"
)
_JS_SYMBOL_RX = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
    re.M,
)
_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


class WorkspaceGraph:
    """Persistent deterministic local repository graph.

    RC1 indexes Python plus JavaScript/TypeScript symbols and import edges.
    It never calls a model: the graph is cheap evidence for executive planning,
    impact analysis, relevant-test discovery and context grounding.
    """

    def __init__(self, guard: PathGuard, state_dir: Path, max_files: int = 25_000) -> None:
        self.guard = guard
        self.path = state_dir / "workspace_index.json"
        self.max_files = max_files
        self.data: dict[str, Any] = {"format": 3, "files": {}, "updated": 0.0}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
                self.data = raw
        except Exception:
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _python_info(path: Path) -> tuple[list[str], list[str]]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except Exception:
            return [], []
        symbols: list[str] = []
        imports: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(node.name)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append("." * int(getattr(node, "level", 0) or 0) + module)
        return symbols, [x for x in imports if x]

    @staticmethod
    def _js_info(path: Path) -> tuple[list[str], list[str]]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return [], []
        imports = [m.group(1) for m in _JS_IMPORT_RX.finditer(text)]
        symbols: list[str] = []
        for m in _JS_SYMBOL_RX.finditer(text):
            name = m.group(1) or m.group(2)
            if name:
                symbols.append(name)
        # Preserve order while removing duplicates.
        return list(dict.fromkeys(symbols)), list(dict.fromkeys(imports))

    @staticmethod
    def _python_candidates(module: str, source: str) -> list[str]:
        source_path = Path(source)
        candidates: list[Path] = []
        dots = len(module) - len(module.lstrip("."))
        name = module.lstrip(".")
        if dots:
            base = source_path.parent
            for _ in range(max(0, dots - 1)):
                base = base.parent
            prefix = base
            if name:
                prefix = prefix.joinpath(*name.split("."))
            candidates.extend([prefix.with_suffix(".py"), prefix / "__init__.py"])
        else:
            p = Path(*name.split(".")) if name else Path()
            candidates.extend([p.with_suffix(".py"), p / "__init__.py"])
        return [p.as_posix() for p in candidates]

    @staticmethod
    def _js_candidates(module: str, source: str) -> list[str]:
        # Bare package imports belong to node_modules/package resolution and are
        # intentionally not mapped to workspace files in rc1.
        if not module.startswith("."):
            return []
        base = (Path(source).parent / module)
        candidates: list[Path] = []
        if base.suffix.lower() in _JS_EXTS:
            candidates.append(base)
        else:
            for ext in _JS_EXTS:
                candidates.append(Path(str(base) + ext))
            for ext in _JS_EXTS:
                candidates.append(base / ("index" + ext))
        # Normalize ./ and ../ without touching the filesystem.
        out: list[str] = []
        for p in candidates:
            parts: list[str] = []
            for part in p.parts:
                if part in {"", "."}:
                    continue
                if part == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(part)
            out.append(Path(*parts).as_posix())
        return out

    @staticmethod
    def _likely_test_names(path: str) -> set[str]:
        p = Path(path)
        stem = p.stem
        if stem.startswith("test_") or stem.endswith("_test") or stem.endswith(".test") or stem.endswith(".spec"):
            return set()
        ext = p.suffix
        names = {f"test_{stem}{ext}", f"{stem}_test{ext}"}
        if ext.lower() in _JS_EXTS:
            names.update({f"{stem}.test{ext}", f"{stem}.spec{ext}"})
        return names

    @staticmethod
    def _is_test_path(path: str) -> bool:
        p = Path(path)
        name = p.name
        stem = p.stem
        return (
            name.startswith("test_") or stem.endswith("_test") or
            ".test." in name or ".spec." in name or
            "tests" in p.parts or "__tests__" in p.parts
        )

    def _derive_relations(self, files: dict[str, Any]) -> None:
        file_names = set(files)
        imported_by: dict[str, set[str]] = {p: set() for p in file_names}
        for source, info in files.items():
            resolved: list[str] = []
            suffix = str(info.get("suffix") or "")
            for module in info.get("imports", []):
                candidates = (
                    self._python_candidates(str(module), source)
                    if suffix == ".py" else
                    self._js_candidates(str(module), source)
                    if suffix in _JS_EXTS else []
                )
                for candidate in candidates:
                    if candidate in file_names:
                        resolved.append(candidate)
                        imported_by[candidate].add(source)
                        break
            info["internal_imports"] = sorted(set(resolved))

        all_test_paths = [p for p in file_names if self._is_test_path(p)]
        for path, info in files.items():
            info["imported_by"] = sorted(imported_by.get(path, set()))
            expected = self._likely_test_names(path)
            likely: list[str] = []
            if expected:
                for test_path in all_test_paths:
                    tp = Path(test_path)
                    if tp.name in expected:
                        likely.append(test_path)
                        continue
                    if path in files[test_path].get("internal_imports", []):
                        likely.append(test_path)
            info["likely_tests"] = sorted(set(likely))[:50]

    def refresh(self) -> dict[str, int]:
        old = self.data.get("files", {})
        files: dict[str, Any] = {}
        count = 0
        for path, rel in self.guard.iter_files():
            if count >= self.max_files:
                break
            count += 1
            try:
                st = path.stat()
            except OSError:
                continue
            key = rel.as_posix()
            cached = old.get(key)
            if cached and cached.get("size") == st.st_size and cached.get("mtime_ns") == st.st_mtime_ns and self.data.get("format") == 3:
                files[key] = {k: v for k, v in cached.items() if k not in {"internal_imports", "imported_by", "likely_tests"}}
                continue
            symbols: list[str] = []
            imports: list[str] = []
            suffix = path.suffix.lower()
            if st.st_size <= 2_000_000:
                if suffix == ".py":
                    symbols, imports = self._python_info(path)
                elif suffix in _JS_EXTS:
                    symbols, imports = self._js_info(path)
            files[key] = {
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "suffix": suffix,
                "symbols": symbols,
                "imports": imports,
            }
        self._derive_relations(files)
        self.data = {"format": 3, "files": files, "updated": time.time()}
        self.save()
        return self.stats()

    def stats(self) -> dict[str, int]:
        files = self.data.get("files", {})
        return {
            "files": len(files),
            "symbols": sum(len(v.get("symbols", [])) for v in files.values()),
            "internal_edges": sum(len(v.get("internal_imports", [])) for v in files.values()),
            "test_edges": sum(len(v.get("likely_tests", [])) for v in files.values()),
            "python_files": sum(1 for v in files.values() if v.get("suffix") == ".py"),
            "js_ts_files": sum(1 for v in files.values() if v.get("suffix") in _JS_EXTS),
        }

    def find_symbol(self, name: str) -> list[dict[str, Any]]:
        q = name.lower()
        out = []
        for path, info in self.data.get("files", {}).items():
            hits = [s for s in info.get("symbols", []) if q in s.lower()]
            if hits:
                out.append({"path": path, "symbols": hits})
        return out[:100]

    def find_files(self, query: str) -> list[str]:
        q = query.lower()
        return [p for p in self.data.get("files", {}) if q in p.lower()][:200]

    def file_relations(self, path: str) -> dict[str, Any]:
        rel = self.guard.relative(path)
        info = self.data.get("files", {}).get(rel)
        if info is None:
            raise FileNotFoundError(f"File not indexed: {rel}")
        return {
            "path": rel,
            "language": "python" if info.get("suffix") == ".py" else "js/ts" if info.get("suffix") in _JS_EXTS else "other",
            "imports": list(info.get("imports", [])),
            "internal_imports": list(info.get("internal_imports", [])),
            "imported_by": list(info.get("imported_by", [])),
            "likely_tests": list(info.get("likely_tests", [])),
            "symbols": list(info.get("symbols", [])),
        }

    def impact_map(self, path: str, depth: int = 2, max_nodes: int = 200) -> dict[str, Any]:
        start = self.guard.relative(path)
        files = self.data.get("files", {})
        if start not in files:
            raise FileNotFoundError(f"File not indexed: {start}")
        depth = max(0, min(int(depth), 5))
        max_nodes = max(1, min(int(max_nodes), 1000))
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        seen = {start}
        nodes: list[dict[str, Any]] = []
        tests: set[str] = set(files[start].get("likely_tests", []))
        while queue and len(nodes) < max_nodes:
            current, d = queue.popleft()
            info = files.get(current, {})
            nodes.append({"path": current, "depth": d, "imported_by": list(info.get("imported_by", []))})
            tests.update(info.get("likely_tests", []))
            if d >= depth:
                continue
            for parent in info.get("imported_by", []):
                if parent not in seen:
                    seen.add(parent)
                    queue.append((parent, d + 1))
        return {"root": start, "affected": nodes, "likely_tests": sorted(tests)[:200]}

    @staticmethod
    def _terms(text: str) -> list[str]:
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}", text.lower())
        stop = {"this", "that", "with", "from", "into", "when", "where", "what", "should", "could", "would", "please", "make", "change", "implement", "debug", "fix"}
        return [w for w in words if w not in stop][:20]

    def query_context(self, text: str, max_items: int = 24, prefix: str = "") -> str:
        files = self.data.get("files", {})
        normalized_prefix = str(prefix or "").strip("/")
        if normalized_prefix and normalized_prefix != ".":
            files = {k: v for k, v in files.items() if k == normalized_prefix or k.startswith(normalized_prefix + "/")}
        terms = self._terms(text)
        scored: list[tuple[int, str]] = []
        for path, info in files.items():
            p_low = path.lower()
            symbols = " ".join(info.get("symbols", [])).lower()
            score = sum(3 for t in terms if t in p_low) + sum(2 for t in terms if t in symbols)
            if score:
                scored.append((score, path))
        scored.sort(key=lambda x: (-x[0], x[1]))
        lines = ["Workspace evidence (index-derived; inspect files with tools before editing):"]
        for _, path in scored[:max_items]:
            info = files[path]
            lines.append(
                f"- {path}; symbols={','.join(info.get('symbols', [])[:8]) or '-'}; "
                f"imports={','.join(info.get('internal_imports', [])[:6]) or '-'}; "
                f"imported_by={','.join(info.get('imported_by', [])[:6]) or '-'}; "
                f"tests={','.join(info.get('likely_tests', [])[:6]) or '-'}"
            )
        if len(lines) == 1:
            lines.append("- no strong path/symbol matches")
        return "\n".join(lines)[:24_000]

    def brief(self) -> str:
        stats = self.stats()
        markers = []
        for name in ("pyproject.toml", "package.json", "requirements.txt", "Cargo.toml", "go.mod", "README.md"):
            try:
                if self.guard.resolve(name).exists():
                    markers.append(name)
            except PermissionError:
                pass
        suffix_counts: dict[str, int] = {}
        for info in self.data.get("files", {}).values():
            s = info.get("suffix") or "(none)"
            suffix_counts[s] = suffix_counts.get(s, 0) + 1
        top = sorted(suffix_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        return (
            f"Workspace: {self.guard.root}\n"
            f"Index: {stats['files']} files, {stats['symbols']} symbols, "
            f"{stats['internal_edges']} internal import edges, {stats['test_edges']} likely-test edges\n"
            f"Indexed code: Python={stats['python_files']}, JS/TS={stats['js_ts_files']}\n"
            f"Markers: {', '.join(markers) or 'none detected'}\n"
            f"Common file types: {', '.join(f'{k}:{v}' for k,v in top) or 'none'}"
        )
