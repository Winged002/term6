# term_6 v6.0.0 Qualification Gate

## Source qualification

- version: `6.0.0`
- pytest: `159/159` PASS
- doctor/selftest: `55/55` non-failing
- critical failures: `0`
- typed tools: `157`
- procedural skills: `28`
- Python compileall: PASS
- workbench JavaScript syntax (`node --check`): PASS

## Collaborative-autonomy gates

- durable run persistence / mode 0600: PASS
- My Tasks persistence / mode 0600: PASS
- safe AUTO_DECIDE default selection: PASS
- DEFER transition and pending state: PASS
- BLOCK/approval no-auto-approval invariant: PASS
- global My Tasks API/workbench: PASS
- configuration task secret-value isolation: PASS
- configuration completion resolves linked task by variable names only: PASS
- resolved human task resumes same durable run: PASS
- autonomy scheduler timeout → auto-decision → same-run resume: PASS

## Creative Studio gates

- local SVG concept generation: PASS
- creative artifacts are viewable/token-gated: PASS
- missing OpenAI key → deferred configuration task + SVG fallback: PASS
- OpenAI Images adapter (offline mocked provider): PASS
- OpenAI API key absent from tool output/artifacts: PASS
- resolved host/project key added to runtime redactor: PASS
- default image model: `gpt-image-2.5-flare`
- live OpenAI Images call: UNKNOWN in build environment (no live project key used)

## Compatibility gates

- v5.7 project/Git/GitHub suite: PASS via full regression
- v5.8 Configuration & Secrets suite: PASS via full regression
- v5.9 Production Intelligence suite: PASS via full regression
- browser/vision/working-memory/operations historical suites: PASS via full regression
- existing `.term5` state reused: PASS by design; no migration required
- `term5` CLI retained + `term6` CLI added: PASS

## Final artifact qualification

- fresh wheel install: PASS
- fresh wheel `term6 --version`: `6.0.0` PASS
- fresh wheel selftest: `55/55` non-failing
- fresh extracted ZIP pytest: `159/159` PASS
- fresh extracted ZIP selftest: `55/55` non-failing
- final-source archive contains no runtime/test caches: PASS

Live provider/host checks remain environment-dependent and are not fabricated.
