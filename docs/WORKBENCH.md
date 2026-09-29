# v5.6 Workbench

## Information model

v5.6 deliberately separates four concepts that were previously mixed together:

```text
Chat       human prompt/final-response transcript
Files      generated artifacts and screenshots
Tool Logs  operational execution evidence
Runs       compact completed-task memory
```

This mirrors the underlying runtime stores instead of forcing everything into a conversation transcript.

## Daily history

Daily history is derived from `.term5/journal.jsonl`. A journal record contains the timestamp, full user prompt, full final assistant response and selected reasoning mode. It does not contain hidden chain-of-thought or raw tool protocol.

The client requests a rolling date calendar and renders empty dates as disabled. Labels are localized in the browser:

- Today
- Yesterday
- weekday name for recent days
- absolute date + weekday for older dates

The browser timezone offset is sent when loading history so turns near midnight are grouped according to the operator's local day.

## Inline screenshot artifacts

The Markdown renderer recognizes screenshot artifact references:

```text
![label](art_0123456789abcdef)
![label]\(art_0123456789abcdef)
```

The UI replaces the reference with a local image card whose URL includes the current UI token. No screenshot bytes are inserted into provider history.

## Files / screenshot archive

`/api/artifacts` returns metadata only. The Files surface renders:

- screenshot artifacts with thumbnails;
- tool/text artifacts with bounded preview actions;
- artifact ID, creation time and size.

The screenshot archive is visually separated as a side rail on wide screens and stacks beneath the artifact list on narrower layouts.

## Tool Logs

Tool Logs reads the redacted persistent event journal. It intentionally omits any event type representing hidden reasoning content. Payloads are bounded in the UI and can be filtered locally.

## Runs

Runs uses episodic memory, which is compact by design. It is appropriate for task summaries and changed-file/tool metadata, but not for full transcript reconstruction. That is why daily Chat history uses the journal instead.

## Polling / efficiency

The alpha3 invariant remains: one adaptive live activity poller, approximately 900 ms while a turn runs and 3 s while idle. Files, Tool Logs, Runs, Apps, Deployments and Memory load only when opened or explicitly refreshed.
