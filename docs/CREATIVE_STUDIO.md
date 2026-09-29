# term_6 Creative Studio

## Role

Creative Studio is a rendering subsystem, not the main agent. DeepSeek remains the coordinator and decides when a human-facing visual decision is worth creating. A generated mockup is evidence for a direction, not a replacement for implementation.

## Rendering paths

### Local SVG

`creative_svg_concepts` produces lightweight local concept cards. It is appropriate for simple palette, hierarchy, geometry, layout and direction choices and needs no external image provider.

### OpenAI image generation

`creative_image_concepts` uses the OpenAI Images API when configured. The default v6 model is:

```text
gpt-image-2.5-flare
```

Default output profile:

```text
quality = medium
size = 1536x1024
format = PNG
options = up to 4
```

term_6 generates each direction with its own prompt so the alternatives can be genuinely different rather than four random samples of one direction.

## Credentials

The configured key name defaults to `OPENAI_API_KEY`. Resolution order is:

1. host process environment;
2. active project's trusted Configuration secret vault;
3. active project's protected environment file.

The key is not included in `creative_status`, tool output, artifacts or My Tasks. Once resolved, it is also registered with the runtime redactor.

If the key is absent, term_6 creates/de-duplicates a deferred `configure` task and recommends the SVG path as an immediate fallback.

## Design loop

```text
material design question
       ↓
2–4 distinct directions
       ↓
render concepts
       ↓
My Tasks: choose
       ↓
selected artifact ID
       ↓
vision_inspect
       ↓
structured implementation direction
       ↓
edit actual project
       ↓
browser render
       ↓
vision_compare / diagnostics
```

Safe visual choices can use `AUTO_DECIDE` after the configured timeout. The selected artifact remains in the local artifact archive and is token-gated in the workbench.

## Provider contract

v6 targets OpenAI's GPT Image 2.5 generation API and stores returned base64 PNG output. A downloadable provider URL is also accepted defensively. Prompt length is bounded to the Images API's GPT-image prompt limit.
