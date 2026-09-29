# Changelog

## 6.2.0+ui4 — v6.2-ui4

- Added selection-scoped live operational feedback to every Workspace Inspector type.
- Wired Inspector updates directly to the existing activity event stream for immediate UI feedback.
- Added inter-agent response previews to Inspector feedback without adding another poller.
- Added Inspector feedback regression and doctor gates.

## 6.2.0+ui3 — v6.2-ui3

- Replaced CSP-blocked inline spatial positioning with SVG/foreignObject geometry.
- Kept strict `style-src 'self'` without `unsafe-inline`.
- Restored functional non-overlapping Project nodes and persisted pointer dragging.
- Populated the central coordinator Inspector with objectives, handoffs and runtime state.
- Centered compact rail, dock, workspace and entity icons.
- Redesigned Inbox around attention and grouped responses.
- Redesigned Operations around environments, incidents, releases and an Evidence Console.
- Removed remaining UI inline style mutations, including progress widths and textarea height.
- Added Chromium geometry/overflow smoke validation.

## 6.2.0+ui2 — v6.2-ui2

- Audited all 14 workbench pages and normalized content width, spacing, wrapping, panels and responsive behavior.
- Replaced Workspace ring placement with collision-aware packed project footprints.
- Added pointer-drag project positioning with collision resolution.
- Persisted personalized Workspace layout in `orchestrator_settings.workspace_layout_v2` with Reset Layout support.
- Froze auto-layout positions during drag and throttled redraws with requestAnimationFrame.
- Added live inter-agent message/response synchronization without a second polling loop.
- Added recent live messages and responses to the Project Inspector.
- Added v6.2-ui2 UI/layout/message regression tests and doctor gate.

## 6.2.0+ui1 — v6.2-ui

- Replaced wide sidebar/text island with compact icon rail and centered hover-labelled icon dock.
- Added history popover and Cmd/Ctrl+K command jump.
- Reworked Workspace project systems with intuitive icons and progressive detail.
- Added collision-aware expanding multi-ring project placement.
- Low zoom now uses icon-only project nodes with hover labels; selected high-zoom projects reveal queue chips.
- Moved organization timeline into an on-demand overlay.
- Removed the hard Project Owner iteration limit; legacy owner_max_iterations config is ignored.
- Added v6.2-ui regression tests and doctor gates for modern shell and uncapped owner autonomy.

## 6.2.0 — v6.2

- Added spatial Organization Workspace with Systems, Objectives and Dependencies lenses.
- Added semantic zoom, persistent entity inspector and always-on central command bar.
- Added durable organization-level objectives and task `objective_id` inheritance across owner-to-owner delegation.
- Added Agent Inbox and autonomous/coordinated/human attention model.
- Added worker operational telemetry for context budget, model iteration and current tool without exposing private reasoning.
- Added direct task reassignment and drag-to-create dependency edges.
- Enforced coordinator-only central tool surface whenever registered Project Owners exist.
- Skipped detailed product/improvement/executive implementation preflights in central coordinator mode.
- Removed `agent_wait` from the central coordinator surface and added a small central coordination iteration budget.
- Added fast `/api/status-lite` heartbeat and removed abort-error status toasts.
- Added v6.2 spatial/coordinator/objective/status regression tests; full source suite now 186 tests.

## 6.1.0 — v6.1

- Added full Project Owners control-plane UI with project systems, owner/query/task orbits and click-through drill-down.
- Added per-project queues, persistent timelines, typed messages, State Capsules and contract views.
- Added cross-project task dependency graph.
- Added queue reprioritization, cooperative cancellation and retry controls.
- Added live, durable owner/query concurrency limits.
- Added immediate previous-process task recovery on runtime restart plus recovery telemetry.
- Added Project Owner operational feedback in the main chat using the existing single event poller.
- Added global owner audit/dashboard HTTP APIs while preserving loopback/token gating.
- Added seven RC/control-plane regression tests; full suite now includes multi-project dependency, recovery and cancellation scenarios.

## 6.1.0b1 — v6.1-beta

- Added typed Project Owner message mesh with state queries, analysis queries, change requests, dependency requests, task completion/block notifications, and contract notifications.
- Added durable cross-project dependency edges and immediate owner wakeup on prerequisite completion.
- Fixed Project Owner cross-project read routing while preserving strict source mutation isolation and sender identity binding.
- Added versioned integration contracts, consumer subscriptions, contract forecasts, impact analysis, and automatic consumer notifications.
- Added contract state to Project State Capsules and central coordinator context.
- Added beta mesh/contract regression tests.


## 6.1.0a1 — v6.1-alpha

- Added persistent Project Owner Agents for every registered project.
- Added SQLite/WAL orchestration store with durable queues, dependencies and recoverable worker leases.
- Added central coordinator tools for delegation, owner queries, task waiting, status and typed messaging.
- Added per-project current/in-flight/planned/projected State Capsules.
- Added isolated owner episodic memory/artifact stores and bounded per-task owner context.
- Added concurrent execution across different projects with one mutation lane per project.
- Changed central reentrant turns from rejection to queued serialization and kept the web composer available while work runs.
- Added loopback agent HTTP APIs.
- Fixed the stale absolute path in `test_v59_production.py`.


## 6.0.0

- Reframed the runtime as term_6: a collaborative autonomous engineering studio while preserving `term5-local`, `/opt/term5`, `term5.toml` and existing state compatibility.
- Added persistent durable-run orchestration and same-run resume after human decisions.
- Added global **My Tasks** workbench with task badges and open/completed history across projects.
- Added human task types: choose, configure, approve, answer, authorize, review and resolve.
- Added explicit `AUTO_DECIDE`, `DEFER` and `BLOCK` timeout policies; approval tasks cannot auto-decide.
- Integrated Configuration & Secrets with My Tasks without storing secret values in task payloads.
- Added local Creative Studio SVG concepts and optional OpenAI image mockups.
- Added `gpt-image-2.5-flare` as the default image-rendering model with local `OPENAI_API_KEY` resolution/redaction.
- Added creative artifact display in My Tasks and token-gated artifact serving for SVG/PNG mockups.
- Added fallback from missing OpenAI credentials to a deferred Config task plus local SVG concepts.
- Added collaboration/creative context guidance and two procedural skills.
- Added `term6` CLI entry point while retaining `term5`.
- Added autonomy/creative configuration and `TERM6_*` environment overrides.

## 5.9.0

- Added durable development/staging/production environment registry.
- Added bounded production evidence snapshots from app health, public probes, Docker stats, Nginx logs, host capacity and configuration readiness.
- Added site-scoped Nginx access/error logs for managed sites.
- Added durable incidents correlated with project, environment, deployment and commit.
- Added release markers and observing → verified/degraded post-deployment lifecycle.
- Added bounded release verification and environment comparison/promotion evidence.
- Added searchable application/Nginx production logs.
- Added Production Intelligence to `production_readiness`.
- Added Operations workbench island and production HTTP APIs.
- Added optional background monitoring with a bounded sampling interval.
- Added `production-intelligence` procedural skill and 11 typed production tools.
- Prevented unscoped global Nginx traffic from degrading a project-specific release.
- Fixed DNS-preflight deployment failures leaving releases stuck in `deploying`.

## 5.8.0

- Added per-project/per-environment Configuration & Secrets manager.
- Added environment-schema discovery from example env files, Python, JS/TS and Docker Compose.
- Added trusted local secret vault with 0600 permissions and dynamic telemetry redaction.
- Added model-safe `configuration_status`, `configuration_discover`, `configuration_require`, `configuration_set` and `configuration_test` tools.
- Added structured human-input pause state plus Save & Resume continuation.
- Added masked `.env` / `.env.<environment>` editor and automatic Git ignore protection.
- Added typed SMTP, DeepSeek and database/Redis connectivity preflights.
- Added production-readiness configuration checks.
- Added Configuration workbench island and `configuration-secrets` procedural skill.

## 5.7.0

- Added persistent independent Project Registry and active-project scope.
- Existing term_5 applications can be adopted in place; new/cloned projects default under `projects/`.
- Added project-aware Git manager selection and project-scoped deploy/rollback.
- Added typed `git clone`, remote add/set-url, tracking/ahead/behind and first-push upstream support.
- Added typed GitHub CLI provider status/list/view/create tools with separate provider-write gate.
- Added Project Brain goals, decisions and prioritized backlog.
- Added Project workbench island and project switching API.
- Scoped application/workspace intelligence to the active project where applicable.
- Added `project-repository` procedural skill.


## 5.6.0
- Reworked chat history into automatic calendar-day history backed by the full local journal.
- Added grey/disabled empty dates and clickable dates with completed conversation history.
- Added top workspace island: Chat / Files / Tool Logs / Runs.
- Added Files artifact library and dedicated screenshot archive rail.
- Added native inline rendering for screenshot artifact Markdown, including escaped-parenthesis form.
- Added bounded textual artifact preview endpoint.
- Added persistent redacted Tool Logs view backed by events.jsonl.
- Added Runs view backed by compact episodic memory.
- Preserved alpha3 single-poller UI architecture and all 5.5.1 runtime capabilities.

## 5.5.1
- Default model/tool rounds are unlimited (`max_tool_iterations = 0`).
- Added optional wall-clock turn safety and repeated identical tool-batch abort guard.
- Activity UI shows browser opens, screenshot capture, visual audit evidence, vision model calls and bounded findings.
- Added token-gated screenshot artifact viewing in the loopback UI.
- Model-selected next tool actions are surfaced without exposing hidden reasoning.

## 5.5.0

- Consolidated 5.3-alpha3 stable UI with all 5.4 application-intelligence/browser/vision features.
- Added bounded host/server observability and allowlisted systemd service control.
- Added `production_readiness` aggregate preflight.
- Added `server-management` procedural skill.
- Preserved v5.3 Working Memory and v5.2 Docker/Git/Nginx/TLS deployment/rollback behavior.
- Kept loopback/token UI, strict CSP and no generic host or Docker shell.

## 5.4.0-alpha1

- Added deterministic existing-application graph: routes, templates, Jinja relations, CSS/JS selectors and tests.
- Added automatic high-impact improvement planning for broad changes to existing apps.
- Added completion-review gate to reduce long runs that end in minimal, unverified changes.
- Added guarded Playwright/Chromium browser runtime and 11 browser tools.
- Added binary screenshot artifacts.
- Added multimodal `vision_inspect` and before/after `vision_compare`.
- Added `existing-app-improvement` procedural quality skill.
- Added Browser/Vision/Improvement configuration sections and UI system-status visibility.
- Retained v5.3 efficient Working Memory and v5.2 operations stack.


## 5.3.0-alpha2

- Rebuilt web UI as a unified AI + operations workspace.
- Persistent desktop navigation + central workbench + live inspector.
- Responsive mobile bottom navigation and inspector drawer.
- Dedicated Chat, Runs, Apps, Deployments, Product, and Memory views.
- Added command palette and view keyboard shortcuts.
- Added dark/light theme toggle.
- Added recent chat restoration.
- Added `/api/history`, `/api/memory-overview`, `/api/sessions`, `/api/tools`.
- Added rich application/deployment resource cards.
- Added dedicated run dashboard and expanded execution timeline.
- Added product blueprint and working-memory dashboards.
- Moved web CSS/JS to local asset endpoints and tightened page CSP.
- Preserved all v5.3 alpha1 efficient-working-memory behavior.

## 5.3.0-alpha1

- Introduced bounded working memory, episodic summaries, tool artifact archiving, reasoning eviction and dynamic output budgeting.
