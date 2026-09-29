from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .doctor import run_doctor, run_selftest
from .models import ReasoningMode
from .providers.base import ProviderUnavailable
from .runtime import AgentRuntime
from .state.sessions import SessionCorruptionError
from .state.checkpoints import CheckpointCorruptionError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="term5",
        description="term_6 v6.2-ui2 — Project Owner control plane with durable multi-agent orchestration, queues and state capsules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  term5
  term5 --doctor
  term5 --selftest
  term5 --once 'inspect this repository'
  term5 --reasoning high --once 'debug the failing authentication path'
  term5 --session auth-debug --resume
  term5 --recover
  term5 --web
""",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--root", default=".", help="workspace root (default: current directory)")
    p.add_argument("--config", default=None, help="term5 TOML configuration path")
    p.add_argument("--api-key", default=None, help="DeepSeek API key; prefer DEEPSEEK_API_KEY")
    p.add_argument("--model", default=None, help="override model name")
    p.add_argument("--reasoning", choices=[m.value for m in ReasoningMode], default=None)
    p.add_argument("--once", default=None, help="run one prompt and exit")
    p.add_argument("--no-tools", action="store_true", help="disable model tool calls for --once/console turns")
    p.add_argument("--resume", action="store_true", help="resume default or --session NAME local session")
    p.add_argument("--recover", action="store_true", help="recover the last incomplete turn from its local safe-state checkpoint")
    p.add_argument("--session", default=None, help="named local session (stored under .term5/sessions)")
    p.add_argument("--list-sessions", action="store_true", help="list local sessions and exit")
    p.add_argument("--web", action="store_true", help="run token-gated loopback web UI")
    p.add_argument("--no-auto-plan", action="store_true", help="disable automatic executive DAG preflight")
    p.add_argument("--no-product-plan", action="store_true", help="disable automatic product-archetype planning/critique")
    p.add_argument("--web-port", type=int, default=None, help="loopback web UI port (0 = random)")
    p.add_argument("--doctor", action="store_true", help="run local health/invariant checks and exit")
    p.add_argument("--selftest", action="store_true", help="run doctor plus offline 5.2 selftests")
    p.add_argument("--status-json", action="store_true", help="print startup status as JSON and exit")
    p.add_argument("--doctor-json", action="store_true", help="run doctor and emit machine-readable JSON")
    return p


def _apply_cli(cfg, args) -> None:
    if args.api_key:
        cfg.api_key = args.api_key
    if args.model:
        cfg.model.model = args.model
    if args.reasoning:
        cfg.reasoning.default = args.reasoning
    if args.web:
        cfg.ui.web_enabled = True
    if args.no_auto_plan:
        cfg.executive.auto_plan = False
    if args.no_product_plan:
        cfg.skills.auto_product_plan = False
    if args.web_port is not None:
        cfg.ui.web_port = args.web_port


async def _run(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.root), args.config)
    _apply_cli(cfg, args)
    try:
        runtime = AgentRuntime(cfg, resume=args.resume, session_name=args.session, recover_checkpoint=args.recover)
    except (SessionCorruptionError, CheckpointCorruptionError) as exc:
        print(f"Local state error: {exc}", file=sys.stderr)
        return 3

    if args.list_sessions:
        sessions = runtime.session_store.list()
        if not sessions:
            print("No local sessions.")
        for s in sessions:
            print(f"{s.name:24} messages={s.messages:<5} modified={s.modified_at}")
        return 0
    if args.status_json:
        print(json.dumps(runtime.status(), indent=2))
        return 0
    if args.doctor or args.selftest or args.doctor_json:
        report = await (run_selftest(runtime) if args.selftest else run_doctor(runtime))
        print(json.dumps(report.as_dict(), indent=2) if args.doctor_json else report.text())
        if report.critical_failures and cfg.security.fail_closed_on_critical_doctor:
            return 2
        return 1 if report.failures else 0
    if args.recover and runtime.recovered_checkpoint and args.once is None and runtime.recovery_prompt:
        try:
            answer = await runtime.run_turn(runtime.recovery_prompt, use_tools=not args.no_tools)
            print("[recovered incomplete turn]")
            print(answer)
        except ProviderUnavailable as exc:
            print(f"Provider unavailable while recovering: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Recovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    if args.once is not None:
        try:
            answer = await runtime.run_turn(args.once, use_tools=not args.no_tools)
        except ProviderUnavailable as exc:
            print(f"Provider unavailable: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"term5 error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(answer)
        return 0
    if cfg.ui.web_enabled:
        return await web_mode(runtime)
    return await interactive(runtime, use_tools=not args.no_tools)


HELP = """Commands:
  /help                         show commands
  /status                       runtime/model/index/memory/cost status
  /agents                        list Project Owner Agents and queue counts
  /agent PROJECT                 inspect one Project Owner, queue and capsule revision
  /agent-tasks [PROJECT]         list durable owner tasks
  /context                      working-memory/token/GC status
  /tools                        list registered tools and capability requirements
  /doctor                       run local invariant checks
  /reason auto|none|low|high|max
  /memory                       show local memory index
  /executive [on|off]           show/toggle automatic executive preflight
  /plan                          show the last automatic executive plan
  /skills                        list procedural product/framework/capability/quality skills
  /skill NAME                    inspect one procedural skill
  /product                       show the current automatic product blueprint
  /product-audit                 run a HIGH-reasoning source-based completeness audit
  /impact PATH [DEPTH]           show bounded imported-by impact and likely tests
  /verify PATH [PATH...]         deterministic syntax/parse verification
  /tests [TARGET...]             run opt-in pytest/unittest targets
  /sessions                     list named local sessions
  /checkpoint                   show current crash-recovery checkpoint
  /transactions [LIMIT]         show recent local write transactions
  /undo                         undo latest compatible committed transaction
  /redo                         redo latest compatible undone transaction
  /save [NAME]                  save current conversation
  /load NAME                    load a named local session
  /fim-preview PATH START:END INSTRUCTION
  /fim PATH START:END INSTRUCTION
  /clear                        clear conversation session (not durable memory)
  /exit                         quit

  /docker                       inspect local Docker Engine/Compose
  /apps                         list registered local applications
  /app-create-flask NAME PATH PORT  scaffold/build/start Flask+Celery+Mongo and open
  /app-status NAME              inspect app containers + HTTP health
  /app-start NAME [build]       start app; optional build
  /app-stop NAME                stop app without deleting volumes
  /app-restart NAME             restart app
  /app-logs NAME [SERVICE]      recent Compose logs
  /app-open NAME                open healthy app in host browser

  /ops                           inspect Git/Nginx/Certbot/deployment readiness
  /deployments                   list registered deployments
  /deploy-register NAME APP DOMAIN PORT [tls]
  /deploy NAME [EMAIL]           health-gated deploy; EMAIL enables requested TLS issuance
  /rollback NAME [COMMIT]        rollback to previous/explicit recorded Git release
  /nginx                         inspect Nginx config + managed sites
  /tls [DOMAIN]                  inspect Certbot/certificate state
  /git-head                      show HEAD/branch/clean state

Anything else is sent as a normal user turn. Human collaboration, Creative Studio, browser/vision, and production operations remain capability-gated.
"""


async def web_mode(runtime: AgentRuntime) -> int:
    from .ui import LocalWebApp
    app = LocalWebApp(runtime, asyncio.get_running_loop(), runtime.config.ui.web_host, runtime.config.ui.web_port)
    url = app.start()
    print(f"term_6 {__version__} local web UI: {url}")
    print("loopback-only · token-gated · Ctrl+C to stop · no cloud persistence")
    if runtime.config.ui.open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        return 0
    finally:
        app.close()


def _fim_parts(rest: str):
    parts = shlex.split(rest)
    path = parts[0]
    a, b = parts[1].split(":", 1)
    instruction = " ".join(parts[2:]).strip()
    if not instruction:
        raise ValueError("missing instruction")
    return path, int(a), int(b), instruction


async def interactive(runtime: AgentRuntime, *, use_tools: bool = True) -> int:
    print(f"term_6 {__version__} — multi-agent collaborative autonomous engineering studio")
    print(f"workspace: {runtime.config.root}")
    print(f"model: {runtime.config.model.model} | reasoning: {runtime.forced_reasoning.value} | tools: {len(runtime.tools.names())}")
    print(f"session: {runtime.session_name or 'default'} | FIM: {'on' if runtime.config.fim.enabled else 'off'} | apps: {'on' if runtime.config.apps.enabled else 'off'} | ops: {'on' if runtime.config.operations.enabled else 'off'} | vision: {'on' if runtime.config.vision.enabled else 'off'} | autonomy: {'on' if runtime.config.autonomy.enabled else 'off'}")
    if runtime.config.diagnostics:
        print("config warnings: " + "; ".join(runtime.config.diagnostics))
    print("Type /help for commands.\n")
    if runtime.agents is not None and runtime.config.agents.auto_start:
        asyncio.create_task(runtime.agents.scheduler_loop(), name="term6-owner-scheduler")
    while True:
        try:
            # Keep the event loop free so Project Owner workers can continue while the console waits for input.
            line = (await asyncio.to_thread(input, "term6> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if not line.startswith("/"):
            try:
                answer = await runtime.run_turn(line, use_tools=use_tools)
                print(answer)
            except ProviderUnavailable as exc:
                print(f"Provider unavailable: {exc}")
            except KeyboardInterrupt:
                print("Cancelled.")
            except Exception as exc:
                print(f"Error: {type(exc).__name__}: {exc}")
            continue

        cmd, _, rest = line.partition(" ")
        cmd = cmd.lower(); rest = rest.strip()
        if cmd in {"/exit", "/quit"}: return 0
        if cmd == "/help": print(HELP)
        elif cmd == "/status": print(json.dumps(runtime.status(), indent=2))
        elif cmd == "/agents":
            print(json.dumps(runtime.agents.snapshot(), indent=2) if runtime.agents is not None else "Project Owner Agents are disabled.")
        elif cmd == "/agent":
            if runtime.agents is None:
                print("Project Owner Agents are disabled.")
            elif not rest:
                print("Usage: /agent PROJECT")
            else:
                agent = runtime.agents.store.get_agent(rest)
                print(json.dumps({"agent": agent, "capsule": runtime.agents.store.get_capsule(rest), "tasks": runtime.agents.store.list_tasks(rest, limit=50)}, indent=2) if agent else f"Unknown Project Owner: {rest}")
        elif cmd == "/agent-tasks":
            print(json.dumps(runtime.agents.store.list_tasks(rest, limit=100), indent=2) if runtime.agents is not None else "Project Owner Agents are disabled.")
        elif cmd == "/context": print(json.dumps(runtime.working_memory.stats(runtime.messages), indent=2))
        elif cmd == "/tools": print(runtime.tools.describe())
        elif cmd == "/memory": print(runtime.memory.index_text(limit=100))
        elif cmd == "/executive":
            if not rest:
                print("Executive auto-plan:", "on" if runtime.config.executive.auto_plan else "off")
            elif rest.lower() in {"on", "off"}:
                runtime.config.executive.auto_plan = rest.lower() == "on"
                print("Executive auto-plan:", rest.lower())
            else:
                print("Usage: /executive [on|off]")
        elif cmd == "/plan":
            print(json.dumps(runtime.last_executive_plan, indent=2) if runtime.last_executive_plan else "No automatic executive plan has run in this session.")
        elif cmd == "/skills":
            rows = [runtime.skills.describe(name) for name in runtime.skills.names()]
            print("\n".join(f"{r['name']:22} [{r['category']}] {r['description']}" for r in rows if r))
        elif cmd == "/skill":
            if not rest:
                print("Usage: /skill NAME")
            else:
                data = runtime.skills.describe(rest)
                print(json.dumps(data, indent=2) if data else f"Unknown skill: {rest}")
        elif cmd == "/product":
            print(json.dumps(runtime.product.current(), indent=2) if runtime.product.current() else "No product blueprint has been prepared in this session.")
        elif cmd == "/product-audit":
            try:
                result = await runtime.tools.execute("product_audit", {})
                print(result.content)
            except Exception as exc:
                print(f"Product audit error: {exc}")
        elif cmd == "/impact":
            parts = shlex.split(rest)
            if not parts:
                print("Usage: /impact PATH [DEPTH]")
            else:
                try:
                    depth = int(parts[1]) if len(parts) > 1 else 2
                    print(json.dumps(runtime.graph.impact_map(parts[0], depth=depth), indent=2))
                except Exception as exc:
                    print(f"Impact error: {exc}")
        elif cmd == "/verify":
            parts = shlex.split(rest)
            if not parts:
                print("Usage: /verify PATH [PATH...]")
            else:
                try:
                    report = runtime.verifier.files([runtime.guard.resolve(x, allow_root=False) for x in parts])
                    print(report.text())
                except Exception as exc:
                    print(f"Verify error: {exc}")
        elif cmd == "/tests":
            report = runtime.verifier.tests(shlex.split(rest))
            print(report.text())
        elif cmd == "/sessions":
            rows = runtime.session_store.list()
            print("\n".join(f"{s.name:24} messages={s.messages:<5} modified={s.modified_at}" for s in rows) or "No local sessions.")
        elif cmd == "/checkpoint":
            print(json.dumps(runtime.checkpoints.describe(), indent=2) if runtime.checkpoints.describe() else "No incomplete-turn checkpoint.")
        elif cmd == "/transactions":
            try:
                limit = int(rest) if rest else 20
                print(json.dumps(runtime.transactions.history(limit=limit), indent=2))
            except Exception as exc:
                print(f"Transaction history error: {exc}")
        elif cmd == "/undo":
            try:
                txid, changed = runtime.transactions.undo_last(); runtime.graph.refresh()
                report = runtime.verifier.files([runtime.guard.resolve(x, allow_root=False) for x in changed])
                print(f"Undid {txid}: {', '.join(changed)}\n{report.text()}")
            except Exception as exc:
                print(f"Undo rejected: {exc}")
        elif cmd == "/redo":
            try:
                txid, changed = runtime.transactions.redo_last(); runtime.graph.refresh()
                report = runtime.verifier.files([runtime.guard.resolve(x, allow_root=False) for x in changed])
                print(f"Redid {txid}: {', '.join(changed)}\n{report.text()}")
            except Exception as exc:
                print(f"Redo rejected: {exc}")
        elif cmd == "/save":
            print("Saved:", runtime.save_session(rest or None))
        elif cmd == "/load":
            if not rest: print("Usage: /load NAME")
            else: print("Loaded." if runtime.load_session(rest) else "Session not found.")
        elif cmd == "/clear":
            runtime.clear_session(); print("Conversation session cleared; durable memory preserved.")
        elif cmd == "/reason":
            if not rest:
                print("Current reasoning policy:", runtime.forced_reasoning.value); continue
            try: mode = ReasoningMode(rest.lower())
            except ValueError:
                print("Usage: /reason auto|none|low|high|max"); continue
            runtime.set_reasoning(mode); print("Reasoning policy:", mode.value)
        elif cmd == "/docker":
            print(json.dumps(runtime.apps.docker_status(), indent=2))
        elif cmd == "/apps":
            print(json.dumps(runtime.apps.list(include_status=False), indent=2))
        elif cmd == "/app-create-flask":
            parts = shlex.split(rest)
            if len(parts) != 3:
                print("Usage: /app-create-flask NAME PATH PORT")
            else:
                try:
                    data = runtime.apps.create_flask(name=parts[0], path=parts[1], port=int(parts[2]), celery=True, mongodb=True, start=True, open_browser=True, wait_timeout_s=runtime.config.apps.wait_timeout_s)
                    runtime.graph.refresh()
                    print(json.dumps(data, indent=2))
                except Exception as exc:
                    print(f"App creation error: {exc}")
        elif cmd == "/app-status":
            if not rest: print("Usage: /app-status NAME")
            else:
                try: print(json.dumps(runtime.apps.status(rest), indent=2))
                except Exception as exc: print(f"App status error: {exc}")
        elif cmd == "/app-start":
            parts = shlex.split(rest)
            if not parts: print("Usage: /app-start NAME [build]")
            else:
                try: print(json.dumps(runtime.apps.start(parts[0], build=(len(parts)>1 and parts[1].lower()=="build"), wait_timeout_s=runtime.config.apps.wait_timeout_s), indent=2))
                except Exception as exc: print(f"App start error: {exc}")
        elif cmd == "/app-stop":
            if not rest: print("Usage: /app-stop NAME")
            else:
                try: print(json.dumps(runtime.apps.stop(rest), indent=2))
                except Exception as exc: print(f"App stop error: {exc}")
        elif cmd == "/app-restart":
            if not rest: print("Usage: /app-restart NAME")
            else:
                try: print(json.dumps(runtime.apps.restart(rest), indent=2))
                except Exception as exc: print(f"App restart error: {exc}")
        elif cmd == "/app-logs":
            parts = shlex.split(rest)
            if not parts: print("Usage: /app-logs NAME [SERVICE]")
            else:
                try: print(runtime.apps.logs(parts[0], service=(parts[1] if len(parts)>1 else ""))["output"])
                except Exception as exc: print(f"App logs error: {exc}")
        elif cmd == "/app-open":
            if not rest: print("Usage: /app-open NAME")
            else:
                try: print(json.dumps(runtime.apps.open(rest), indent=2))
                except Exception as exc: print(f"App open error: {exc}")
        elif cmd == "/ops":
            try: print(json.dumps(runtime.operations.status(), indent=2))
            except Exception as exc: print(f"Operations status error: {exc}")
        elif cmd == "/deployments":
            try: print(json.dumps(runtime.operations.list(), indent=2))
            except Exception as exc: print(f"Deployment list error: {exc}")
        elif cmd == "/deploy-register":
            parts = shlex.split(rest)
            if len(parts) < 4:
                print("Usage: /deploy-register NAME APP DOMAIN PORT [tls]")
            else:
                try:
                    item = runtime.operations.register(name=parts[0], app_name=parts[1], domain=parts[2], upstream_port=int(parts[3]), tls=(len(parts)>4 and parts[4].lower()=="tls"))
                    print(json.dumps({"name":item.name,"app_name":item.app_name,"domain":item.domain,"upstream_port":item.upstream_port,"tls":item.tls}, indent=2))
                except Exception as exc: print(f"Deployment registration error: {exc}")
        elif cmd == "/deploy":
            parts = shlex.split(rest)
            if not parts: print("Usage: /deploy NAME [EMAIL]")
            else:
                try:
                    email = parts[1] if len(parts)>1 else ""
                    print(json.dumps(runtime.operations.deploy(parts[0], build=True, configure_nginx=True, issue_tls=bool(email), email=email, auto_rollback=runtime.config.operations.auto_rollback), indent=2))
                except Exception as exc: print(f"Deployment error: {exc}")
        elif cmd == "/rollback":
            parts = shlex.split(rest)
            if not parts: print("Usage: /rollback NAME [COMMIT]")
            else:
                try: print(json.dumps(runtime.operations.rollback(parts[0], target=(parts[1] if len(parts)>1 else "")), indent=2))
                except Exception as exc: print(f"Rollback error: {exc}")
        elif cmd == "/nginx":
            try:
                data = runtime.operations.nginx.status(); data["managed_sites"] = runtime.operations.nginx.managed_sites(); print(json.dumps(data, indent=2))
            except Exception as exc: print(f"Nginx error: {exc}")
        elif cmd == "/tls":
            try: print(json.dumps(runtime.operations.tls.status(rest), indent=2))
            except Exception as exc: print(f"TLS error: {exc}")
        elif cmd == "/git-head":
            try: print(json.dumps({"head":runtime.operations.git.head(),"branch":runtime.operations.git.branch(),"status":runtime.operations.git.status()}, indent=2))
            except Exception as exc: print(f"Git error: {exc}")
        elif cmd == "/doctor":
            report = await run_doctor(runtime); print(report.text())
        elif cmd in {"/fim", "/fim-preview"}:
            try:
                path, a, b, instruction = _fim_parts(rest)
                result = await (runtime.fim_preview(path, a, b, instruction) if cmd == "/fim-preview" else runtime.fim_edit(path, a, b, instruction))
                print(result.content)
            except Exception as exc:
                print(f"Usage: {cmd} PATH START:END INSTRUCTION\nError: {exc}")
        else:
            print("Unknown command. Type /help.")


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = asyncio.run(_run(args))
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)
