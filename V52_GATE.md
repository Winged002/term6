# v5.2.0-alpha2 qualification gate

A server should not be treated as qualified solely because the offline release tests pass.

Before production use, verify on the target host:

- Git CLI and repository behavior;
- Docker Engine and Compose;
- Nginx paths and permissions;
- Nginx service control under the account running term_5;
- real DNS resolution for a disposable hostname;
- Certbot Nginx plugin using staging;
- live HTTP/HTTPS probes;
- Mongo/Postgres backup policy for the application's actual credentials;
- Flask/Django migration preset if used;
- manual release rollback from one known-good commit to another;
- SSH-tunnel access to the loopback-only web UI;
- Git remote authentication if remote operations are enabled.

Do not enable automatic rollback until manual rollback has been qualified on the actual server.
