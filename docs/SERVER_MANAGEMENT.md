# Server Management — term_5 v5.5.0

v5.5 adds a bounded host-management layer around the existing Docker/Git/Nginx/TLS deployment runtime. The goal is production visibility and controlled recovery without giving the model a generic root shell.

## Read-only host tools

- `server_status` — hostname/platform, uptime, load average, CPU count, memory and disk pressure.
- `server_ports` — bounded listening-socket inventory using `ss` with fixed argv.
- `server_service_status` — systemd state for an allowlisted service.
- `server_journal` — bounded journal entries for an allowlisted service.
- `server_audit` — compact host + service + port audit.
- `production_readiness` — host, Docker applications, Git, Nginx, Certbot and deployment readiness in one preflight.

These tools require only `ops.server.read` and do not mutate the host.

## Service mutation

`server_service_control` supports only:

```text
start
stop
restart
reload
```

and only for names explicitly present in:

```toml
[operations]
managed_services = ["docker", "nginx"]
```

Mutation additionally requires:

```toml
[security]
allow_server_write = true
```

The gate is independent of Nginx/TLS/Git write permissions. There is no arbitrary command parameter and no shell string is evaluated.

## Production workflow

The recommended v5.5 production loop is:

```text
production_readiness
        ↓
application_map / improvement plan when changing an existing app
        ↓
source edits + tests
        ↓
browser/vision verification when UI is affected
        ↓
Git stage + review + commit
        ↓
deployment_deploy
        ↓
Docker health
        ↓
validated Nginx
        ↓
TLS/public probe
        ↓
release record
```

Use host service control only when the problem is actually at the host-service layer. Application containers should normally be managed through the typed `app_*` tools instead.
