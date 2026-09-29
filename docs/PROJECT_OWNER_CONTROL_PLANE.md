# Project Owner Control Plane — v6.2

## Purpose

The v6.2 spatial workspace builds on the Project Owner control plane to make the multi-agent runtime observable and directly operable without exposing hidden reasoning. It shows durable state: owners, queues, dependencies, messages, contracts, State Capsules, human tasks and redacted execution events.

## Visual project systems

Each registered project appears as a system card:

- **Project core** — the registered project/product.
- **OWNER inner node** — the persistent Project Owner identity.
- **Q inner node** — the read-only knowledge/query lane.
- **W outer node** — the currently leased mutation worker, when one exists.
- **Outer task nodes** — queued or blocked work, labelled by priority.

The visualization never invents worker processes. Active worker identity comes from the durable task lease; queue nodes come directly from the orchestration database.

## Project drill-down

Selecting a project exposes five views:

1. **Queue** — status, priority, source, worker lease, dependencies, changed files, result/error and controls.
2. **Timeline** — persistent `agent.*` / `agents.*` records from the redacted event journal.
3. **Messages** — typed owner-to-owner traffic and responses.
4. **State Capsule** — current truth, in-flight work, planned work, projected changes and contract state.
5. **Contracts** — authoritative owned/consumed revisions plus explicitly non-authoritative forecasts.

## Task graph

The dashboard renders a cross-project DAG from `tasks.depends_on_json`. Edges mean "target waits for source". Open work is kept visible; recent terminal nodes provide completion context.

## Queue management

- Priority may be changed live between P0 (highest) and P3.
- Cancellation is cooperative; no concurrent replacement writer is spawned.
- Failed/cancelled tasks can be reset to queued and retried.
- Dependency-blocked tasks become queued automatically when all prerequisites complete.

## Concurrency

The dashboard exposes two independent runtime capacities:

- `max_concurrent_owners` — number of different project mutation lanes that may execute simultaneously.
- `max_concurrent_queries` — number of deep read-only Project Owner queries that may run simultaneously.

The values are applied through a runtime-adjustable async capacity limiter and persisted in `orchestrator_settings`.

## Recovery

At service start, previous-process `claimed`, `executing`, and `verifying` tasks are recovered immediately. During a live process, ordinary worker heartbeats and stale-lease recovery remain active. The UI exposes total recovered tasks and the last recovery time.

## Central-chat feedback

The browser's existing single activity poller also listens for significant agent events. Starts, completions, failures, cancellations, dependency releases, contract changes, restart recovery and concurrency changes are rendered as lightweight live team notices in the main chat. They are operational feedback, not synthetic stored assistant turns.

## Security boundaries

- Source mutation remains bound to the owning project.
- Cross-project communication uses typed messages/contracts rather than arbitrary filesystem access.
- The UI remains loopback-only and token-gated.
- Event data is redacted and bounded before browser exposure.
- Secret values remain outside model/UI telemetry paths.
