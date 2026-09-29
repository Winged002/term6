# Production Intelligence — v5.9

## Purpose

The production layer closes the engineering feedback loop after deployment without pretending the local runtime is a full observability platform. It records bounded, inspectable evidence that term_5 can correlate with a project, environment, deployment and Git commit.

## State model

```text
Project
  ├─ development environment
  ├─ staging environment
  └─ production environment
          ↓
      deployment
          ↓
        commit
          ↓
      release marker
          ↓
       snapshots
          ↓
       incidents
```

Local state:

```text
.term5/production/registry.json    environment + incident state
.term5/production/metrics.jsonl   bounded evidence snapshots
.term5/production/releases.jsonl  deployment/release markers
```

## Snapshot evidence

A snapshot can include:

1. registered application/container health;
2. external HTTP/HTTPS probe status and elapsed milliseconds;
3. Docker `stats --no-stream` evidence for Compose containers;
4. Nginx request status classes, 5xx percentage, top paths and latency percentiles when the log format contains request time;
5. bounded Nginx error-level counts;
6. server load, memory and disk state;
7. Configuration & Secrets readiness for the selected project/environment.

Large raw logs remain outside ordinary model context. `production_logs` returns only a bounded/filterable slice.

## Nginx attribution boundary

Managed v5.9 Nginx configs write domain-specific logs. A domain-specific log is considered scoped evidence. If it is unavailable, the runtime may read the global Nginx log for debugging, but global traffic is never used to create a project-specific high-5xx incident or fail a release verification. This avoids attributing another app's failures to the selected project.

## Release lifecycle

```text
deploying
   ↓
observing
   ↓ release_verify
verified  OR  degraded
```

Failed deployment phases become `failed`. DNS preflight failure is part of the same transaction and cannot leave a release stuck at `deploying`.

`release_verify` performs a bounded number of evidence samples. App/public health must remain healthy; site-scoped 5xx evidence must remain below the configured incident threshold. It records `last_verified_at` separately from ordinary `last_observed_at`.

## Incidents

Incident records contain:

```text
id
title
severity P0-P3
status open/investigating/mitigated/resolved
project
environment
deployment
commit
source
summary
evidence
actions
timestamps
```

The same concrete condition is deduplicated while unresolved. Operator/agent notes are appended as actions.

## Background monitoring

Background monitoring is off by default. When enabled, a daemon worker takes one evidence snapshot per registered deployment every configured interval. It does not execute arbitrary commands and uses the same typed app/server/Nginx adapters as manual snapshots.

## Operations UI

The Operations island exposes:

- environment/release state;
- recent snapshots;
- open incidents;
- bounded application/Nginx log search;
- manual `Collect now`;
- release/deployment status.

It complements Tool Logs: Tool Logs show what term_5 did, while Operations shows what deployed systems are doing.

## What v5.9 is not

This is not a full metrics backend, distributed tracing system, or long-term log warehouse. Prometheus/OpenTelemetry/Sentry-style integrations can be layered on later. v5.9's job is to give the autonomous engineering runtime trustworthy local evidence and a durable incident/release model now.
