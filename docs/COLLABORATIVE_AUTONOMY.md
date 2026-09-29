# term_6 Collaborative Autonomy

## Purpose

term_6 treats collaboration as durable orchestration rather than chat turn-taking. The AI should complete everything it can safely decide itself. When a human contribution has real value, it creates a compact durable task and continues any independent work.

## Human task model

Kinds:

```text
choose       subjective/product/design choice
configure    trusted environment/credential input
approve      explicit authorization for sensitive action
answer       information unavailable to the runtime
authorize    external login/OAuth/device authorization
review       inspect evidence/result
resolve      human intervention for an unresolved blocker
```

Every task records project, durable run, title/description, blocking state, priority, options or field names, timeout policy, deadline, resume instruction and timestamps.

The queue never stores credential values. Configure tasks store variable names and sensitivity flags only.

## Timeout policies

### AUTO_DECIDE

Use only for safe reversible subjective choices. If the deadline passes, the runtime chooses `default_option`, then a recommended option, then the first option. Approval tasks are explicitly forbidden from using this policy.

### DEFER

Use when the human input is useful or eventually required but unrelated work can proceed. After the deadline the task becomes non-blocking/deferred and the run can finish as `completed_with_pending`.

### BLOCK

Use for approval, destructive action, irreversible production mutation or other situations where silence cannot be interpreted as consent. There is no timeout mutation.

## Durable run states

Typical lifecycle:

```text
running
  ↓
waiting_human ──→ running ──→ completed
  │
  └──────────────→ completed_with_pending

failure → failed
```

Resolved tasks linked to a run are resumable. The continuation contains the original objective, resolved option/answer, selected artifact ID when present and a bounded resume instruction.

## My Tasks

My Tasks is global across all projects. The user can return later without searching chat history. Cards make the no-response behavior visible and provide task-specific actions such as Configure, Defer, select option, submit answer, or Let AI decide now.

## Security invariants

- approval cannot auto-resolve;
- configure tasks cannot be resolved by sending a secret through the normal task API;
- secret values enter through the trusted Configuration UI only;
- task and run stores are local mode `0600` files;
- model context gets compact task metadata, not secret values;
- term_6 should not create human tasks for trivial choices it can safely make itself.
