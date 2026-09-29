# v5.3.0-alpha2 UI Qualification Gate

Before promoting this UI beyond alpha:

- Exercise the desktop three-pane layout through the SSH tunnel on the real Syntal-TWO runtime.
- Verify Chat refresh restores the bounded recent human-facing conversation.
- Run at least one long Docker build and one Nginx/TLS operation and confirm the Inspector remains responsive.
- Verify Apps actions (start/restart/stop/open/logs) against real registered applications.
- Verify the Deployments view against multiple live deployments.
- Check dark and light modes in Brave/Chrome.
- Check mobile widths around 390px and tablet widths around 768px.
- Confirm no tool messages, turn-context blocks or reasoning_content appear through `/api/history`.
- Confirm the UI remains accessible only through loopback/SSH tunneling.
