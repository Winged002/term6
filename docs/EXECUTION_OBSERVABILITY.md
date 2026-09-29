# Execution Observability — term_5 5.5.1

5.5.1 exposes operational evidence without exposing hidden chain-of-thought.

## Unlimited rounds

```toml
[scheduler]
max_tool_iterations = 0
max_turn_seconds = 0
loop_abort_repeats = 8
```

`max_tool_iterations = 0` means there is no arbitrary model/tool round ceiling. A finite value can still be configured explicitly. The runtime can stop on a final answer, failure, optional wall-clock safety, context/provider constraints, or repeated identical tool-batch loop safety.

## Activity evidence

The Activity drawer shows:

- current iteration and `unlimited` policy;
- model-selected next tool actions;
- active tools and redacted arguments;
- browser navigation/audit state;
- screenshot capture state;
- recent screenshot thumbnails, viewport and artifact IDs;
- configured/resolved vision model;
- vision request running/completed/failed state;
- image artifact IDs passed to vision;
- bounded vision finding previews;
- browser/vision events in the execution timeline.

This is execution telemetry, not private model reasoning.

## Screenshot security

Screenshots remain binary artifacts under `.term5/artifacts/`. The loopback web UI can render only screenshot artifacts through the token-gated `/api/artifact/<artifact-id>` route. Other artifact kinds are not exposed by that image endpoint.
