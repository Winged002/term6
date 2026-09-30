# term_6

> **A host-resident, coordinator-first autonomous engineering workbench for building, changing, verifying, deploying and operating multiple software projects from one durable workspace.**

**Current release:** `v6.2-ui4`  
**Runtime:** host-resident Python service + local web workbench  
**Execution model:** central coordinator → Project Owners → project-scoped workers/tools  
**State:** durable local orchestration under `.term5/`  
**Access model:** loopback-first web UI, normally reached directly on the host or through SSH port forwarding

`term_6` is designed to act less like a single chat-based coding assistant and more like a small software organization running on your own machine or server. You give it an objective. A central coordinator understands the request, delegates implementation to the correct Project Owner, tracks dependencies between projects, asks the human only when a decision or authorization is actually required, and keeps the resulting work visible in one operational workspace.

The system combines software engineering, project orchestration and production operations. It can inspect and modify registered repositories, run tests, audit rendered applications, work with Git and GitHub, manage Docker applications, prepare Nginx/TLS deployment paths, verify releases and retain durable execution history without turning the central model into an unrestricted root shell.

---

## What is term_6?

`term_6` is a **local autonomous engineering control plane** for one or more software projects.

Instead of giving one model every tool and asking it to solve everything in one long loop, `term_6` separates responsibilities:

- the **human** defines objectives, supplies judgment and authorizes sensitive actions;
- the **central coordinator** understands the organization-wide objective and routes work;
- each registered project has a **Project Owner** responsible for implementation inside that project;
- Project Owners use project-scoped tools and workers for repository inspection, coding, browser auditing, testing, Git and deployment work;
- durable queues, objectives, dependencies, messages, events and recovery state keep the system coherent even when the browser disconnects or a process restarts.

The result is one workspace for interacting with an engineering organization rather than a collection of isolated coding chats.

## Why does it exist?

Most coding assistants are optimized for a single conversational turn: inspect some files, produce changes, answer, and stop. That becomes limiting when the work spans several applications, requires production verification, contains dependencies, needs human approval at specific points, or simply takes longer than one browser session.

`term_6` was built to address those problems directly:

- **Long-running work should be durable.** Objectives and owner work should survive reconnects instead of existing only inside a browser request.
- **Multiple projects need ownership.** A central model should not casually mutate every repository. Work belongs to a Project Owner with project-scoped access.
- **Coordination and implementation are different jobs.** The coordinator should route and reason about dependencies; implementation should happen inside the responsible project context.
- **Human attention should be selective.** The system can continue autonomously when it has enough information and create a human task when judgment, configuration or approval is material.
- **Deployment is part of engineering.** A change is not necessarily finished when a file was edited. Git state, tests, rendered behavior, Docker health, Nginx/TLS, release evidence and rollback matter too.
- **Observability matters.** The human should be able to see objectives, owners, workers, queues, current tools, dependencies and operational events without exposing private model chain-of-thought.
- **Autonomy needs boundaries.** Host-changing actions are typed and capability-gated. The system does not receive a generic unrestricted host shell.

## How does it work?

At a high level, every meaningful request becomes a durable objective and flows through the organization:

```text
Human objective
      │
      ▼
Central Coordinator
understands scope, projects and dependencies
      │
      ├──────────────► Project Owner A ─► workers/tools ─► verify ─► Git/deploy
      │
      ├──────────────► Project Owner B ─► workers/tools ─► verify ─► Git/deploy
      │
      └──────────────► Human task when judgment/approval/configuration is required
                              │
                              ▼
                    same durable objective resumes
      │
      ▼
Production evidence / incidents / owner feedback
      │
      ▼
Coordinator returns the organization-level result to the human
```

When one or more Project Owners are registered, the central coordinator receives a coordinator-focused tool surface. Repository inspection, source editing, browser work, tests, Git mutation and project implementation belong to the Project Owners. This prevents the coordinator from spending a long turn doing implementation work while project agents sit idle.

Project Owner implementation is not constrained by the legacy `owner_max_iterations` setting. Owners continue until the assigned work reaches a meaningful terminal or waiting condition such as completion, cancellation, failure, required human input, an unresolved dependency or a recoverable process interruption. The central coordinator retains a separate small `central_max_iterations` budget so its role stays focused on routing and handoff.

### Durable objectives and dependencies

Every central user turn creates an objective in `.term5/orchestrator.sqlite3`. Delegated work inherits that objective identity, including later owner-to-owner work created after the original central turn has returned.

The Workspace exposes three views over the same organization:

- **Systems** — coordinator, projects, Project Owners, active workers, queues and message connections.
- **Objectives** — all durable work caused by a human request, including work spread across multiple projects.
- **Dependencies** — the durable task DAG; dependency-blocked work resumes when prerequisites complete.

### Human collaboration

`My Tasks` is the explicit human-in-the-loop queue. A task can use one of three no-response policies:

- `AUTO_DECIDE` for safe subjective choices with a reasonable default;
- `DEFER` for information or configuration that can wait while unrelated work continues;
- `BLOCK` for approvals, destructive actions and sensitive authorization.

Silence never grants a blocked approval. Secret values are entered through trusted configuration paths rather than inserted into model-facing task content.

### Verification and production loop

The intended engineering loop is broader than code generation:

```text
understand project
      ↓
inspect source / application graph
      ↓
implement coherent changes
      ↓
run tests
      ↓
render and audit application when applicable
      ↓
DOM / console / network / screenshot / optional vision verification
      ↓
review Git state and create release history
      ↓
Docker build/start/health
      ↓
Nginx / TLS / public probe when permitted
      ↓
release evidence and Production Intelligence
      ↓
verified, degraded, retried or rolled back
```

## Where does it run?

`term_6` is **host-resident**. It runs on the machine that owns or can access the software projects it is responsible for. In production-oriented setups this is typically a Linux development or application server with the required tools already installed.

The runtime deliberately manages the host's existing Git repositories, Docker Engine, Nginx and deployment state rather than running a second Docker daemon inside Docker.

Typical layout:

```text
host / server
├── term_6 runtime
├── .term5/                     durable orchestration and local state
├── project-a/                  Git repository
├── project-b/                  Git repository
├── Docker Engine + Compose
├── Nginx
└── Certbot                     optional TLS operations
```

The web workbench is loopback-first and token-gated. On a remote server, the normal access pattern is SSH port forwarding rather than publishing the control plane directly to the internet:

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

Use the actual loopback port printed by the runtime if it differs from `8765`.

The installed package and service keep the compatibility names `term5-local` and `term5`. A `term6` CLI entry point is also installed. Existing `.term5` state is intentionally reused so an in-place upgrade does not move project source trees or discard accumulated operational state.

## When should you use it?

`term_6` is a strong fit when the work is larger than a single code-generation interaction. Typical examples include:

- maintaining several related applications on one server;
- delegating separate workstreams to independent Project Owners;
- implementing features that cross repositories or depend on another service being changed first;
- auditing and improving an existing application rather than generating a greenfield code sample;
- keeping a long-running engineering task alive across browser reconnects or service restarts;
- asking an agent to build, test, commit, deploy and verify a change as one controlled workflow;
- operating Docker/Nginx/TLS-backed applications through explicit production gates;
- requiring a human decision only at material approval, design, configuration or authorization points;
- retaining a durable record of objectives, execution events, releases, incidents and evidence.

It is intentionally less suited to tasks that require an unrestricted root shell, arbitrary operating-system administration, automatic DNS-provider management or other capabilities outside its typed tool surface.

## What can it do?

### Multi-project engineering

- register, import, create and clone independent projects;
- maintain an active project and project-specific source scope;
- keep durable project goals, decisions and backlog state;
- coordinate multiple Project Owners concurrently across independent repositories;
- enforce one mutation lane per project while allowing cross-project concurrency;
- represent explicit task dependencies and owner-to-owner contracts.

### Code and application intelligence

- map existing application structure before broad edits;
- inspect and modify source through typed file/code tools;
- run project tests and verification workflows;
- audit applications in a guarded Chromium/Playwright browser;
- inspect DOM, console and network behavior;
- capture screenshot artifacts;
- use optional multimodal vision inspection when the configured provider accepts image input;
- retain browser/vision evidence without flooding active model context with binary or verbose artifacts.

### Git and GitHub

- inspect status, diffs, branches, commits and upstream state;
- stage and commit coherent changes;
- fetch, fast-forward pull and push when remote access is enabled;
- work with GitHub through the typed provider adapter;
- create/list/inspect repositories when the corresponding write capability is enabled;
- keep Git mutation, remote Git access and provider-side repository creation behind separate permissions.

### Configuration and secrets

- discover required environment variables;
- distinguish missing configuration from code failure;
- let the human supply values through the trusted Config UI;
- keep secret values out of model-facing tool results;
- create protected environment files and configuration profiles;
- test known service connections without exposing stored secret values to the model.

### Docker and deployment operations

- register and inspect Docker Compose applications;
- build, start, stop and restart managed applications;
- inspect bounded logs and health state;
- prepare and validate Nginx reverse-proxy configuration;
- issue or validate TLS through Certbot when explicitly allowed;
- create release markers and maintain rollback information;
- run configured backup and known migration policies;
- verify public endpoints after deployment.

### Production Intelligence

- distinguish development, staging and production environments;
- collect bounded evidence snapshots from application health, HTTP, Docker, Nginx and host state;
- record release verification state;
- create incidents from concrete observed failures;
- surface production state in the Operations workbench without giving the model a generic monitoring shell.

### Durable working memory

- compact completed tool traces instead of keeping every result in active context;
- archive verbose results under `.term5/artifacts/`;
- retain compact episodic history for relevant later recall;
- keep recent conversation and active execution context bounded;
- expose context/compaction state through diagnostics and the Live Execution UI.

## The workbench

The current workbench is organized around a persistent command surface rather than forcing every action back through a single chat screen:

```text
Chat | Workspace | Inbox | My Tasks | Project | Config | Operations | Files | Tool Logs | Runs
```

The command composer remains available across the workbench. A user can inspect a project, incident, dependency or worker and issue the next organization-level objective without navigating back to Chat first.

### Spatial Workspace

The Workspace represents the engineering system as a navigable organization rather than a flat task table.

- project cards use collision-aware packed placement;
- project positions can be dragged and are persisted across refresh/restart;
- semantic zoom suppresses detail at organization scale and reveals worker/task state when zoomed in;
- task satellites appear around the selected project rather than permanently surrounding every project;
- queued or waiting work can be reassigned to another project through direct manipulation;
- dependency edges can be created in the Dependencies lens;
- moving a task changes ownership but does not grant filesystem access to another project's source tree.

### Persistent Inspector

Selecting the coordinator, a project, Project Owner, worker, task or objective updates the right-side Inspector. It shows operational state such as objective, queue, worker ID, current tool, iteration, bounded context usage, dependencies, status, retry/cancel controls and recent execution events.

The Inspector is an observability surface. It does not expose private model chain-of-thought.

### Inbox and attention model

The Agent Inbox prioritizes coordination and exceptions instead of routine chatter:

```text
● autonomous     no human action required
◆ coordinated    owners are waiting on another owner/dependency
▲ human          human input is required in My Tasks
```

Routine messages remain in durable history without dominating the attention surface.

## Current release — v6.2-ui4

`v6.2-ui4` is the current refinement of the v6.2 spatial organization workspace.

### v6.2-ui4 — live Inspector feedback

Every Workspace selection receives a live operational feedback stream. Coordinator, Project Owner, Task/Worker and Objective inspectors consume activity from the existing activity poll, while inter-agent response text is synchronized through the existing message refresh path. No additional polling loop is introduced.

### v6.2-ui3 — UI reliability

- spatial Workspace geometry is CSP-safe and no longer depends on inline runtime styles;
- Project dragging works under the shipped `style-src 'self'` policy;
- the coordinator Inspector is populated by default;
- workbench icons are explicitly centered;
- Inbox and Operations were redesigned;
- Workspace/Inbox inter-agent responses update without requiring a manual browser refresh;
- route-level layout, spacing, overflow and responsiveness were audited across the workbench.

See `V62_UI3_GATE.md` and `UI_AUDIT.md` for the corresponding UI validation material.

### Status reliability

The lightweight `/api/status-lite` heartbeat avoids the old pattern where the expensive full `/api/status` request could self-abort and incorrectly surface `Status unavailable: signal is aborted without reason`. Heartbeat calls do not overlap, retain the last good snapshot across transient failure and leave heavy Docker/operations/configuration probes on their dedicated pages.

## Security and operational boundaries

`term_6` is intentionally **not** a general autonomous root shell.

Host-changing functionality is exposed through typed tools and separate security gates. Remote Git, local Git mutation, Nginx writes, TLS issuance and other sensitive capabilities can be enabled independently rather than being implied by general agent access.

The runtime does not currently:

- install operating-system packages;
- manage DNS-provider records;
- manage UFW, nftables or firewalld;
- restore database backups automatically;
- run arbitrary migration shell commands;
- expose unrestricted `docker exec`;
- expose a generic host shell;
- guarantee zero-downtime deployment.

The local secret vault provides filesystem isolation and runtime redaction; it should not be described as cryptographic at-rest encryption unless an external encrypted storage layer is used.

## Quick start

From a source checkout:

```bash
cd <term_6-source>
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp term5.toml.example term5.toml
export DEEPSEEK_API_KEY='...'
term5 --selftest
term5 --web
```

The `term5` command remains the compatibility entry point used by existing installations. `term6` is also installed as a CLI entry point.

For production-oriented operations, the host should already provide the software that `term_6` is expected to manage:

```text
Git
Docker Engine + Docker Compose
Nginx
Certbot + Certbot Nginx plugin    # when TLS issuance is needed
```

Writing `/etc/nginx` and issuing certificates normally requires suitable host permissions. `term_6` does not invoke `sudo` or request an interactive password on behalf of the model.

Remote Git authentication must already work non-interactively if remote Git capability is enabled.

## First-run mental model

A useful first interaction is not “open a shell and do whatever is necessary.” Give the coordinator an engineering objective and let it determine which project owns the work.

For example:

```text
Audit the customer-facing application, fix the remaining mobile layout problems,
run its tests, verify the affected pages in the browser, commit the changes and
prepare the production deployment. If another project must change first, create
that dependency instead of modifying its files directly.
```

For a cross-project objective:

```text
Add the new authentication capability to the SSO project and update the client
application to consume it. Coordinate both Project Owners, keep the dependency
explicit, verify each repository independently and return when the integrated
workflow has been validated.
```

## Local state and important paths

| Path | Purpose |
| --- | --- |
| `.term5/orchestrator.sqlite3` | durable objectives, queues, task/dependency and orchestration state |
| `.term5/runs_v6.json` | durable run metadata |
| `.term5/human_tasks.json` | human-in-the-loop tasks |
| `.term5/projects.json` | registered project metadata |
| `.term5/deployments.json` | recorded deployment/release state |
| `.term5/artifacts/` | archived tool output, screenshots and other bounded artifacts |
| `.term5/episodes.jsonl` | compact completed-work memory |
| `.term5/backups/` | deployment backup artifacts when configured |

Existing `.term5` state is reused across compatible upgrades.

## Diagnostics

```bash
term5 --doctor
term5 --doctor-json
term5 --selftest
term5 --status-json
```

Inside the workbench, Live Execution, Tool Logs, Runs, Workspace, Operations and the Inspector provide different views of the same bounded operational state.

## Documentation map

The repository contains deeper documentation for the major subsystems. Start with:

- `docs/SPATIAL_ORGANIZATION_WORKSPACE.md` — v6.2 organization/workspace model;
- `docs/PROJECT_OWNER_AGENTS.md` — Project Owner behavior and worker execution;
- `docs/AGENT_MESH_CONTRACTS.md` — owner-to-owner communication and integration contracts;
- `docs/PROJECT_OWNER_CONTROL_PLANE.md` — durable Project Owner control plane;
- `docs/COLLABORATIVE_AUTONOMY.md` — human tasks and durable v6 collaboration;
- `docs/CREATIVE_STUDIO.md` — optional visual concept workflow;
- `docs/PROJECTS_GIT.md` — project-aware source and Git lifecycle;
- `docs/CONFIGURATION_SECRETS.md` — trusted configuration/secrets workflow;
- `docs/PRODUCTION_INTELLIGENCE.md` — post-deployment evidence and incidents;
- `docs/WORKBENCH.md` — day-to-day web workbench and artifacts;
- `docs/WORKING_MEMORY.md` — bounded context, compaction and episodic memory;
- `docs/BROWSER_VISION.md` — browser and optional multimodal inspection;
- `docs/SERVER_MANAGEMENT.md` and `docs/OPERATIONS.md` — host and deployment operations;
- `MIGRATION.md` — upgrade and compatibility notes.

---

# Capability history and technical lineage

The sections below document the major capability layers retained by the current release. They are useful when upgrading older installations or tracing why a current subsystem exists. The product description, current architecture and installation guidance above should be treated as the primary entry point for new users.

## Inherited v6.0 capabilities

`term_6` is a host-resident collaborative autonomous engineering studio. It retains the project-aware Git/GitHub, Configuration & Secrets, browser/vision, Docker/Nginx/TLS, deployments and Production Intelligence stack built through term_5, then changes the interaction model: work is durable, term_6 can assign small tasks back to the human, and the same objective resumes when those tasks are resolved.

The installed Python package and service remain `term5-local` / `term5` for in-place upgrade compatibility. A `term6` CLI entry point is also installed. Existing `.term5` state is reused; no application source tree is moved.

## v6 headline: COLLABORATIVE AUTONOMY

The core v6 loop is bidirectional:

```text
human objective
      ↓
term_6 durable run
      ↓
autonomous engineering
      ├──────────────→ continue when AI can decide safely
      │
      └──────────────→ My Tasks when human value is material
                           ↓
                   choose / configure / approve / answer
                   authorize / review / resolve
                           ↓
                    same durable run resumes
                           ↓
                code → verify → deploy → observe
```

The top workbench adds a global **My Tasks** island immediately beside Chat:

```text
Chat | My Tasks | Project | Config | Operations | Files | Tool Logs | Runs
```

My Tasks aggregates work from every project. Each task records its project, durable run, kind, blocking state and explicit no-response policy.

### Safe timeout policies

- `AUTO_DECIDE` — only for safe subjective choices with a reasonable default, such as a visual direction. Default timeout is 30 seconds.
- `DEFER` — for configuration/information that can wait. term_6 continues unrelated work and keeps the task open.
- `BLOCK` — for approvals, destructive operations and sensitive authorization. Silence never grants approval.

Approval tasks cannot be created or resolved with `AUTO_DECIDE`. Configuration tasks never carry secret values; they contain variable names and route the user to the trusted Config UI.

### Durable runs

Every ordinary objective gets durable orchestration metadata under `.term5/runs_v6.json`. Human tasks live in `.term5/human_tasks.json`. Both files are written atomically with mode `0600`. When a linked task is resolved, the web-mode autonomy scheduler resumes the same run ID with the original objective and the resolved decision rather than requiring the human to reconstruct the conversation.

### Creative Studio

DeepSeek remains the engineering director. Creative Studio is an optional rendering layer for design decisions:

```text
DeepSeek identifies a material design choice
      ↓
2–4 distinct directions
      ↓
local SVG concepts ── or ── OpenAI image mockups
      ↓
My Tasks choice
      ↓
selected artifact
      ↓
vision inspection
      ↓
real application implementation
      ↓
browser/vision verification
```

Simple concepts can be rendered locally as SVG with no external image provider. Rich mockups can use OpenAI's Images API. The default is `gpt-image-2.5-flare`; the provider key is resolved locally from `OPENAI_API_KEY` or the trusted project Configuration store and is never returned to the model/tool result. If the key is absent, term_6 creates a deferred Config task and can continue with SVG concepts instead of blocking.

New collaboration/creative tools include:

```text
human_task_create
human_task_list
durable_run_list
durable_run_status

creative_status
creative_svg_concepts
creative_image_concepts
```

See `docs/COLLABORATIVE_AUTONOMY.md` and `docs/CREATIVE_STUDIO.md`.

## v5.9 headline: PRODUCTION INTELLIGENCE

v5.9 closes the loop after deployment. Development, staging and production are explicit environment records; deployments create durable release markers; read-only evidence snapshots combine application health, public HTTP latency, Docker resource snapshots, site-scoped Nginx request/error samples, host capacity and configuration readiness; concrete failures become durable incidents tied to project/environment/deployment/commit.

A successful deployment now enters an **observing** state when Production Intelligence is enabled. `release_verify` performs bounded post-deploy samples and marks the release **verified** or **degraded**. Only site-scoped Nginx logs may influence a specific release's 5xx verification; global fallback logs remain visible but are not attributed to one project.

The workbench adds an **Operations** island with environments, releases, incidents, evidence snapshots and bounded searchable app/Nginx logs. Background monitoring is opt-in; when enabled it periodically records snapshots and updates incident state without granting a generic shell or arbitrary monitoring command surface.

```text
source / Git
    ↓
deploy
    ↓
release marker
    ↓
observing
    ↓
app + HTTP + Docker + Nginx + host + config evidence
    ↓
incident detection / release verification
    ↓
verified or degraded
```

See `docs/PRODUCTION_INTELLIGENCE.md`.

## v5.8 headline: CONFIGURATION & SECRETS

v5.8 makes external-service configuration a first-class engineering dependency. term_5 can discover required environment variables, show exactly what is missing, pause work for trusted human input, store secret values outside model context, test configured services, materialize protected environment files and resume the blocked run.

The Configuration workbench supports Production, Staging and Development profiles. Secret fields are masked; environment files are `0600` and automatically Git-ignored. `.env.example` remains trackable. The local secret vault is also `0600`; it provides filesystem isolation and runtime redaction, not cryptographic at-rest encryption.

Model-facing tools only see configuration names/state. A model may request a named secret through `configuration_require`, but cannot retrieve or set the value itself. Typed connection tests resolve values only inside the trusted runtime. Production readiness reports missing required production variables.

See `docs/CONFIGURATION_SECRETS.md`.

## v5.7 headline: PROJECT-AWARE ENGINEERING

v5.7 changes the unit of work from one global workspace repository to **independent engineering projects**. A project owns a source root, Git repository, upstream state, application/deployment mapping, and a compact Project Brain containing durable goals, decisions and backlog items. The active project becomes the default scope for coding, Git, application mapping, browser audits and releases.

Existing registered applications are adopted **in place**; term_5 does not move working source trees during migration. New and cloned projects default to `projects/<name>`. Project metadata is stored locally in `.term5/projects.json`.

The Git lifecycle is now project-aware:

```text
Git/SSH credentials configured on the host
        ↓
project_import / project_create / project_clone
        ↓
active project
        ↓
status / diff / branch / commit / tag
        ↓
fetch / pull --ff-only / push
        ↓
typed GitHub provider adapter (`gh`)
        ↓
create/list/inspect private or public repositories
        ↓
project-scoped deployment release + rollback
```

`git_*` tools accept an optional `project` argument and default to the active project. First pushes can use `git_push(set_upstream=true)`. Remote Git access (`security.allow_git_remote`) and GitHub repository creation (`security.allow_git_provider_write`) remain independent write gates from local Git mutation (`security.allow_git_write`). SSH keys and GitHub tokens remain host credentials and are never injected into model context.

The workbench adds a **Project** island with active-project status, branch/HEAD/upstream sync state, application/deployment/domain mapping, Goals, Decisions, Backlog, registered projects and GitHub authentication status. Switching the project changes the authoritative scope used on subsequent turns.

New project/provider tools include `project_list`, `project_status`, `project_switch`, `project_import`, `project_create`, `project_clone`, `project_update`, `project_unregister`, Project Brain tools, `github_status`, `github_repo_list`, `github_repo_view`, `github_repo_create`, `git_remote_add` and `git_remote_set_url`.

See `docs/PROJECTS_GIT.md`.

## v5.6 headline: DAILY WORKBENCH + ARTIFACT ARCHIVE

v5.6 keeps the full 5.5.1 production-engineering runtime and reorganizes the loopback UI around how a long-lived server agent actually works. Conversation history is chronological by day instead of a list of named chats, screenshot artifacts render natively inside assistant responses, and generated evidence lives in a dedicated archive rather than disappearing into tool traces.

The top workspace island is now:

```text
Chat | Files | Tool Logs | Runs
```

- **Chat** — current conversation plus clickable daily history.
- **Files** — local artifact library with a dedicated screenshot archive rail and bounded previews for textual tool artifacts.
- **Tool Logs** — persisted redacted operational events from coding, browser, vision and production tools.
- **Runs** — compact completed-task episodes with outcome, tools and changed files.

The left rail shows a calendar-like history automatically: Today, Yesterday, recent weekday names, then absolute date + weekday for older entries. Empty days remain visible but grey/unavailable; days with journaled work are clickable. Daily history is read from `.term5/journal.jsonl`, so the full user prompt and final assistant Markdown are preserved without putting old raw tool traces back into active model context.

Screenshot references such as `![documents](art_...)` and the escaped `![documents]\(art_...)` form now render as token-gated inline images. The same screenshots remain available in the Files screenshot archive.

See `docs/WORKBENCH.md`.

## v5.5.1: UNLIMITED EXECUTION + VISIBLE VISION PIPELINE

The default 40-round tool/model ceiling is removed. `scheduler.max_tool_iterations = 0` means unlimited rounds; safety is based on actual repeated loops, optional wall-clock limits, context headroom and failures instead of an arbitrary iteration count.

The Live Execution drawer now makes browser/vision work directly observable: screenshot thumbnails and artifact IDs, browser audit/open state, the vision model in use, vision start/completion/failure, a bounded findings preview, and the model's next selected tool actions. Hidden chain-of-thought remains private.

## v5.5 headline: PRODUCTION ENGINEERING SYSTEM

The intended end-to-end workflow is now:

```text
understand existing project / product request
        ↓
application graph + procedural skills + product/improvement plan
        ↓
coherent source changes + transactions + tests
        ↓
render in guarded browser when applicable
        ↓
DOM/console/network + screenshot/vision verification
        ↓
Git review + commit
        ↓
production_readiness
        ↓
Docker build/start/health
        ↓
validated Nginx + optional TLS
        ↓
public probe + release record
        ↓
rollback path remains known
```

### Stable alpha3 UI

v5.5 deliberately uses the 5.3-alpha3 UI architecture rather than the denser alpha2/5.4 shell: one left navigation rail, one central work area, and one optional Activity drawer. There is one adaptive activity poller (~900 ms while running, ~3 s idle), no `setInterval`, no command palette, and no permanent third column. Apps, Deployments and Memory load only when opened or refreshed.

### Existing-application intelligence

Broad improve/redesign/audit/refactor/UI/UX tasks receive a deterministic application map, a HIGH-reasoning improvement plan, surface-coverage expectations, coherent edit batches, and a completion review that asks whether the result is materially visible rather than merely test-correct. Browser and vision verification are used when available.

### Browser + vision

The guarded browser runtime supports loopback apps by default and optional explicitly allowlisted public hosts. It exposes page open, viewport, DOM, screenshots, interactions, diagnostics and batch audits. Screenshots are stored as binary artifacts; only selected images are sent through the configured multimodal provider path. Vision failure is explicit and does not disable DOM/browser verification.

### Server management

v5.5 adds `server_status`, `server_ports`, `server_service_status`, `server_journal`, `server_audit`, `server_service_control`, and `production_readiness`. Host service mutation is separately gated by `security.allow_server_write` and restricted to `operations.managed_services`; there is no generic command tool.

### Efficient working memory

The v5.3 bounded Working Memory system remains active: completed tool traces are compacted, verbose results are archived under `.term5/artifacts/`, completed work becomes episodic memory, and output budgeting is dynamically limited by reasoning mode and physical context headroom.

See:

- `docs/WEB_UI.md`
- `docs/EXISTING_APP_INTELLIGENCE.md`
- `docs/BROWSER_VISION.md`
- `docs/SERVER_MANAGEMENT.md`
- `docs/OPERATIONS.md`
- `docs/WORKING_MEMORY.md`

## v5.4 headline: EXISTING-APPLICATION CODING INTELLIGENCE

v5.4 focuses on improving applications that already exist. The runtime now maps the application before broad edits, creates an explicit improvement plan with surface coverage and visible-impact requirements, can render applications in a guarded Playwright/Chromium browser, stores screenshots as binary artifacts, and can pass selected screenshots to the configured OpenAI-compatible model for multimodal inspection.

Broad `improve`, `redesign`, `polish`, `audit`, `modernize`, `UI/UX`, and refactor requests receive an automatic preflight:

```text
user improvement request
        ↓
deterministic Application Graph
(routes → templates, shared Jinja shells, CSS, JS, tests)
        ↓
HIGH-reasoning Improvement Plan
(surface coverage + coherent batches + visible-impact goals)
        ↓
source edits / tests
        ↓
render existing app in Chromium
        ↓
DOM + overflow + console + network evidence
        ↓
screenshot artifacts → optional vision inspection
        ↓
before/after visual comparison
        ↓
completion gate: is the improvement actually material?
```

New coding/inspection tools include:

- `application_map`, `improvement_plan`, `improvement_plan_current`;
- `browser_status`, `browser_open`, `browser_set_viewport`, `browser_dom`, `browser_screenshot`;
- `browser_click`, `browser_fill`, `browser_wait_for`, `browser_diagnostics`;
- `browser_audit_pages` for batched desktop/mobile/tablet route inspection;
- `vision_inspect` and `vision_compare` for screenshot-based visual review.

The browser is guarded: loopback applications are allowed by default; arbitrary public browsing is disabled unless explicitly configured. No generic page-JavaScript execution tool is exposed. Screenshots live in `.term5/artifacts/` and only compact metadata enters normal model context.

Vision is fail-loud rather than assumed. `vision.enabled=true` registers the multimodal path, but the configured DeepSeek/OpenAI-compatible model must actually accept image inputs. If it does not, browser DOM/console/network verification remains available and term_5 reports vision as unavailable rather than fabricating visual observations.

For broad improvement work, v5.4 adds a final completion-review round. A model response that tries to stop is shown the number of discovered surfaces, files changed, tools used and whether rendered evidence was gathered. If a UI/UX request lacks rendered verification while browser capability is configured, term_5 explicitly directs the model to inspect the result before finalizing.

See `docs/EXISTING_APP_INTELLIGENCE.md` and `docs/BROWSER_VISION.md`.

## v5.3 alpha3 headline: STABLE WEB WORKSPACE

The alpha3 shell intentionally simplifies the control plane: one navigation rail, one work area and one optional Activity drawer. It keeps recent chat restoration, Apps, Deployments, Product, Memory and live execution observability while removing duplicate navigation, permanent inspector width and overlapping polling loops. Frontend state and network activity are deliberately bounded.

See `docs/WEB_UI.md`.

## v5.3 headline: EFFICIENT WORKING MEMORY

v5.3 changes the long-running context model. The provider prompt is now bounded working memory rather than an ever-growing execution archive. Completed work is distilled into compact episodes; verbose tool results are archived locally; completed hidden reasoning and tool protocol traces are evicted; source truth is re-read from files/Git; and only relevant episodic/semantic context is recalled for later requests.

Default working-memory profile:

```text
active target          120k tokens
compact at             180k
defensive input cap    360k
safety reserve          32k
recent full turns         4

completion cap
NONE                     16k
LOW                      24k
HIGH                     64k
MAX                      96k
```

The old `model.max_output_tokens = 384000` remains a provider ceiling for compatibility, but ordinary requests no longer reserve 384k. v5.3 dynamically chooses the smaller of the reasoning-mode cap, configured ceiling, and actual remaining model-window headroom.

Large tool results are stored under `.term5/artifacts/` and represented in active model context by bounded previews. `artifact_read` retrieves the full output only if needed. Completed task summaries live in `.term5/episodes.jsonl` and are recalled by relevance.

Pre-v5.3 huge sessions are automatically migrated on load: raw tool/reasoning history is distilled into episodes and only a small recent conversational window remains active. No side effects are replayed.

Use `/context` or the Live Execution UI to see active token estimates, output budget, compactions, episodes, and archived artifacts. See `docs/WORKING_MEMORY.md`.

## v5.2 alpha2 headline: LIVE EXECUTION OBSERVABILITY + TOOL-PROTOCOL RECOVERY

The loopback web UI now exposes a live, bounded execution timeline while a turn is running. It shows high-level phase/state (routing, product planning, executive planning, model reasoning, tool execution, verification/finalization), reasoning effort, model/tool iteration, elapsed time, active tools with redacted arguments, tool result previews, success/failure counts, retries/protocol repairs, and a warning when the same tool signature is requested repeatedly. Hidden chain-of-thought is never exposed.

The UI polls `/api/activity` independently of the long-running `/api/turn` request, so Docker builds, Nginx/Certbot/Git operations, health checks and model/tool loops remain visible while the chat request is still in progress.

Alpha2 also fixes a provider-history failure that could poison later turns when an assistant `tool_calls` message was persisted without every required `tool` response. Incomplete exchanges are now repaired with explicit synthetic **not executed / interrupted** tool results, context compaction preserves complete tool groups atomically, and the tool-iteration-limit path records skipped results instead of leaving dangling calls. Already-poisoned saved sessions are repaired on load/pre-turn without replaying side effects.

## v5.2 headline: OPERATIONS

The 5.1 line made term_5 substantially better at deciding what product to build and at creating/running local Docker applications. v5.2 adds the missing production-operations layer:

```text
product request
   ↓
procedural skills + product blueprint
   ↓
implementation / tests / product audit
   ↓
Git status + staged diff + commit
   ↓
Docker build/start/health
   ↓
optional database backup + known migration preset
   ↓
DNS preflight
   ↓
Nginx candidate install → nginx -t → start/reload
   ↓
optional Certbot / Let's Encrypt
   ↓
public HTTP/HTTPS probe
   ↓
record healthy release commit
   ↓
previous release remains available for rollback
```

The intended server workflow is therefore:

```text
SSH tunnel to the loopback web UI
        ↓
prompt term_5 to build/change/fix an application
        ↓
term_5 edits code and verifies it
        ↓
term_5 commits a coherent Git release
        ↓
term_5 builds/starts Docker Compose
        ↓
term_5 configures Nginx and TLS when permitted
        ↓
term_5 verifies the public endpoint
        ↓
term_5 records the release
```

## Operations tools

v5.2 registers typed tools for:

```text
ops_status

git_head
git_diff_staged
git_init
git_stage
git_commit
git_branch_create
git_switch
git_remotes
git_fetch
git_pull_ff
git_push
git_tag
git_restore
git_revert

nginx_status
nginx_service_status
nginx_service_control
nginx_site_plan
nginx_site_install
nginx_site_remove
nginx_logs

tls_status
tls_issue
tls_renew_dry_run

deployment_register
deployment_list
deployment_status
deployment_dns
deployment_backup
deployment_deploy
deployment_rollback
```

Existing read-only `git_status`, `git_diff` and `git_log` tools remain available.

Every host command uses explicit argv with `shell=False`. DeepSeek never receives a generic host-shell tool.

## Security gates

Read-only operational inspection is available when `[operations]` is enabled. Host-changing actions are separately gated:

```toml
[security]
allow_git_write = false
allow_git_remote = false
allow_nginx_write = false
allow_tls_issue = false
```

For a server where you intentionally want term_5 to own the release path, enable only the capabilities you need:

```toml
[security]
allow_git_write = true
allow_git_remote = false
allow_nginx_write = true
allow_tls_issue = true
```

Remote Git access is deliberately independent from local Git commits/branches. You can therefore allow local source history while leaving `fetch`, `pull`, and `push` disabled.

## Deployment policy

Default v5.2 deployment policy:

```toml
[operations]
enabled = true
command_timeout_s = 120
health_timeout_s = 180
require_git = true
require_clean_git = true
require_dns = true
auto_rollback = false
nginx_sites_available = "/etc/nginx/sites-available"
nginx_sites_enabled = "/etc/nginx/sites-enabled"
nginx_max_body_mb = 64
```

A production deployment therefore refuses to proceed when:

- the workspace is not a Git repository;
- the Git working tree is dirty;
- the requested public hostname does not resolve, when DNS preflight is enabled;
- the application fails its Docker/container/HTTP health gate;
- a configured database backup fails;
- a known migration preset fails;
- Nginx candidate configuration fails `nginx -t`;
- Nginx cannot start/reload;
- requested certificate issuance fails;
- a final live HTTPS probe fails.

A release is written to `.term5/deployments.json` only after the configured gates pass.

## Git release workflow

For ongoing application maintenance the recommended flow is:

```text
git_status
   ↓
inspect/edit/test
   ↓
git_diff
   ↓
git_stage
   ↓
git_diff_staged
   ↓
git_commit
   ↓
deployment_deploy
```

Git source history, term_5 file transactions, and deployment history are separate recovery layers:

```text
local transaction undo/redo   → editing mistakes
Git revert/reset              → source release history
deployment rollback           → restore previous known-good running release
```

`deployment_rollback` requires Git-write capability and a clean working tree. It uses a hard reset to the previous recorded release, rebuilds/restarts the registered Docker app and requires health to recover. If recovery itself fails, term_5 makes a best-effort attempt to restore the commit it started from.

Automatic rollback is disabled by default because it is destructive to Git history state:

```toml
[operations]
auto_rollback = false
```

It can be enabled explicitly after qualification on your server.

## Nginx

v5.2 generates a conventional reverse proxy to a loopback-published Docker port. The generated site includes:

- `Host` forwarding;
- client address forwarding;
- `X-Forwarded-Proto`;
- WebSocket upgrade headers;
- configurable upload/body size;
- bounded proxy timeouts.

Only public DNS hostnames and loopback upstreams are accepted in alpha1.

Nginx installation is guarded:

```text
write candidate
   ↓
enable site
   ↓
nginx -t
   ├─ fail → restore previous site/link
   └─ pass
        ↓
reload running Nginx
        or
start Nginx if it is not running
```

`nginx_site_remove` refuses to remove a site that is not marked as term_5-managed.

The default paths target the common Debian/Ubuntu layout, but both site directories are configurable.

## Let's Encrypt / Certbot

`tls_issue` uses the Certbot Nginx plugin with typed arguments:

```text
certbot --nginx -d <domain> --non-interactive --agree-tos --email <email> --redirect
```

Staging issuance is available. `tls_renew_dry_run` can validate renewal behavior.

term_5 does not install Certbot itself in this alpha. The host must already have the required system packages.

## Database backup + migrations

A deployment can record one known migration policy:

```text
none
flask-db   → flask db upgrade
django     → python manage.py migrate --noinput
```

It can also record one backup policy:

```text
none
mongodb    → mongodump --archive --gzip
postgres   → pg_dumpall -U postgres
```

Backups are written under:

```text
.term5/backups/<deployment>/
```

A configured backup runs before the migration. Database restore is intentionally **not automated in alpha1**; code rollback and database rollback are different operations and should not be conflated.

## Procedural operations skills

v5.2 adds bundled local skill packs for:

- deployment operations;
- Git releases;
- Nginx production proxying;
- Let's Encrypt;
- database backups.

This means prompts such as:

```text
Deploy this Flask app publicly with Nginx and Let's Encrypt.
```

resolve operational context automatically before the executive plan runs.

All 5.1 product/framework/capability/UX/security/testing skills are retained. User and workspace overrides still work through:

```text
~/.term5/skills/**/manifest.json
<workspace>/.term5/skills/**/manifest.json
```

## Docker application runtime

The 5.1 Local App Runtime is unchanged in principle: term_5 runs on the host and manages the Docker Engine already running on that host.

It can create/register/build/start/stop/restart/log/inspect/open Docker Compose applications without Docker-in-Docker and without exposing the Docker socket to generated application containers.

## Web UI over SSH

The web UI remains loopback-only and token-gated:

```bash
term5 --web
```

On a remote server, tunnel it rather than exposing it publicly. For example, if term_5 prints port `8765` on the server:

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

Then open the tokenized loopback URL locally.

v5.2 adds a Deployments panel alongside Local Apps and Product Blueprint.

## Console commands

```text
/status
/tools
/doctor
/reason auto|none|low|high|max
/skills
/product
/product-audit

/docker
/apps
/app-status NAME
/app-start NAME [build]
/app-stop NAME
/app-restart NAME
/app-logs NAME [SERVICE]
/app-open NAME

/ops
/deployments
/deploy-register NAME APP DOMAIN PORT [tls]
/deploy NAME [EMAIL]
/rollback NAME [COMMIT]
/nginx
/tls [DOMAIN]
/git-head
```

Natural-language prompting can use the same underlying tools.
