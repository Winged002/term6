# term_5 v5.2 Operations Runtime

The Operations Runtime exists to let term_5 maintain real server applications without turning the model into a general root shell.

## Trust boundaries

The model requests typed tools. The local runtime owns argv construction, validation, capability checks, filesystem locations and release records.

```text
DeepSeek
  ↓ typed tool request
ToolRegistry + SecurityPolicy
  ↓
OperationsRuntime
  ├─ GitManager
  ├─ NginxManager
  ├─ TLSManager
  └─ DeploymentRegistry
        ↓
explicit argv, shell=False
```

There is no `shell(command)` tool.

## Release transaction

`deployment_deploy` performs a bounded sequence:

1. Resolve deployment record.
2. Verify Git repository/clean tree according to policy.
3. Record current HEAD.
4. Resolve public DNS when required.
5. Validate/build/start the registered Docker app and require health.
6. Create configured Mongo/Postgres backup.
7. Run only the configured known migration preset.
8. Install validated Nginx reverse proxy when requested.
9. Probe HTTP.
10. Issue Certbot TLS when requested/permitted.
11. Probe HTTPS.
12. Record the release as healthy.

A failed deployment is recorded as failed. Optional auto-rollback targets the previous recorded commit and is disabled by default.

## Rollback semantics

`deployment_rollback` is a source/runtime rollback, not a database rollback.

It requires:

- `security.allow_git_write=true`;
- a clean Git working tree;
- an explicit or previously recorded target commit.

It performs a hard reset, rebuild/start and health check. Persistent Docker volumes are not deleted.

Database backup artifacts are retained separately under `.term5/backups/` for operator-controlled recovery.

## Nginx ownership

Generated sites contain:

```text
# managed-by: term_5 v5.2
```

Removal refuses unmarked sites. Candidate installation snapshots the previous managed file/link, runs `nginx -t`, and restores the prior state on validation/reload failure.

Alpha1 only emits loopback upstreams.

## TLS

Live issuance requires a working public DNS/HTTP path and a contact email. The runtime calls the Certbot Nginx plugin directly with explicit arguments. It does not install Certbot.

## Git

Local writes and remote network operations are different capabilities:

```text
git.write  → init/stage/commit/branch/switch/tag/restore/revert/rollback
git.remote → fetch/pull/push
```

Fast-forward-only pull avoids model-created merge commits during unattended server maintenance.

## Recommended server qualification

Before trusting production automation:

1. Run `term5 --selftest`.
2. Confirm `/ops` detects Git, Docker, Nginx and Certbot as expected.
3. Keep automatic rollback disabled.
4. Deploy a disposable hostname first.
5. Use Certbot staging first.
6. Verify Nginx rollback by deliberately testing an invalid candidate in a non-production environment.
7. Verify database backup artifacts for the actual image/service credentials you use.
8. Only then enable the desired write capability flags for production.
