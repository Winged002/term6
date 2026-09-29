from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import threading
import urllib.parse
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..context import TURN_CONTEXT_HEADER
from ..models import ReasoningMode
from .web_assets import CSS, HTML, JS


def _host_ok(value: str) -> bool:
    host = (value or "").strip().lower()
    if host.startswith("["):
        return False
    host = host.rsplit(":", 1)[0] if ":" in host else host
    return host in {"127.0.0.1", "localhost"}


def _recent_chat(messages: list[dict[str, Any]], limit: int = 24) -> list[dict[str, str]]:
    """Return only human-facing recent chat; never expose tool protocol/reasoning."""
    rows: list[dict[str, str]] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "user" and (
            content.startswith(TURN_CONTEXT_HEADER)
            or content.startswith("[term_5 turn context")
            or content.startswith("[term_6 turn context")
            or content.startswith("[term_5 executive preflight")
            or content.startswith("[term_6 executive preflight")
            or content.startswith("[term_5 product blueprint")
            or content.startswith("[term_6 product blueprint")
        ):
            continue
        rows.append({"role": role, "content": content[:100_000]})
    return rows[-max(1, min(int(limit), 60)):]


def _journal_rows(runtime, *, limit: int = 5000) -> list[dict[str, Any]]:
    path = runtime.journal_path
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines[-max(1, min(int(limit), 20000)):]:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if not str(rec.get("prompt") or "").strip() and not str(rec.get("answer") or "").strip():
            continue
        rows.append(rec)
    return rows


def _local_date(ts: str, offset_minutes: int = 0) -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.astimezone(timezone.utc) - timedelta(minutes=int(offset_minutes or 0))).date().isoformat()
    except Exception:
        return str(ts)[:10]


def _journal_history_days(runtime, *, days: int = 60, offset_minutes: int = 0) -> list[dict[str, Any]]:
    span = max(1, min(int(days), 366))
    now = datetime.now(timezone.utc) - timedelta(minutes=int(offset_minutes or 0))
    counts: dict[str, int] = {}
    for rec in _journal_rows(runtime):
        key = _local_date(str(rec.get("ts") or ""), offset_minutes)
        counts[key] = counts.get(key, 0) + 1
    return [
        {"date": (now.date()-timedelta(days=i)).isoformat(),
         "count": counts.get((now.date()-timedelta(days=i)).isoformat(), 0),
         "has_history": counts.get((now.date()-timedelta(days=i)).isoformat(), 0) > 0}
        for i in range(span)
    ]


def _journal_day(runtime, date_key: str, *, offset_minutes: int = 0) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for rec in _journal_rows(runtime):
        if _local_date(str(rec.get("ts") or ""), offset_minutes) != str(date_key):
            continue
        ts = str(rec.get("ts") or "")
        prompt = str(rec.get("prompt") or "")
        answer = str(rec.get("answer") or "")
        if prompt.strip():
            rows.append({"role":"user", "content":prompt, "ts":ts})
        if answer.strip():
            rows.append({"role":"assistant", "content":answer, "ts":ts})
    return rows


def _persistent_events(runtime, *, day: str = "", limit: int = 400) -> list[dict[str, Any]]:
    """Read a bounded tail of the redacted persistent execution event log."""
    path = runtime.event_path
    if not path.exists():
        return []
    n = max(1, min(int(limit), 1000))
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        ts = str(rec.get("ts") or "")
        if day and not ts.startswith(day):
            continue
        typ = str(rec.get("type") or "")
        # The Tool Logs surface is about execution evidence, not hidden reasoning.
        if typ.startswith("model.reasoning") or typ in {"reasoning_content"}:
            continue
        rows.append({"ts": ts, "type": typ, "data": rec.get("data") if isinstance(rec.get("data"), dict) else {}})
        if len(rows) >= n:
            break
    rows.reverse()
    return rows


def _agent_audit(runtime, *, project: str = "", limit: int = 500) -> list[dict[str, Any]]:
    rows = _persistent_events(runtime, limit=max(1, min(int(limit), 1000)))
    out: list[dict[str, Any]] = []
    for row in rows:
        typ = str(row.get("type") or "")
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if not (typ.startswith("agent.") or typ.startswith("agents.") or typ.startswith("objective.") or typ.startswith("coordinator.")):
            continue
        if project:
            refs = {str(data.get("project") or ""), str(data.get("owner_project") or ""),
                    str(data.get("from_project") or ""), str(data.get("to_project") or "")}
            if project not in refs:
                continue
        out.append(row)
    return out[-max(1, min(int(limit), 1000)):]


def _worker_snapshots(runtime, tasks: list[dict[str, Any]], audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out=[]
    for task in tasks:
        if str(task.get("status") or "") not in {"claimed","executing","verifying","waiting_agent","waiting_human"}:
            continue
        tid=str(task.get("id") or ""); events=[]
        for row in audit:
            data=row.get("data") if isinstance(row.get("data"),dict) else {}
            if str(data.get("task_id") or "") == tid:
                events.append(row)
        context={}; current_tool=""; iteration=0; last_event=""
        for row in events:
            typ=str(row.get("type") or ""); data=row.get("data") if isinstance(row.get("data"),dict) else {}
            last_event=typ
            if typ=="agent.context.budget": context=data
            if typ=="agent.model.started": iteration=max(iteration,int(data.get("iteration") or 0))
            if typ=="agent.tool.started": current_tool=str(data.get("name") or "")
            if typ=="agent.tool.finished" and str(data.get("name") or "")==current_tool: current_tool=""
        out.append({
            "worker_id": task.get("lease_owner") or f"owner:{task.get('project')}",
            "task_id": tid, "project": task.get("project"), "objective_id": task.get("objective_id") or "",
            "status": task.get("status"), "objective": task.get("objective"), "started_at": task.get("started_at"),
            "lease_expires_at": task.get("lease_expires_at"), "iteration": iteration, "current_tool": current_tool,
            "context": context, "last_event": last_event, "events": events[-30:],
        })
    return out


def _agent_dashboard(runtime) -> dict[str, Any]:
    agents = runtime.agents
    if agents is None:
        return {"enabled": False, "agents": [], "tasks": [], "messages": [], "contracts": [], "forecasts": [], "objectives": [], "attention": {}, "workers": [], "graph": {"nodes": [], "edges": []}, "audit": []}
    snap = agents.snapshot()
    tasks = agents.store.list_tasks("", limit=1000)
    audit = _agent_audit(runtime, limit=1000)
    humans = runtime.human_tasks.list(state="open", project="", limit=500)
    return {
        **snap,
        "tasks": tasks,
        "messages": agents.store.list_messages("", limit=500),
        "contracts": agents.store.list_contracts(),
        "forecasts": agents.store.list_contract_forecasts(active_only=False, limit=500),
        "objectives": agents.objectives(limit=100),
        "attention": agents.attention_snapshot(humans),
        "workers": _worker_snapshots(runtime, tasks, audit),
        "graph": agents.store.task_graph(limit=700),
        "human_tasks": humans,
        "human_task_stats": runtime.human_tasks.stats(),
        "audit": audit,
        "coordinator": {"busy": runtime._turn_lock.locked(), "mode": runtime._coordinator_mode(), "active_run_id": runtime._active_run_id},
        "workspace_layout": agents.workspace_layout(),
    }


class LocalWebApp:
    def __init__(self, runtime, loop: asyncio.AbstractEventLoop, host: str = "127.0.0.1", port: int = 0) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("term_6 web UI is loopback-only")
        self.runtime = runtime
        self.loop = loop
        self.host = "127.0.0.1"
        self.port = int(port)
        self.token = secrets.token_urlsafe(24)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> str:
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "term6-6.2-ui4"

            def log_message(self, fmt, *args):
                return

            def _token(self) -> str:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                return (q.get("token") or [""])[0]

            def _host_authorized(self) -> bool:
                return _host_ok(self.headers.get("Host", ""))

            def _authorized(self) -> bool:
                return self._host_authorized() and hmac.compare_digest(app.token, self._token())

            def _json(self, code: int, obj: Any):
                raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def _text(self, code: int, raw: bytes, content_type: str):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> dict[str, Any]:
                n = min(int(self.headers.get("Content-Length", "0") or 0), 2_000_000)
                raw = self.rfile.read(n).decode("utf-8")
                data = json.loads(raw or "{}")
                if not isinstance(data, dict):
                    raise ValueError("JSON body must be an object")
                return data

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path

                # Static UI assets contain no user/project data. Host gating still
                # prevents non-loopback virtual hosts, while the application/data
                # routes remain token-gated.
                if path == "/assets/app.css":
                    if not self._host_authorized():
                        self._json(403, {"error": "forbidden"}); return
                    self._text(200, CSS.encode("utf-8"), "text/css; charset=utf-8"); return
                if path == "/assets/app.js":
                    if not self._host_authorized():
                        self._json(403, {"error": "forbidden"}); return
                    self._text(200, JS.encode("utf-8"), "application/javascript; charset=utf-8"); return

                if not self._authorized():
                    self._json(403, {"error": "forbidden"}); return

                if path == "/api/status":
                    self._json(200, app.runtime.status()); return
                if path == "/api/status-lite":
                    self._json(200, app.runtime.status_lite()); return
                if path == "/api/activity":
                    q = urllib.parse.parse_qs(parsed.query)
                    try:
                        after = int((q.get("after") or ["0"])[0])
                    except Exception:
                        after = 0
                    try:
                        limit = int((q.get("limit") or ["250"])[0])
                    except Exception:
                        limit = 250
                    self._json(200, app.runtime.activity_snapshot(after=after, limit=limit)); return
                if path == "/api/projects":
                    if app.runtime.projects is None:
                        self._json(200, {"active": "", "projects": [], "github": {"available": False, "reason": "projects disabled"}}); return
                    try:
                        gh = app.runtime.projects.github.status() if app.runtime.config.github.enabled else {"available": False, "reason": "GitHub integration disabled"}
                    except Exception as exc:
                        gh = {"available": False, "authenticated": False, "error": f"{type(exc).__name__}: {exc}"}
                    self._json(200, {"active": app.runtime.projects.registry.active_name, "projects": app.runtime.projects.list(include_status=True), "github": gh}); return
                if path == "/api/agents":
                    if app.runtime.agents is None:
                        self._json(200, {"enabled": False, "agents": [], "counts": {"agents": 0, "working": 0, "queued": 0, "waiting_dependency": 0}}); return
                    self._json(200, app.runtime.agents.snapshot()); return
                if path == "/api/agent-dashboard":
                    self._json(200, _agent_dashboard(app.runtime)); return
                if path == "/api/objectives":
                    if app.runtime.agents is None:
                        self._json(200, []); return
                    self._json(200, app.runtime.agents.objectives(limit=200)); return
                if path == "/api/attention":
                    if app.runtime.agents is None:
                        self._json(200, {"projects": []}); return
                    humans = app.runtime.human_tasks.list(state="open", project="", limit=500)
                    self._json(200, app.runtime.agents.attention_snapshot(humans)); return
                if path == "/api/agent-messages":
                    if app.runtime.agents is None:
                        self._json(200, []); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    direction = str((q.get("direction") or ["both"])[0])
                    self._json(200, app.runtime.agents.store.list_messages(project, direction=direction, limit=500)); return
                if path == "/api/agent-contracts":
                    if app.runtime.agents is None:
                        self._json(200, {"contracts": [], "forecasts": []}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    contracts = app.runtime.agents.store.list_contracts(owner_project=project) if project else app.runtime.agents.store.list_contracts()
                    consumed = app.runtime.agents.store.list_contracts(consumer_project=project) if project else []
                    forecasts = app.runtime.agents.store.list_contract_forecasts(owner_project=project, active_only=False, limit=500) if project else app.runtime.agents.store.list_contract_forecasts(active_only=False, limit=500)
                    self._json(200, {"contracts": contracts, "consumed": consumed, "forecasts": forecasts}); return
                if path == "/api/agent-audit":
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    try:
                        limit = int((q.get("limit") or ["500"])[0])
                    except Exception:
                        limit = 500
                    self._json(200, _agent_audit(app.runtime, project=project, limit=limit)); return
                if path == "/api/agent-tasks":
                    if app.runtime.agents is None:
                        self._json(200, []); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    status = str((q.get("status") or [""])[0])
                    try:
                        limit = int((q.get("limit") or ["100"])[0])
                    except Exception:
                        limit = 100
                    self._json(200, app.runtime.agents.store.list_tasks(project, statuses=([status] if status else None), limit=limit)); return
                if path == "/api/agent-capsule":
                    if app.runtime.agents is None:
                        self._json(404, {"error": "project owner agents are disabled"}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    if not project:
                        self._json(400, {"error": "project is required"}); return
                    self._json(200, app.runtime.agents.query_capsule(project)); return
                if path == "/api/human-tasks":
                    q = urllib.parse.parse_qs(parsed.query)
                    state = str((q.get("state") or ["open"])[0])
                    project = str((q.get("project") or [""])[0])
                    try:
                        limit = int((q.get("limit") or ["200"])[0])
                    except Exception:
                        limit = 200
                    rows = app.runtime.human_tasks.list(state=state, project=project, limit=limit)
                    self._json(200, {"tasks": rows, "stats": app.runtime.human_tasks.stats()}); return
                if path == "/api/durable-runs":
                    q = urllib.parse.parse_qs(parsed.query)
                    status = str((q.get("status") or [""])[0])
                    try:
                        limit = int((q.get("limit") or ["100"])[0])
                    except Exception:
                        limit = 100
                    self._json(200, app.runtime.durable_runs.list(limit=limit, status=status)); return
                if path == "/api/creative-status":
                    if app.runtime.creative is None:
                        self._json(200, {"enabled": False}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    environment = str((q.get("environment") or ["production"])[0])
                    self._json(200, app.runtime.creative.status(project, environment)); return
                if path == "/api/configuration":
                    if app.runtime.configuration is None:
                        self._json(200, {"enabled": False, "variables": [], "missing": [], "ready": True}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    environment = str((q.get("environment") or [app.runtime.config.configuration.default_environment])[0])
                    self._json(200, app.runtime.configuration.status(project, environment, discover=True)); return
                if path == "/api/environment-file":
                    if app.runtime.configuration is None:
                        self._json(404, {"error": "configuration manager is disabled"}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    name = str((q.get("name") or [".env"])[0])
                    self._json(200, app.runtime.configuration.read_env_file(name, project=project)); return
                if path == "/api/apps":
                    self._json(200, app.runtime.apps.list(include_status=True)); return
                if path == "/api/deployments":
                    self._json(200, app.runtime.operations.list()); return
                if path == "/api/production-overview":
                    if app.runtime.production is None:
                        self._json(200, {"enabled": False, "project": "", "environments": [], "deployments": [], "incidents": [], "open_incidents": 0}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    project = str((q.get("project") or [""])[0])
                    self._json(200, app.runtime.production.overview(project)); return
                if path == "/api/production-metrics":
                    if app.runtime.production is None:
                        self._json(200, {"snapshots": [], "count": 0}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    deployment = str((q.get("deployment") or [""])[0])
                    project = str((q.get("project") or [""])[0])
                    environment = str((q.get("environment") or [""])[0])
                    try:
                        limit = int((q.get("limit") or ["100"])[0])
                    except Exception:
                        limit = 100
                    self._json(200, app.runtime.production.metrics(deployment=deployment, project=project, environment=environment, limit=limit)); return
                if path == "/api/incidents":
                    if app.runtime.production is None:
                        self._json(200, []); return
                    q = urllib.parse.parse_qs(parsed.query)
                    self._json(200, app.runtime.production.incidents(
                        project=str((q.get("project") or [""])[0]),
                        environment=str((q.get("environment") or [""])[0]),
                        status=str((q.get("status") or [""])[0]),
                    )); return
                if path == "/api/production-logs":
                    if app.runtime.production is None:
                        self._json(404, {"error": "production intelligence is disabled"}); return
                    q = urllib.parse.parse_qs(parsed.query)
                    deployment = str((q.get("deployment") or [""])[0])
                    if not deployment:
                        self._json(400, {"error": "deployment is required"}); return
                    source = str((q.get("source") or ["app"])[0])
                    service = str((q.get("service") or [""])[0])
                    search = str((q.get("search") or [""])[0])
                    level = str((q.get("level") or [""])[0])
                    try:
                        lines = int((q.get("lines") or ["300"])[0])
                    except Exception:
                        lines = 300
                    result = app.runtime.production.logs(deployment=deployment, source=source, service=service, search=search, level=level, lines=lines)
                    result["lines"] = [app.runtime.redactor.redact(str(line)) for line in (result.get("lines") or [])]
                    self._json(200, result); return
                if path == "/api/history":
                    self._json(200, _recent_chat(app.runtime.messages)); return
                if path == "/api/history-days":
                    q = urllib.parse.parse_qs(parsed.query)
                    try:
                        days = int((q.get("days") or ["60"])[0])
                    except Exception:
                        days = 60
                    try:
                        offset = int((q.get("offset") or ["0"])[0])
                    except Exception:
                        offset = 0
                    self._json(200, _journal_history_days(app.runtime, days=days, offset_minutes=offset)); return
                if path == "/api/history-day":
                    q = urllib.parse.parse_qs(parsed.query)
                    day = str((q.get("date") or [""])[0])
                    try:
                        offset = int((q.get("offset") or ["0"])[0])
                    except Exception:
                        offset = 0
                    self._json(200, _journal_day(app.runtime, day, offset_minutes=offset)); return
                if path == "/api/artifacts":
                    q = urllib.parse.parse_qs(parsed.query)
                    try:
                        limit = int((q.get("limit") or ["200"])[0])
                    except Exception:
                        limit = 200
                    kind = str((q.get("kind") or [""])[0])
                    day = str((q.get("day") or [""])[0])
                    self._json(200, app.runtime.artifacts.list(limit=limit, kind=kind, day=day)); return
                if path == "/api/tool-logs":
                    q = urllib.parse.parse_qs(parsed.query)
                    try:
                        limit = int((q.get("limit") or ["400"])[0])
                    except Exception:
                        limit = 400
                    day = str((q.get("day") or [""])[0])
                    self._json(200, _persistent_events(app.runtime, day=day, limit=limit)); return
                if path == "/api/runs":
                    try:
                        q = urllib.parse.parse_qs(parsed.query)
                        limit = int((q.get("limit") or ["100"])[0])
                    except Exception:
                        limit = 100
                    self._json(200, [asdict(ep) for ep in app.runtime.episodes.recent(limit)]); return
                if path == "/api/memory-overview":
                    episodes = [asdict(ep) for ep in app.runtime.episodes.recent(20)]
                    memories = [asdict(m) for m in app.runtime.memory.records[-30:][::-1]]
                    self._json(200, {
                        "episodes_count": app.runtime.episodes.count(),
                        "memories_count": len(app.runtime.memory.records),
                        "episodes": episodes,
                        "memories": memories,
                    }); return
                if path == "/api/sessions":
                    self._json(200, [asdict(x) for x in app.runtime.session_store.list()]); return
                if path == "/api/tools":
                    self._json(200, app.runtime.tools.names()); return
                if path.startswith("/api/artifact-text/"):
                    aid = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                    try:
                        meta = app.runtime.artifacts.metadata(aid)
                        if not meta.get("textual") and int(meta.get("chars") or 0) <= 0:
                            self._json(403, {"error": "artifact is binary"}); return
                        self._json(200, {"id": aid, "metadata": meta, "content": app.runtime.artifacts.read(aid, max_chars=50000)}); return
                    except Exception as exc:
                        self._json(404, {"error": f"{type(exc).__name__}: {exc}"}); return
                if path.startswith("/api/artifact/"):
                    aid = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                    try:
                        meta = app.runtime.artifacts.metadata(aid)
                        payload = app.runtime.artifacts.path_for(aid)
                        if payload.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg"} or str(meta.get("kind") or "") not in {"screenshot", "creative_mockup"}:
                            self._json(403, {"error": "artifact is not a viewable image"}); return
                        mime = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".webp":"image/webp", ".svg":"image/svg+xml"}.get(payload.suffix.lower(), "application/octet-stream")
                        self._text(200, payload.read_bytes(), mime); return
                    except Exception as exc:
                        self._json(404, {"error": f"{type(exc).__name__}: {exc}"}); return
                if path == "/":
                    raw = HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                        "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                        "form-action 'none'",
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw); return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                path = urllib.parse.urlparse(self.path).path
                if not self._authorized():
                    self._json(403, {"error": "forbidden"}); return
                try:
                    data = self._body()
                    if path == "/api/agent-delegate":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        project = str(data.get("project") or "").strip()
                        objective = str(data.get("objective") or "").strip()
                        if not project or not objective:
                            raise ValueError("project and objective are required")
                        result = app.runtime.agents.delegate(
                            project, objective, acceptance=str(data.get("acceptance") or ""),
                            priority=int(data.get("priority", 2) or 2),
                            depends_on=[str(x) for x in (data.get("depends_on") or [])], source="web",
                        )
                        self._json(200, result); return
                    if path == "/api/agent-task-cancel":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.cancel_task(str(data.get("task_id") or "")), app.loop)
                        self._json(200, fut.result(timeout=30)); return
                    if path == "/api/agent-task-retry":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.retry_task(str(data.get("task_id") or "")), app.loop)
                        self._json(200, fut.result(timeout=30)); return
                    if path == "/api/agent-task-priority":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.reprioritize_task(str(data.get("task_id") or ""), int(data.get("priority", 2))), app.loop)
                        self._json(200, fut.result(timeout=30)); return
                    if path == "/api/agent-task-move":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.move_task(str(data.get("task_id") or ""), str(data.get("project") or "")), app.loop)
                        self._json(200, fut.result(timeout=30)); return
                    if path == "/api/agent-dependency":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        task_id = str(data.get("task_id") or "")
                        depends_on = str(data.get("depends_on") or "")
                        result = app.runtime.agents.store.add_dependency(task_id, depends_on)
                        if result.get("project"):
                            app.runtime.agents.kick(str(result.get("project")))
                        self._json(200, {"task": result, "dependency_state": app.runtime.agents.store.dependency_state(task_id)}); return
                    if path == "/api/agent-concurrency":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        owners = data.get("owners")
                        queries = data.get("queries")
                        fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.set_concurrency_limits(
                            owners=(None if owners is None else int(owners)), queries=(None if queries is None else int(queries))
                        ), app.loop)
                        self._json(200, fut.result(timeout=30)); return
                    if path == "/api/workspace-layout":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        if bool(data.get("reset", False)):
                            fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.reset_workspace_layout(), app.loop)
                        elif isinstance(data.get("positions"), dict):
                            fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.set_workspace_layout(data.get("positions") or {}), app.loop)
                        else:
                            project = str(data.get("project") or "").strip()
                            if not project:
                                raise ValueError("project is required")
                            fut = asyncio.run_coroutine_threadsafe(
                                app.runtime.agents.set_workspace_position(project, float(data.get("x")), float(data.get("y"))), app.loop
                            )
                        self._json(200, {"layout": fut.result(timeout=30)}); return
                    if path == "/api/agent-query":
                        if app.runtime.agents is None:
                            raise ValueError("project owner agents are disabled")
                        project = str(data.get("project") or "").strip()
                        question = str(data.get("question") or "").strip()
                        if bool(data.get("deep", False)):
                            fut = asyncio.run_coroutine_threadsafe(app.runtime.agents.deep_query(project, question, from_project=str(data.get("from_project") or "central")), app.loop)
                            result = fut.result(timeout=300)
                        else:
                            result = app.runtime.agents.query_capsule(project, question)
                        self._json(200, result); return
                    if path == "/api/turn":
                        prompt = str(data.get("prompt") or "").strip()
                        if not prompt:
                            raise ValueError("prompt is empty")
                        reason = ReasoningMode(str(data.get("reasoning") or "auto").lower())
                        fut = asyncio.run_coroutine_threadsafe(app.runtime.run_turn(prompt, reasoning=reason), app.loop)
                        answer = fut.result()
                        self._json(200, {"answer": answer, "status": app.runtime.status()}); return
                    if path == "/api/human-task-resolve":
                        task_id = str(data.get("task_id") or "").strip()
                        if not task_id:
                            raise ValueError("task_id is required")
                        result = app.runtime.human_tasks.resolve(
                            task_id, option_id=str(data.get("option_id") or ""), answer=str(data.get("answer") or ""), auto=bool(data.get("auto", False))
                        )
                        awaitable = app.runtime.events.emit("human_task.resolved", task_id=task_id, status=result.get("status"), response=result.get("response"), project=result.get("project", ""))
                        if app.loop.is_running():
                            asyncio.run_coroutine_threadsafe(awaitable, app.loop).result(timeout=5)
                        else:
                            app.loop.run_until_complete(awaitable)
                        self._json(200, result); return
                    if path == "/api/human-task-defer":
                        task_id = str(data.get("task_id") or "").strip()
                        if not task_id:
                            raise ValueError("task_id is required")
                        result = app.runtime.human_tasks.defer(task_id)
                        self._json(200, result); return
                    if path == "/api/human-task-view":
                        task_id = str(data.get("task_id") or "").strip()
                        result = app.runtime.human_tasks.mark_viewed(task_id)
                        self._json(200, result or {"error": "task not found"}); return
                    if path == "/api/project-switch":
                        if app.runtime.projects is None:
                            raise ValueError("projects are disabled")
                        name = str(data.get("name") or "").strip()
                        if not name:
                            raise ValueError("project name is required")
                        result = app.runtime.projects.switch(name)
                        awaitable = app.runtime.events.emit("project.switched", project=name, path=result.get("path", ""))
                        asyncio.run_coroutine_threadsafe(awaitable, app.loop).result(timeout=5)
                        self._json(200, result); return
                    if path == "/api/configuration-save":
                        if app.runtime.configuration is None:
                            raise ValueError("configuration manager is disabled")
                        entries = data.get("entries") or []
                        if not isinstance(entries, list):
                            raise ValueError("entries must be a list")
                        result = app.runtime.configuration.set_values(
                            entries, project=str(data.get("project") or ""),
                            environment=str(data.get("environment") or app.runtime.config.configuration.default_environment),
                            materialize=bool(data.get("materialize", True)),
                        )
                        awaitable = app.runtime.events.emit("configuration.updated", project=result.get("project"), environment=result.get("environment"), keys=[str(x.get("name") or "") for x in entries])
                        if app.loop.is_running():
                            asyncio.run_coroutine_threadsafe(awaitable, app.loop).result(timeout=5)
                        else:
                            app.loop.run_until_complete(awaitable)
                        status = app.runtime.configuration.status(result.get("project", ""), result.get("environment", "production"), discover=False)
                        configured_keys = {str(v.get("name") or "") for v in status.get("variables") or [] if v.get("configured")}
                        completed = app.runtime.human_tasks.complete_configuration(project=result.get("project", ""), environment=result.get("environment", "production"), configured_keys=configured_keys)
                        for task in completed:
                            ev = app.runtime.events.emit("human_task.resolved", task_id=task.get("id"), status=task.get("status"), project=task.get("project", ""), response=task.get("response"))
                            if app.loop.is_running(): asyncio.run_coroutine_threadsafe(ev, app.loop).result(timeout=5)
                            else: app.loop.run_until_complete(ev)
                        result["completed_human_tasks"] = [x.get("id") for x in completed]
                        self._json(200, result); return
                    if path == "/api/configuration-test":
                        if app.runtime.configuration is None:
                            raise ValueError("configuration manager is disabled")
                        result = app.runtime.configuration.test_connection(
                            str(data.get("service") or ""), project=str(data.get("project") or ""),
                            environment=str(data.get("environment") or app.runtime.config.configuration.default_environment),
                        )
                        awaitable = app.runtime.events.emit("configuration.test", service=result.get("service"), ok=bool(result.get("ok")), error=result.get("error", ""))
                        if app.loop.is_running():
                            asyncio.run_coroutine_threadsafe(awaitable, app.loop).result(timeout=5)
                        else:
                            app.loop.run_until_complete(awaitable)
                        self._json(200 if result.get("ok") else 400, result); return
                    if path == "/api/environment-file":
                        if app.runtime.configuration is None:
                            raise ValueError("configuration manager is disabled")
                        result = app.runtime.configuration.write_env_file(
                            str(data.get("name") or ".env"), str(data.get("content") or ""), project=str(data.get("project") or ""),
                        )
                        environment = "production" if str(data.get("name") or ".env") == ".env" else str(data.get("name") or ".env").removeprefix(".env.") or "production"
                        status = app.runtime.configuration.status(result.get("project", ""), environment, discover=False)
                        configured_keys = {str(v.get("name") or "") for v in status.get("variables") or [] if v.get("configured")}
                        completed = app.runtime.human_tasks.complete_configuration(project=result.get("project", ""), environment=environment, configured_keys=configured_keys)
                        result["completed_human_tasks"] = [x.get("id") for x in completed]
                        self._json(200, result); return
                    if path == "/api/configuration-resume":
                        if app.runtime.configuration is None:
                            raise ValueError("configuration manager is disabled")
                        project = str(data.get("project") or "")
                        environment = str(data.get("environment") or app.runtime.config.configuration.default_environment)
                        pending = app.runtime.configuration.resume_pending(project, environment)
                        if not pending:
                            raise ValueError("there is no pending configuration request to resume")
                        prompt = (
                            "The required project configuration has now been supplied through the trusted Configuration UI. "
                            "Resume the blocked work from the previous turn. Recheck configuration status and connection tests as appropriate; "
                            "do not ask for secret values in chat. Pending reason: " + str(pending.get("reason") or "configuration supplied")
                        )
                        fut = asyncio.run_coroutine_threadsafe(app.runtime.run_turn(prompt, reasoning=ReasoningMode.AUTO), app.loop)
                        answer = fut.result()
                        self._json(200, {"answer": answer, "status": app.runtime.status()}); return
                    if path == "/api/production-snapshot":
                        if app.runtime.production is None:
                            raise ValueError("production intelligence is disabled")
                        result = app.runtime.production.snapshot(
                            deployment=str(data.get("deployment") or ""), project=str(data.get("project") or ""),
                            environment=str(data.get("environment") or "production"), persist=True, detect=True,
                        )
                        self._json(200, result); return
                    if path == "/api/incident-update":
                        if app.runtime.production is None:
                            raise ValueError("production intelligence is disabled")
                        result = app.runtime.production.update_incident(
                            str(data.get("incident_id") or ""), status=str(data.get("status") or ""),
                            severity=str(data.get("severity") or ""), note=str(data.get("note") or ""),
                        )
                        self._json(200, result); return
                    if path == "/api/app-action":
                        name = str(data.get("name") or "").strip()
                        action = str(data.get("action") or "").strip().lower()
                        if not name:
                            raise ValueError("name is required")
                        if action == "start":
                            result = app.runtime.apps.start(name, wait_timeout_s=app.runtime.config.apps.wait_timeout_s)
                        elif action == "restart":
                            result = app.runtime.apps.restart(name)
                        elif action == "stop":
                            result = app.runtime.apps.stop(name)
                        elif action == "open":
                            result = app.runtime.apps.open(name)
                        elif action == "logs":
                            result = app.runtime.apps.logs(name, tail=300)
                        else:
                            raise ValueError("unsupported app action")
                        self._json(200, result); return
                    self._json(404, {"error": "not found"})
                except Exception as exc:
                    self._json(400, {"error": f"{type(exc).__name__}: {exc}"})

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        actual_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="term6-web")
        self.thread.start()
        if self.loop.is_running() and app.runtime.config.autonomy.enabled:
            app.runtime._autonomy_future = asyncio.run_coroutine_threadsafe(app.runtime.autonomy_loop(), self.loop)
        if self.loop.is_running() and app.runtime.agents is not None and app.runtime.config.agents.auto_start:
            app.runtime._agents_future = asyncio.run_coroutine_threadsafe(app.runtime.agents.scheduler_loop(), self.loop)
        return f"http://127.0.0.1:{actual_port}/?token={urllib.parse.quote(self.token)}"

    def close(self) -> None:
        fut = getattr(self.runtime, "_autonomy_future", None)
        if fut is not None:
            fut.cancel()
            self.runtime._autonomy_future = None
        agents_fut = getattr(self.runtime, "_agents_future", None)
        if agents_fut is not None:
            agents_fut.cancel()
            self.runtime._agents_future = None
        if self.server:
            self.server.shutdown(); self.server.server_close()
