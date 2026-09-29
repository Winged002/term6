# Migration — v6.2 / v6.2-ui → v6.2-ui2

v6.2-ui2 is an in-place UI/runtime behavior upgrade. It does not recreate the orchestration database. It adds a persisted `workspace_layout_v2` entry inside the existing `orchestrator_settings` table. Existing `.term5/orchestrator.sqlite3`, objectives, queues, contracts, State Capsules, human tasks and project state remain valid.

## Configuration change

Remove this line from `[agents]` when convenient:

```toml
owner_max_iterations = 24
```

It is no longer used. Old configs containing it remain compatible; the field is accepted but ignored. `TERM61_OWNER_MAX_ITERATIONS` is also no longer read.

`central_max_iterations` is unrelated and remains active for the central coordinator.

## UI

No reverse-proxy changes are needed. The same loopback web service serves the icon-first shell. A hard browser refresh after deployment is recommended because `/assets/app.css` and `/assets/app.js` change substantially.

Project positions can now be dragged in the Systems lens. Once a project is moved, the current layout is persisted under `workspace_layout_v2`; Reset Layout clears it and returns to automatic collision-aware packing.

Inter-agent message responses update live in Workspace/Inbox using the existing activity loop, so no browser refresh should be required.

---

# Migration — term_6 v6.1 → v6.2

v6.2 is an in-place upgrade. Existing project registrations, `.term5` state, owner queues, messages, contracts, forecasts, capsules, human tasks, durable runs, configuration/secrets, deployments and session history remain valid.

## SQLite schema upgrade

v6.2 adds:

- `objectives` — durable organization-level user objectives;
- `tasks.objective_id` — durable link from Project Owner work to the originating objective.

The store checks `PRAGMA table_info(tasks)` at initialization and adds `objective_id` only when missing. The existing database is not recreated.

Existing v6.1 tables remain unchanged, including `agents`, `tasks`, `messages`, `capsules`, `contracts`, `contract_consumers`, `contract_forecasts` and `orchestrator_settings`.

## New default agent configuration

Existing v6.1 configuration remains valid. When these values are absent, v6.2 defaults to:

```toml
[agents]
coordinator_only = true
central_max_iterations = 6
```

Environment overrides:

```text
TERM62_COORDINATOR_ONLY=true
TERM62_CENTRAL_MAX_ITERATIONS=6
```

`coordinator_only=true` only fences the central model when at least one registered Project Owner exists. Owners retain their scoped engineering tools. With zero registered owners, the central runtime can still bootstrap/import/register projects.

## UI heartbeat change

The workbench now uses `/api/status-lite` for frequent status rendering. `/api/status` remains available for full runtime status. No reverse-proxy change is required.

Transient heartbeat timeout/abort failures no longer clear the visible state or produce an abort toast; the last successful snapshot remains displayed.

## Workspace UX

The former **Owners** route becomes **Workspace** and adds a separate **Agent Inbox**. Project Owner queues, messages, contracts and audit data are still sourced from the same orchestration database/event journal.

The bottom command composer is now persistent across routes. Commands issued while projects are multi-selected include that selection as explicit routing context for the central coordinator.

## Rollback

Rollback is still a release symlink switch plus service restart. v6.1 ignores the new `objectives` table and unknown `tasks.objective_id` column. Tasks and owner state remain intact. If rolling back, do not delete `.term5/orchestrator.sqlite3`.

As before, do not run two independent term_6 services against the same orchestration database.
