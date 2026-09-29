# term_6 v6.2-ui2 Qualification Gate

## Scope

- full workbench page audit;
- collision-safe Workspace packing;
- draggable/persistent Project nodes;
- live inter-agent response refresh;
- retained uncapped Project Owner execution;
- retained coordinator-first v6.2 runtime.

## Required gates

- [x] All 14 workbench routes remain present.
- [x] Unique DOM IDs across the workbench.
- [x] Shared modern page shell for non-Workspace pages.
- [x] Packed layout reserves project footprint and avoids default overlaps.
- [x] Projects can be dragged with pointer input.
- [x] Final drop collision resolution.
- [x] Whole personalized layout persists across runtime restart.
- [x] Reset Layout clears personalized positions.
- [x] Drag redraws are animation-frame throttled.
- [x] Live message responses update without manual browser refresh.
- [x] Live message sync reuses the existing activity poll.
- [x] Project Inspector renders recent response text.
- [x] Project Owner hard iteration cap remains removed.

Final test/self-test counts and artifact hashes are recorded in `VALIDATION.txt` / `BUILD_INFO.json`.
