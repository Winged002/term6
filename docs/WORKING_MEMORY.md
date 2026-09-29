# term_5 v5.3 Working Memory

v5.3 separates **active working context** from **durable project memory**.
The provider prompt is no longer the archive of everything term_5 has ever done.

## Memory hierarchy

```text
active working context
  recent conversational turns
  current turn context
  relevant semantic memory
  relevant completed-task episodes
  current tool protocol
        |
        +--> filesystem / Git = current + historical source truth
        +--> deployment registry = runtime/release truth
        +--> artifact store = verbose tool/build/log output
        +--> episodic store = compact completed-task outcomes
```

### Active working context

Default budgets:

- target: 120k estimated tokens
- proactive compaction threshold: 180k
- defensive input ceiling: 360k
- provider safety reserve: 32k
- recent full conversational turns: 4

The provider's physical window remains 1,048,576 tokens, but v5.3 deliberately does not try to fill it during ordinary work.

### Dynamic completion budgets

The old 384k configured output limit is retained as a physical ceiling for compatibility, but it is no longer requested on ordinary calls.

Default per-reasoning caps:

- NONE: 16k
- LOW: 24k
- HIGH: 64k
- MAX: 96k

For every provider request term_5 computes:

```text
available = physical_window - prompt - tool_schemas - safety_reserve
requested_output = min(configured_ceiling, reasoning_cap, available)
```

This prevents the failure mode where a large prompt plus a blindly requested 384k completion crosses the provider's total context limit.

### Completed-turn GC

During an active tool loop, assistant reasoning content and tool protocol messages are preserved because the provider may require them for continuation.

After the turn finishes, term_5:

1. creates a compact episode from the user request, final outcome, tools used, changed files, and failures;
2. removes generated turn-context/evidence messages;
3. removes intermediate assistant tool-call messages and tool responses;
4. evicts completed hidden `reasoning_content`;
5. retains the user request and final answer for a small recent-turn window;
6. relies on episodic retrieval for older completed work.

The raw execution timeline remains available in local events/audit data instead of being repaid to the model every turn.

### Tool artifact cold store

Tool outputs larger than `context.tool_inline_chars` are written under:

```text
.term5/artifacts/YYYY-MM-DD/art_<id>.txt
```

The model receives a bounded head/tail preview plus an artifact reference. The `artifact_read` tool retrieves or searches the full artifact only when exact omitted details are relevant.

This is especially useful for:

- Docker builds
- test logs
- Nginx logs
- container logs
- large file reads
- deployment diagnostics

### Episodic memory

Completed tasks are written to `.term5/episodes.jsonl` as compact records. Retrieval is local, lexical + recency ranked, and bounded by the current query.

v5.3 can also read the v5.2 episode/journal format (`ts`, `prompt`, `answer`, `reasoning`) and treats those records as legacy episodes.

### Legacy session migration

If a pre-v5.3 saved session is already very large, v5.3 performs a one-time in-memory migration when it loads:

- identifies actual user/final-answer pairs;
- distills them into episodic records;
- drops old raw tool exchanges and hidden reasoning;
- keeps only the configured recent conversational window.

No tools or side effects are replayed during migration.

### Authoritative state

v5.3 deliberately treats these as better memories than old chat text:

- current source: filesystem
- source history: Git
- application runtime: Docker/App registry
- production state: deployment registry + Nginx/TLS inspection
- verbose execution evidence: artifact store
- durable facts: semantic memory
- completed work: episodic memory

This avoids remembering stale code or stale operational output just because it once appeared in a chat.

## Observability

`/context` prints working-memory statistics. `/status` includes a `working_memory` object.

The web UI Live Execution card shows estimated active tokens, dynamic output budget, episode count, archived tool-result count, and latest compaction savings.

## Configuration

See `[context]` in `term5.toml.example`. Important environment overrides include:

```text
TERM5_ACTIVE_TARGET_TOKENS
TERM5_SOFT_COMPACT_TOKENS
TERM5_HARD_INPUT_TOKENS
TERM5_CONTEXT_SAFETY_TOKENS
TERM5_RECENT_FULL_TURNS
TERM5_TOOL_INLINE_CHARS
TERM5_TOOL_PREVIEW_CHARS
TERM5_EPISODE_RECALL_ITEMS
TERM5_EPISODE_RECALL_CHARS
TERM5_TOTAL_CONTEXT_TOKENS
```
