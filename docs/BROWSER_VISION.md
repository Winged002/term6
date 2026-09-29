# Browser + Vision — v5.4

## Browser runtime

The browser runtime is a guarded Playwright/Chromium renderer for applications term_5 is already developing. It is not a general unrestricted web browser.

Default network policy:

```text
loopback             allowed
explicit allowlist   allowed
arbitrary public     blocked
private/internal     blocked unless explicitly allowlisted
```

There is no model-facing generic JavaScript evaluation tool. DOM inspection uses a fixed internal probe.

`browser_audit_pages` is the preferred high-level tool for UI work. It renders multiple routes at up to three viewports and stores PNG screenshots while returning compact evidence: HTTP status, title, headings, buttons, form/input/image counts, horizontal overflow, console/page errors and failed network requests.

## Screenshot artifacts

PNG bytes are written under `.term5/artifacts/`. The normal model tool history only receives artifact IDs and metadata. This preserves v5.3's bounded-context design.

## Vision

`vision_inspect` reads screenshot artifacts locally and makes one multimodal provider call. Up to `vision.max_images` images may be inspected together.

`vision_compare` is specialized for BEFORE/AFTER evidence. The prompt explicitly labels image 1 as BEFORE and image 2 as AFTER and asks whether the requested improvement is materially visible, including regressions and remaining work.

Vision is capability-checked at runtime. v5.4 does not claim that every model named `deepseek-flash` supports image input. If the configured endpoint rejects multimodal payloads, the tool returns the provider error and term_5 continues with browser DOM/diagnostic evidence.

## Installation

```bash
pip install -e '.[browser]'
```

Then either provide a system Chromium/Chrome executable, or run:

```bash
playwright install chromium
```
