# term_6 v6.2 — Agent Mesh and Integration Contracts

v6.2 retains the v6.1 agent-mesh and contract layers as the communication substrate underneath the spatial organization workspace.

## Typed Project Owner mesh

Project Owners communicate through durable messages in `.term5/orchestrator.sqlite3`. Supported message types are `STATE_QUERY`, `ANALYSIS_QUERY`, `CHANGE_REQUEST`, `DEPENDENCY_REQUEST`, `CONTRACT_PUBLISHED`, `CONTRACT_CHANGED`, `CONTRACT_FORECAST`, `TASK_BLOCKED`, `TASK_COMPLETED`, and `RISK_NOTICE`.

`STATE_QUERY` is answered from the target owner's latest Project State Capsule and does not take the target mutation lane. `ANALYSIS_QUERY` uses a bounded read-only model lane. `CHANGE_REQUEST` creates a durable task in the target owner's queue. Dependency edges are durable and can span projects; when prerequisites complete, waiting tasks are released and their owners are woken immediately.

Project Owners can read other owners' published state and send requests, but they cannot mutate another project's files. Sender identity is bound by the runtime and cannot be spoofed by an owner.

## Integration contracts

Cross-project interfaces are first-class records with a stable contract key, owner, revision, interface payload, compatibility classification, source task, and explicit consumers. Published contract state is authoritative.

Owners can separately publish contract forecasts in `in_flight`, `planned`, or `projected` state. Forecasts are deliberately non-authoritative. When a new authoritative contract revision is published, active forecasts for that contract are resolved.

Consumers subscribe to a contract. Every published/revised contract and every forecast is delivered to subscribed consumer inboxes. `contract_impact` exposes the consumers and their active queue state before an owner publishes a potentially breaking revision.

Project State Capsules include owned contracts, consumed contracts, and active forecasts so owner-to-owner answers can distinguish what exists now from what is expected next.
