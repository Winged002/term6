# term_6 v6.1 Release Gate

## Scope

Final v6.1 combines alpha1/2/3, beta1/2 and the RC Project Owner control plane.

## Required capabilities

- [x] Persistent Project Owner per registered project.
- [x] Durable SQLite/WAL queues and dependencies.
- [x] One mutation lane per project; concurrent work across projects.
- [x] Isolated owner memory/context and Project State Capsules.
- [x] Typed inter-agent state/analysis/change/dependency messaging.
- [x] Versioned contracts, consumers, forecasts and impact analysis.
- [x] Visual Project Owner dashboard with project/owner/worker/task orbit systems.
- [x] Per-project queue, timeline, message, capsule and contract drill-down.
- [x] Cross-project task dependency graph.
- [x] Live queue priority, cancellation and retry controls.
- [x] Runtime-adjustable/persisted owner and query concurrency limits.
- [x] Immediate restart recovery for previous-process in-flight work.
- [x] Human-task summary and direct My Tasks routing.
- [x] Persistent redacted audit timeline.
- [x] Main-chat Project Owner feedback.
- [x] Strict project mutation isolation preserved.

## Validation

Source gate: 177/177 tests PASS.

Installed-wheel doctor: 58/58 non-failing, 0 critical failures, 176 typed tools.

Python compile and browser JavaScript syntax checks PASS. Final artifact hashes are recorded in `VALIDATION.txt` and `BUILD_INFO.json`; the ZIP hash is reported alongside the downloadable artifact because embedding its own hash would be self-referential.
