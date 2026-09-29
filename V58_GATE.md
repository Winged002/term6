# term_5 5.8.0 Qualification Gate

`5.8.0` passes the Configuration & Secrets consolidation gate.

## Final local qualification

```text
pytest                         135 / 135 PASS
doctor/selftest                 52 / 52 non-failing
critical failures                0
typed tools                    139
procedural skills               25
Python compileall               PASS
JavaScript syntax               PASS
```

## Configuration & Secrets

- project/environment configuration persistence: PASS
- example/code/Compose variable discovery: PASS
- secret values excluded from model-facing status/context: PASS
- dynamic secret telemetry redaction: PASS
- vault mode `0600`: PASS
- materialized environment file mode `0600`: PASS
- automatic env-file Git ignore protection: PASS
- `.env.example` remains trackable: PASS
- masked env-editor round-trip preserves secrets: PASS
- human input required → configured → resume: PASS
- SMTP trusted-side secret resolution: PASS (local/fake-server contract)
- production-readiness configuration gate: PASS
- token-gated Configuration API smoke: PASS

## Regression

All v5.2–v5.7 regression tests remain passing, including Working Memory, project-aware Git/GitHub contracts, browser/vision, Docker/Nginx/TLS/deployment logic, server management, daily history, Files, Tool Logs and Runs.

## Clean artifacts

- fresh wheel install reports `5.8.0`: PASS
- fresh wheel selftest: 52/52 PASS
- freshly extracted source ZIP pytest: 135/135 PASS
- freshly extracted source ZIP selftest: 52/52 PASS

## Environment-limited checks

The build environment does not provide a live DeepSeek API credential, authenticated GitHub CLI session, or Docker/Nginx/Certbot production host. Those external calls are therefore qualified on the target server. Chromium package/system discovery passes locally. The SMTP unit contract uses a local/fake server; real SMTP authentication is exercised by `configuration_test` on the target server after credentials are supplied.

## Secret storage statement

`.term5/secrets.json` is protected by Unix file mode `0600`, kept outside model context, and dynamically redacted from runtime telemetry. v5.8 does **not** claim cryptographic encryption at rest.
