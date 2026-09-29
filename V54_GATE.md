# term_5 5.4.0-alpha1 Qualification Gate

Release focus: existing-application coding intelligence, guarded browser rendering and configurable multimodal screenshot inspection.

## Qualified locally

- pytest: **95 / 95 PASS**
- doctor/selftest: **49 / 49 non-failing**, 0 critical failures
- registered tools: **109**
- procedural skills: **22**
- Python compileall: PASS
- frontend JavaScript syntax (`node --check`): PASS
- deterministic Application Graph tests: PASS
- improvement planner + broad-task completion-gate tests: PASS
- binary screenshot artifact storage/retrieval: PASS
- DeepSeek-compatible multimodal payload contract (mock provider): PASS
- browser guard policy tests: PASS
- Chromium process launch: PASS using `/usr/bin/chromium`
- rendered in-memory DOM inspection + PNG screenshot artifact: PASS

## Environment-limited live checks

- The build environment has no `DEEPSEEK_API_KEY`; a real provider-side image inference call was **not** spent. The multimodal request adapter is unit-tested and the live path fails loudly if the configured DeepSeek/OpenAI-compatible model does not support image input.
- The build environment does not expose the Docker CLI; live Docker/Nginx/Certbot deployment remains unexecuted here. Existing v5.2 operations tests remain green.
- Chromium in this environment is subject to an administrator policy that blocks loopback HTTP navigation. Browser launch and rendered DOM/screenshot logic were qualified with an in-memory page instead. The runtime's own URL guard permits loopback applications by default.

## Release invariants

- v5.3 bounded Working Memory remains enabled.
- screenshot bytes are cold artifacts, not normal prompt history.
- no unrestricted host shell, Docker shell or model-facing arbitrary JavaScript-evaluation tool was added.
- public browser navigation remains disabled by default.
- broad existing-app improvement requests receive application mapping + improvement planning + one completion review round.
