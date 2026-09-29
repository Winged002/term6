# term_6 v6.2 — Spatial Organization Workspace

## Mental model

v6.2 treats term_6 as a software organization rather than a chatbot with a dashboard. The central AI is the coordinator. Registered projects are organizational units. Each Project Owner owns a serialized mutation lane, durable queue, State Capsule and read-only knowledge lane. Ephemeral workers execute owner tasks.

## Systems lens

The central coordinator occupies the center. Projects are arranged around it. Each project exposes attention, owner state, active worker and a bounded sample of open tasks. Durable inter-agent messages render as mesh connections.

Low semantic zoom suppresses detailed queue nodes. Higher zoom exposes worker/task details. Zoom changes presentation only; it does not change execution state.

## Objectives lens

Every central turn receives a durable `obj_<id>`. The lens positions objectives independently from repository boundaries and connects each objective to the projects executing its child tasks. Completion percentage is derived from durable child task state.

This makes a request such as “update every consumer to SSO r10” visible as one objective with SSO/NJS/Files/Planner branches rather than four unrelated queues.

## Dependencies lens

The dependency lens renders `tasks.depends_on_json` as a DAG. A target task waits for the source prerequisite. Dependency-blocked tasks are visually distinct.

Direct manipulation supports dragging a task onto another task. The drop target becomes dependent on the dragged prerequisite. The persisted graph then drives normal automatic dependency release/wakeup.

## Direct project reassignment

Queued/waiting/failed/cancelled work can be dragged onto a different project. Completed or actively mutating tasks cannot be reassigned. Moving a task preserves its objective ID.

Project filesystem isolation is unchanged: ownership reassignment determines which scoped Project Owner may later execute the task.

## Persistent inspector

The inspector renders operational state for the selected entity:

- Central coordinator: role, working/queued/blocked counts, recovery and live concurrency controls.
- Project: attention state, owner/capsule state, active worker and queue.
- Objective: original intent and child project tasks.
- Task/worker: task identity, status, dependencies, worker lease, iteration, current tool and bounded context estimate.

Private chain-of-thought is never exposed.

## Attention model

`autonomous` means the project can proceed without external input. `coordinated` means work is waiting on another durable owner/dependency. `human` means an open My Task is associated with the project.

The Agent Inbox uses this model to surface exceptions rather than routine state chatter.

## Persistent command bar

The central command bar is outside individual page views and remains available everywhere. Shift/Ctrl/Cmd project selection adds explicit routing context to the next objective. The central model still decides decomposition and dependencies; selection does not bypass owner boundaries.

## Central coordinator fence

When owners exist, the central model receives only organization/project registration, agent/contract coordination, human task, configuration/readiness and high-level state tools. It cannot directly browse project pages, read/write source, run project tests or perform Git/deployment implementation.

Detailed product/improvement/executive preflights are also skipped centrally. The central turn hands off and ends; owner progress returns through the persistent activity/workspace stream.

## Status heartbeat

The UI heartbeat uses `/api/status-lite`. It deliberately avoids Docker/operations/configuration probes, which were a source of slow responses and browser aborts in v6.1. Transient heartbeat failures are silent and preserve the last good state.
