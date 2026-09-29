# term_5 5.7.0 Qualification Gate

## Result

`5.7.0` passes the local consolidation and project-aware engineering gate.

### Automated regression

```text
pytest                         125 / 125 PASS
doctor/selftest                 51 / 51 non-failing
critical failures                0
registered typed tools         134
procedural skills               24
Python compileall              PASS
frontend JavaScript syntax     PASS
```

### Project/repository qualification

```text
Project Registry roundtrip                 PASS
active project persistence                 PASS
Project Brain goals/decisions/backlog      PASS
independent Git repositories               PASS
workspace root need not be a repository    PASS
existing app adopted in place              PASS
active-project context injection           PASS
project-aware Git default selection        PASS
typed git clone argv                       PASS
typed remote add/set-url                   PASS
upstream ahead/behind parsing              PASS
first push --set-upstream                  PASS
project-scoped deployment Git manager      PASS
project-scoped rollback selection          PASS
GitHub SSH/HTTPS slug inference            PASS
typed gh status/list/create/view path       PASS
provider write separate capability gate    PASS
GitHub create requires all Git gates       PASS
Project workbench/API                      PASS
single adaptive UI poller retained         PASS
project-repository procedural skill        PASS
```

### Retained subsystem regression

The 5.2–5.6 suites remain part of the same 125-test run, including Working Memory, transaction recovery/undo, procedural skills/product planning, Docker app lifecycle interfaces, Git/Nginx/TLS/deployment operations, server management, browser/vision interfaces, existing-application improvement, unlimited execution/loop guard, daily history, inline screenshots, artifact archive, Tool Logs and Runs.

## Environment-limited checks

The build environment does not contain authenticated GitHub CLI, a DeepSeek API key, or Docker. Doctor therefore reports those live integrations as UNKNOWN rather than PASS:

```text
provider-client       UNKNOWN — DEEPSEEK_API_KEY absent
github-provider       UNKNOWN — gh unavailable/auth not present in build environment
local-app-runtime     UNKNOWN — docker CLI absent
vision-path           UNKNOWN live model capability — API key absent
```

The typed GitHub adapter is covered with explicit argv/no-shell tests. Real Git initialization and independent repository discovery are exercised with the local Git CLI. A live GitHub repository create/push, DeepSeek call, Docker build, Nginx mutation and Certbot issuance must be requalified on the target server.

## Target-server acceptance

On Syntal-TWO, qualify in this order:

1. `ssh -T git@github.com` succeeds using the host key.
2. Install/authenticate `gh`; `gh auth status` succeeds.
3. Enable only the intended Git gates in `term5.toml`.
4. Start 5.7 and confirm existing applications are adopted in place.
5. Ask for `project_list`/Git status with no mutation.
6. Initialize/publish one non-critical project and verify origin/upstream sync.
7. Create or clone one test repository through term_5.
8. Verify a project-scoped commit/deploy does not depend on another project's dirty state.
9. Qualify browser/vision and production readiness using the existing 5.6/5.5 gates.
