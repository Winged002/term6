# term_6 v6.1-alpha Qualification Gate

## Scope

This release combines the planned v6.1-alpha1, alpha2 and alpha3 work into one deployable preview:

1. durable Project Owner Agent runtime;
2. central coordinator/delegation layer;
3. per-project Project State Capsules and context isolation.

## Source qualification

- package version: `6.1.0a1`
- release label: `v6.1-alpha`
- pytest: `164/164` PASS
- doctor/selftest: `56/56` non-failing
- critical failures: `0`
- typed tools: `168`
- procedural skills: `28`
- Python compileall: PASS
- workbench JavaScript syntax (`node --check`): PASS

## Agent-runtime gates

- SQLite orchestration database initializes in WAL mode: PASS
- one persistent owner record per registered project: PASS
- durable owner task enqueue/claim/execute/complete path: PASS
- dependency-blocked tasks release after prerequisites complete: PASS
- worker lease heartbeat and stale-lease recovery implementation: PASS
- one mutation lane per project: PASS
- different project owners execute concurrently: PASS
- project-aware tools are bound to the owner project: PASS
- cross-project direct file/multi-file/FIM mutation is rejected: PASS
- owner cancellation is observed before subsequent model/tool phases: PASS

## Coordinator gates

- `agent_list`: PASS
- `agent_status`: PASS
- `agent_delegate`: PASS
- `agent_query`: PASS
- `agent_task_list`: PASS
- `agent_task_cancel`: PASS
- `agent_task_retry`: PASS
- `agent_wait`: PASS
- `agent_message_send`: PASS
- `agent_message_list`: PASS
- `agent_capsule_refresh`: PASS
- central context receives compact owner directory rather than owner execution history: PASS
- later central chat turns queue instead of failing reentrant-turn guard: PASS

## Project State Capsule / context gates

- capsule mirrors to `.term5/agents/<project>/capsule.json`: PASS
- `current_truth` is separated from `in_flight`, `planned`, and `projected_changes`: PASS
- deterministic capsule queries refresh project/Git/queue/message state: PASS
- owner task context excludes central chat history: PASS
- owner-specific episodes and artifacts use `.term5/agents/<project>/`: PASS
- owner hot context has an independent target budget: PASS
- deep queries run as read-only knowledge-lane model calls: PASS

## Compatibility

- existing v6.0 `term5.toml` remains valid; `[agents]` has defaults: PASS
- existing project/workspace state requires no destructive migration: PASS
- package name remains `term5-local`: PASS
- `term5` and `term6` CLI entry points retained: PASS
- stale v6.0 build-host path in `test_v59_production.py` corrected: PASS

## Artifact qualification

- fresh wheel build: PASS (`term5_local-6.1.0a1-py3-none-any.whl`)
- fresh wheel install/version: PASS (`6.1.0a1`)
- fresh wheel selftest: `56/56` non-failing
- fresh extracted ZIP pytest: `164/164` PASS
- fresh extracted ZIP selftest: `56/56` non-failing
- final archive excludes runtime/test caches: PASS

Live provider/host checks remain environment-dependent and are not fabricated.
