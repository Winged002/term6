# term_5 5.5.0 Qualification Gate

Release focus: consolidate the stable 5.3-alpha3 UI and all 5.4 existing-app/browser/vision intelligence into one production-engineering runtime, adding bounded host/server management.

## Qualified locally

- pytest: **101 / 101 PASS**
- doctor/selftest: **49 / 49 non-failing**, 0 critical failures
- registered typed tools: **116**
- procedural skills: **23**
- Python compileall: PASS
- frontend JavaScript syntax (`node --check`): PASS
- 5.3-alpha3 single activity poller / no `setInterval`: PASS
- working-memory/context-GC regressions: PASS
- Docker/Git/Nginx/TLS/deployment regressions: PASS
- application graph + improvement plan/completion-gate regressions: PASS
- browser URL guard + multimodal adapter contract: PASS
- server capacity/service/allowlist tests: PASS
- Chromium process launch: PASS (`/usr/bin/chromium`)
- rendered in-memory DOM + PNG screenshot artifact: PASS
- wheel build + fresh virtualenv install/selftest: PASS
- fresh extracted ZIP pytest/selftest: **101/101**, **49/49**

## Production/server management invariants

- no unrestricted host shell;
- no unrestricted Docker shell;
- host service writes require `security.allow_server_write=true`;
- service writes are restricted to `operations.managed_services`;
- read-only `production_readiness` aggregates host, Docker apps, Git, Nginx, Certbot and deployments;
- application container lifecycle remains separate from host service lifecycle;
- Nginx/TLS/Git/server write gates remain independent.

## UI invariants

The release uses the 5.3-alpha3 shell:

- one left navigation rail;
- one central work area;
- one optional Activity drawer;
- one adaptive activity polling loop;
- no command palette / duplicate mobile navigation / permanent third inspector column;
- Apps/Deployments/Memory load on demand;
- local CSS/JS only, token-gated loopback server and strict CSP.

## Environment-limited live checks

- No `DEEPSEEK_API_KEY` is available in the build environment, so a paid live multimodal image inference was not executed. The data-URL multimodal contract and fail-loud path are tested.
- The build environment does not expose the Docker CLI, so a real production Docker/Nginx/Certbot deployment was not performed here. Existing typed operations tests remain green.
- Target-server qualification should run `term5 --selftest`, `production_readiness`, a disposable app/browser audit, then one real health-gated deployment before enabling automatic rollback or host-service writes.
