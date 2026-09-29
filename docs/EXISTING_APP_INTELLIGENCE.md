# Existing-Application Coding Intelligence — v5.4

v5.4 treats "improve this existing application" as a different problem from "create a new application".

## Why

A source-only agent can produce correct changes that are too local, visually negligible, or incomplete across the application's pages. Tests and Docker health prove correctness/availability; they do not prove that a redesign is materially better.

## Pipeline

```text
request
  ↓
Application Graph (model-free)
  ↓
Improvement Plan (HIGH reasoning)
  ↓
coherent edit batches
  ↓
syntax/tests
  ↓
rendered browser audit
  ↓
vision/DOM/console evidence
  ↓
completion review
```

### Application Graph

`application_map` scans source without a model call. It records route surfaces, route→template mappings, Jinja relationships, likely shared shells, CSS and JS selector evidence, forms/controls and tests. It complements the existing import graph instead of replacing it.

### Improvement Plan

The automatic plan contains:

- surfaces that must be inspected or covered;
- shared-system changes;
- page-specific changes;
- code-quality/refactor changes;
- expected visible impact;
- implementation batches;
- verification requirements;
- explicit stop conditions.

The planner is intentionally skeptical of tiny diffs for broad requests. A one-file edit can still be valid when it changes a truly shared shell/style system and rendered evidence proves app-wide impact.

### Completion review

When the primary model first tries to finish a broad improvement task, the runtime gives it one additional evidence review containing surface count, changed-file count, tools used, and whether rendered evidence exists. UI/UX requests with browser capability but no rendered evidence are instructed to inspect the application before stopping.

This review is not used for normal narrow coding requests, so ordinary tasks do not pay an unnecessary extra model round.
