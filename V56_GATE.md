# term_5 5.6.0 Qualification Gate

## Result

PASS for local release qualification.

## Automated regression

```text
pytest                 111 / 111 PASS
selftest/doctor          49 / 49 non-failing
critical failures         0
registered tools        116
procedural skills        23
```

## v5.6 workbench checks

- daily history rail present: PASS
- journal-backed full Markdown history: PASS
- empty calendar days represented and disabled: PASS
- browser-local timezone offset grouping: PASS
- Chat / Files / Tool Logs / Runs island: PASS
- screenshot archive rail: PASS
- screenshot artifact metadata listing: PASS
- text artifact bounded preview path: PASS
- inline `![label](art_...)` rendering contract: PASS
- escaped `![label]\\(art_...)` rendering contract: PASS
- persistent redacted Tool Logs: PASS
- hidden reasoning event exclusion: PASS
- Runs backed by compact episodes: PASS
- alpha3 single activity poller invariant: PASS
- no `setInterval`: PASS
- frontend JavaScript syntax (`node --check`): PASS
- Python compileall: PASS

## Live loopback HTTP smoke

A real `LocalWebApp` instance was started with token gating and seeded journal/artifact/episode state.

```text
/api/history-days       PASS
/api/history-day        PASS
/api/artifacts          PASS
/api/tool-logs          PASS
/api/runs               PASS
/api/artifact/<png>     PASS (image/png bytes)
```

## Packaging checks

```text
wheel build                  PASS
fresh wheel install          PASS
fresh wheel version          5.6.0
fresh wheel selftest         49 / 49 non-failing
fresh extracted ZIP pytest   111 / 111 PASS
fresh extracted ZIP selftest 49 / 49 non-failing
```

## Retained 5.5.1 behavior

- unlimited model/tool rounds by default: retained
- repeated identical loop safety: retained
- Working Memory/context GC: retained
- browser + vision evidence pipeline: retained
- Docker/Git/Nginx/TLS/deployment stack: retained
- bounded server management: retained
- no unrestricted shell: retained

## Environment-limited live checks

As with 5.5.x, the build environment does not provide a DeepSeek API key or Docker Engine, so a paid live vision inference and live Docker/Nginx/TLS deployment are not executed here. The corresponding runtime contracts remain covered by the inherited test suite and doctor reports them explicitly as UNKNOWN rather than PASS.
