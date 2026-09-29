# DeepSeek provider assumptions — v5.3.0-alpha2

DeepSeek Flash remains an inference provider only. term_5 owns durable cognitive state, workspace operations, Docker application state/registry, verification and UI locally.

- OpenAI-compatible base URL: `https://api.deepseek.com`
- default model: `deepseek-flash`
- configured context ceiling: 1,000,000 tokens
- configured maximum chat/reasoning output: 384,000 tokens
- reasoning modes: none, low, high, max
- FIM: separate non-thinking beta completion path
- term_5 FIM hard completion cap: 4096 tokens
- vision: deliberately disabled

## Host Docker boundary

Docker is not a model provider and term_5 is not containerized. The local host process invokes a typed Docker/Compose adapter. The model sees structured tools, never an arbitrary Docker/shell command string. Generated application containers are not given Docker Engine access.

## Local ownership

The following remain local:

- sessions and turn checkpoints
- semantic memory and episode journal
- workspace graph
- transaction history/backups
- audit/event traces
- app registry (`.term5/apps.json`)
- generated Compose/application source
- Docker/HTTP health observations
- console and token-gated loopback web UI


## v5.3 context budgeting

The runtime treats 1,048,576 tokens as a physical window, not a normal operating target. Main-loop calls use WorkingMemoryManager dynamic budgets and reserve tool-schema + safety headroom. Default reasoning-mode completion caps are NONE 16k, LOW 24k, HIGH 64k and MAX 96k.
