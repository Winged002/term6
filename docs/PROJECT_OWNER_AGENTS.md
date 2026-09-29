# term_6 v6.2 — Project Owner Agent Architecture

v6.2 keeps the coordinator + Project Owner topology and changes the human-facing interaction model into a spatial organization workspace with durable objectives.

## Roles

### Central AI / coordinator

The normal chat remains the human-facing entry point. Its job is to understand objectives, split cross-project work, delegate implementation to the correct Project Owner Agent, express task dependencies, query project state, and synthesize outcomes.

The coordinator receives a compact owner directory in turn context and has typed tools:

- `agent_list`
- `agent_status`
- `agent_delegate`
- `agent_query`
- `agent_task_list`
- `agent_wait`
- `agent_task_cancel`
- `agent_task_retry`
- `agent_message_send`
- `agent_message_list`
- `agent_capsule_refresh`

Central turns remain serialized to preserve chat coherence, but additional web-chat commands can be submitted while a turn is running; they wait on the central turn lock instead of being rejected.

### Project Owner Agent

Every registered project receives one persistent logical owner. The owner is persistent because its queue, capsule, messages and episodic memory are durable; it does not keep an LLM process permanently alive.

Each owner has:

- a durable SQLite-backed task queue;
- one mutation lane for that project;
- isolated episodic memory under `.term5/agents/<project>/episodes.jsonl`;
- an owner artifact store under `.term5/agents/<project>/artifacts/`;
- a Project State Capsule mirrored to `.term5/agents/<project>/capsule.json`;
- a typed inter-agent inbox/outbox;
- a bounded owner context rebuilt for every task.

Different project owners can work concurrently up to `[agents].max_concurrent_owners`. Work inside one project is serialized.

## Durable orchestration store

`.term5/orchestrator.sqlite3` uses SQLite WAL mode and contains small coordination records only:

- `agents`
- `tasks`
- `messages`
- `capsules`

Task lifecycle states are:

`created -> planned -> queued -> claimed -> executing -> verifying -> completed`

with additional states for `waiting_dependency`, `waiting_agent`, `waiting_human`, `failed`, and `cancelled`.

Workers use leases. If the process dies while a task is claimed/executing/verifying, an expired lease is recovered back to `queued` on the scheduler loop.

## Project State Capsule

A capsule separates present truth from forecast state.

`current_truth` contains project identity, current Git state, durable project goals/decisions/backlog and integration metadata.

`in_flight` contains claimed/executing/verifying work.

`planned` contains queued and dependency-blocked work.

`projected_changes` describes what queued work is expected to change. It is explicitly marked non-authoritative until completed and verified.

This lets another agent ask, for example, “What SSO contract exists now and what is about to change?” without interrupting the SSO owner’s active mutation task.

## Inter-agent queries

`agent_query(..., deep=false)` rebuilds the target capsule deterministically and returns current + in-flight + planned state without an LLM call.

`agent_query(..., deep=true)` uses the target owner’s capsule plus its relevant episodic memory in a separate read-only knowledge lane. It does not acquire the owner mutation lock, so it can answer while the owner is coding.

Inter-agent messages are typed and auditable. Supported kinds include `STATE_QUERY`, `ANALYSIS_QUERY`, `CHANGE_REQUEST`, `DEPENDENCY_REQUEST`, `CONTRACT_PUBLISHED`, `CONTRACT_CHANGED`, `TASK_BLOCKED`, `TASK_COMPLETED`, and `RISK_NOTICE`.

## Context policy

Owner context does not inherit central chat history. A task begins with:

1. owner charter;
2. freshly built Project State Capsule;
3. relevant completed owner episodes;
4. recent inter-agent inbox entries;
5. assigned task + acceptance criteria.

Verbose tool outputs are archived through the owner artifact store. If the active owner trace grows beyond `[agents].owner_context_target_tokens`, older complete assistant/tool protocol units are removed from hot context while the capsule, episodes and artifacts remain durable.

## Dependency example

A coordinator can delegate an upstream SSO task, then enqueue a Product A task with `depends_on=[<sso-task-id>]`. Product A remains `waiting_dependency` until the SSO task is completed. Independent Product B work can run at the same time.

## Safety boundaries

Project Owners cannot use project create/clone/import/switch/unregister tools and cannot target a different project through tools that expose an explicit `project` argument. Direct workspace path tools are checked against the owner project path for the most mutation-sensitive operations.

The source tree and runtime remain the final truth. Capsules are cached knowledge; projected changes are forecasts.


## Beta mesh and contracts

The beta adds typed owner-to-owner requests and first-class integration contracts. See `AGENT_MESH_CONTRACTS.md` for the message semantics, dependency wakeups, contract revisions, forecasts, impact analysis, and consumer notification model.
