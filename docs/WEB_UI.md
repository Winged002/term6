# term_5 Web UI — v5.5.0

v5.5 deliberately retains the stable 5.3-alpha3 shell rather than the denser alpha2 layout.

```text
┌───────────────┬──────────────────────────────────────┐
│ Navigation    │ Work area                            │
│               │                                      │
│ Chat          │ conversation / current resource view │
│ Apps          │                                      │
│ Deployments   │                                      │
│ Product       │                                      │
│ Memory        │                                      │
│               │                                      │
│ Activity      │                         [drawer →]   │
└───────────────┴──────────────────────────────────────┘
```

The Activity drawer contains the live execution timeline, active tools, working-memory context and system state. It is not part of the normal page width.

## Polling policy

There is exactly one recurring frontend poll: Activity.

- running turn: approximately every 900 ms
- idle: approximately every 3 seconds
- a poll never starts while the prior poll is still pending

Apps, Deployments and Memory are fetched when opened or manually refreshed. This avoids constant Docker/status work merely because the UI is open.

## Responsive behavior

Desktop keeps the sidebar visible. Narrow desktop/tablet collapses labels. Mobile turns the sidebar into a drawer. Activity remains a separate right-side drawer. Sidebar and Activity are mutually exclusive and share a single scrim.

## Security

The server remains loopback-only and token-gated. The page loads only local CSS/JS. No CDN, analytics, remote font, frontend framework or third-party image service is used. Human chat history excludes tool protocol messages, generated context blocks and hidden reasoning.
