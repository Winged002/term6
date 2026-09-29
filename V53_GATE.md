# term_5 5.3 Release Gate

Before promotion beyond alpha1:

- [ ] Run the full offline test suite.
- [ ] Run `term5 --selftest` with zero critical failures.
- [ ] Load a copy of a large v5.2 autosave and verify startup compaction.
- [ ] Verify active context remains approximately bounded over 50+ real turns.
- [ ] Verify archived Docker/test/Nginx logs can be recovered with `artifact_read`.
- [ ] Verify dynamic output budget is below provider total-window headroom for LOW/HIGH/MAX.
- [ ] Verify a real DeepSeek multi-tool turn preserves reasoning/tool protocol until completion.
- [ ] Verify completed hidden reasoning does not appear in the next provider request.
- [ ] Verify long KoalaCare-style work no longer approaches the physical 1,048,576-token window under normal operation.
- [ ] Re-run Docker/Nginx/TLS/Git production qualification from the v5.2 gate.

The physical context window is emergency capacity, not the normal operating target.
