# Configuration & Secrets — v5.8

v5.8 makes project configuration a first-class part of engineering and production readiness.

## Model boundary

The model can see configuration names and state such as `configured`, `missing`, `required`, and service grouping. It cannot retrieve secret values. Model-facing tools reject attempts to set secret values.

Secrets are resolved only inside the trusted host runtime for materialization and typed connection tests. They are stored in `.term5/secrets.json` with Unix mode `0600`. This is local filesystem protection and model isolation; v5.8 does **not** claim cryptographic encryption at rest.

## Discovery

`configuration_discover` identifies environment-variable names from common sources without returning values:

- `.env.example`, `.env.sample`, `.env.template`, `env.example`, `example.env`;
- Python `os.getenv`, `os.environ.get`, and `os.environ[...]`;
- JavaScript/TypeScript `process.env.*`;
- Docker Compose `${VAR}` references.

Variables are classified into common service groups such as SMTP, DeepSeek, databases, Redis, S3/AWS, Stripe, Twilio, Cloudflare, GitHub, or generic application configuration.

## Human input

When work requires a human-supplied credential, the agent uses `configuration_require`. The run enters a visible `waiting for configuration` state rather than asking the user to paste a secret into chat.

The Configuration workspace presents the exact missing variables. Secret fields are password inputs. Existing secrets are represented only as `Configured`; the value is not sent back to the browser until the user explicitly replaces it, and even then it goes only to the trusted loopback configuration endpoint.

After the required values are supplied, **Save & Resume** verifies completeness and starts a continuation turn telling the agent to resume the blocked work.

## Environment files

Configuration is scoped by project and environment. Production materializes to `.env`; other environments use `.env.<environment>`. These files are written with mode `0600` and term_5 ensures they are ignored by Git while leaving `.env.example` trackable.

The UI can open and edit protected environment files. Secret lines are masked as `••••••••`. Saving an unchanged mask preserves the existing secret.

## Connection tests

Typed tests are available for common services. They execute locally and return only bounded diagnostic results:

- SMTP: DNS/TCP, optional TLS, and authentication; no email is sent.
- DeepSeek: authenticated provider `/models` request.
- MongoDB, Redis, PostgreSQL/database URLs: host/port connectivity preflight.

Tests resolve secrets locally and do not echo them.

## Production readiness

`production_readiness` includes production configuration for registered projects. Missing required production variables are surfaced as warnings/blockers before release work proceeds.
