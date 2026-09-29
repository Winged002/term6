# v6.2-ui3 UI audit

## Deployed failure evidence

The deployed console showed repeated Content Security Policy failures for inline styles in `renderSpatialWorkspace`. The application CSP is intentionally `style-src 'self'`. v6.2-ui2 nevertheless generated inline positions for project nodes, worker nodes, task chips, graph nodes, progress widths and workspace transforms.

Because Chromium discarded those inline styles, absolutely positioned elements fell back to the same origin in the canvas. This explains the observed cluster in the top-left corner and why pointer dragging appeared to do nothing even though the persistence API existed.

## Workspace

Changed the graph renderer to a CSP-safe SVG canvas. Dynamic locations are now SVG `x`, `y`, `width`, `height`, `viewBox`, `path d`, and `foreignObject` geometry attributes. Project cards remain normal HTML controls inside SVG `foreignObject` containers, so accessibility and click/drag behavior remain intact.

The packed layout is retained. Manual project positions remain persisted in `orchestrator_settings.workspace_layout_v2`. Dragging updates the SVG geometry and saves the final collision-resolved position.

A browser smoke with six projects measured zero project-card overlap and verified that dragging a project changed its screen position.

## Main coordinator inspector

The coordinator is now the default Workspace selection regardless of whether owners exist. Its inspector now shows:

- ready/coordinating state;
- active objective count;
- working, queued and human-attention counts;
- current active objectives;
- recent objectives;
- recent coordinator handoffs;
- owner/query concurrency controls.

## Icon alignment

Added explicit grid centering for compact rail buttons, top icon dock buttons, workspace lens buttons, icon buttons and project/inspector symbols. SVGs are block-level and centered rather than inheriting baseline/left alignment.

A Chromium geometry smoke verifies icon SVG centers against their parent button centers.

## Inbox

Replaced the previous generic two-panel dashboard with an attention-first surface:

- compact summary strip;
- left project-attention rail;
- grouped Priority Feed;
- Needs You, Coordination and Recent Updates groups;
- response previews inline;
- project navigation from each relevant item.

Routine state-query traffic remains suppressed.

## Operations

Replaced the old symmetric dashboard blocks with a production command-center hierarchy:

1. compact production summary;
2. environment readiness as primary surface;
3. open incidents / Needs Attention beside it;
4. releases as a full-width evidence row;
5. logs moved into a dedicated Evidence Console.

The data/API model is unchanged.

## Other CSP cleanup

Removed runtime inline styles from working-memory progress, objective progress, project headings, textarea autosizing, legacy task graph helpers and spatial zoom sizing. Textarea growth now uses the `rows` attribute.

## Browser audit

An in-memory Chromium smoke (used because direct loopback navigation is blocked in the build environment) verified:

- 6 project nodes rendered with zero pairwise overlap;
- pointer drag changed a project node position;
- central inspector populated;
- top-dock and rail icon centers aligned within 1.5px;
- Inbox rendered its priority feed;
- Operations rendered its Evidence Console;
- no desktop page-level horizontal overflow at 1440px;
- no Inbox page-level horizontal overflow at 390px.

The only console error in that in-memory harness is localStorage access on `about:blank`; the production app is served from HTTP and does not have that harness restriction.
