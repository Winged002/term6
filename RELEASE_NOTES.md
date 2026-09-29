# term_6 v6.2-ui4 — Release Notes

v6.2-ui4 closes the remaining Inspector observability gap in the modern Workspace. It keeps the v6.2-ui3 CSP-safe geometry, alignment, Inbox and Operations redesign unchanged.

## Live Inspector feedback

- Added a dedicated **Live feedback** section to the Central Coordinator, Project Owner, Task/Worker and Objective Inspector views.
- New `agent.*`, `agents.*`, `objective.*` and `coordinator.*` events are injected into the Inspector immediately from the existing `/api/activity` stream.
- Feedback includes task starts/completions/failures, model steps, tool activity, next actions, dependency releases, contract publication/forecast events, coordinator handoffs and recovery events.
- Inter-agent response text is rendered inline in the same feedback area through the existing message synchronization path.
- Feedback is selection-scoped: a project sees its own tasks/messages/events; a task sees its task events; an objective sees its delegated task events; the coordinator sees organization-level handoffs and significant outcomes.
- The activity stream remains single-poller; v6.2-ui4 does **not** introduce a second feedback polling loop.

## Validation

- Added dedicated Inspector-feedback regression tests.
- Added a doctor gate that verifies the feedback renderer, event ingestion path, response integration and feedback styling remain present.

## Previous v6.2-ui3 changes

v6.2-ui3 is the CSP/layout reliability and interface-hierarchy revision. It keeps the v6.2 coordinator/Project Owner backend intact.

## Fixed deployed Workspace failure

- Removed inline `style` positioning that was blocked by the existing `style-src 'self'` CSP.
- Kept the strict CSP; no `unsafe-inline` was added.
- Rebuilt spatial rendering around SVG geometry and `foreignObject` project/task controls.
- Project nodes now render in their intended packed positions and remain draggable/persistent.
- Removed all runtime `.style.*` mutations from the app bundle.

## Inspector and alignment

- Central coordinator is always a valid/default selection.
- Coordinator inspector now shows active/recent objectives, handoffs, attention/work counts and concurrency.
- Explicitly centered navigation, workspace and entity icons.

## Inbox and Operations

- Inbox is now attention-first: summary, project attention, Needs You, Coordination and Recent Updates.
- Operations is now a production command center: environment readiness, Needs Attention, releases and a separate Evidence Console.

## Validation

- Full regression suite includes CSP-safe rendering, inspector content, icon-centering markers, Inbox hierarchy and Operations hierarchy.
- In-memory Chromium smoke verifies zero project-card overlap, real pointer drag movement, centered icons, and desktop/mobile overflow behavior.

## Previous v6.2-ui2 changes

v6.2-ui2 is the full-workbench audit and spatial interaction revision on top of the working v6.2 coordinator/Project Owner architecture.

## Modern shell

- Compact 64px icon rail replaces the wide permanent sidebar.
- Centered icon dock replaces the large text island.
- Hover labels appear below/alongside icons for discoverability without visual clutter.
- Added Search / Jump control with `Cmd/Ctrl+K` focus to the persistent command composer.
- History moves to an on-demand popover instead of permanently occupying the sidebar.
- Theme, execution and project/operations access remain available from compact controls.

## Workspace layout and interaction

- Replaced the previous ring algorithm with collision-aware packed slots that reserve a larger footprint for project cards plus worker/task satellites.
- Project nodes are pointer-draggable. Manual positions are stored in `orchestrator_settings.workspace_layout_v2` and survive browser refresh and service restart.
- Dragging freezes the current auto-layout before movement, so unrelated projects do not jump around during the gesture.
- Drop positions are collision-resolved before persistence.
- Added Reset Layout to return to automatic packing.
- Drag redraws are requestAnimationFrame-throttled for smoother movement.
- Project systems use intuitive icons (identity/lock, files/folder, code, calendar, database, chat, boards, AI, generic product).
- Normal zoom shows project card + owner/knowledge chips; low zoom collapses to icon-only nodes with hover labels.
- Active worker nodes remain visible without filling the canvas with every queued item.
- Queued task chips expand only for the selected project at high zoom.
- Organization timeline is now an on-demand overlay.
- Inspector, objective lens, dependency lens, drag-to-reassign, drag-to-depend, multi-project selection and always-on command bar remain intact.

## Unlimited Project Owner iterations

- Removed the hard `owner_max_iterations` execution stop from `ProjectOwnerAgent.execute`.
- Existing `owner_max_iterations` configuration is retained as a deprecated compatibility field but is ignored by owner execution.
- Removed `TERM61_OWNER_MAX_ITERATIONS` runtime handling and numeric validation.
- Central coordinator iteration budgeting is unchanged and remains intentionally bounded.

## Compatibility

The release upgrades v6.2 in place. No database migration is required for the UI changes. Existing queues, objectives, contracts, capsules, settings, sessions, project state and human tasks are preserved.

## Live messages

- Workspace and Agent Inbox no longer require a manual browser refresh to show message responses.
- Message synchronization piggybacks on the existing activity polling loop; no second scheduler/poller was introduced.
- Message status/response signatures invalidate only the relevant Inbox/Inspector state.
- Project Inspector shows recent inter-agent messages and response text inline.

## Full page audit

- Audited all 14 workbench routes.
- Normalized the non-Workspace content shell to a consistent 1240px rhythm.
- Unified page-head typography, panel/card borders, responsive stats/grids, long-text wrapping and <=900px single-column behavior.
- Files, Tool Logs, Runs, Configuration and Operations now align to the same shell rather than retaining older widths.
- See `UI_AUDIT.md` for findings and changes per page.
