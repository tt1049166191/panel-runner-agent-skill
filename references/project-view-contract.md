# Project-authored view contract

Read this when implementing a project's view for the supplied runtime. This is a transport interface, not a page template: choose the markup, layout, styles, labels, metrics, and charts from the actual project. The skill ships no reusable visual page.

## Minimal integration

Create a self-contained UTF-8 HTML file in the project. It must define `render(snapshot)` and bind `const embedded = __PANEL_STATIC_STATE_JSON__;` in a normal inline script. Place `__PANEL_BOOTSTRAP__` after that definition and after the elements needed for rendering. Each marker must occur exactly once. No other DOM IDs, CSS classes, cards, or chart types are required.

The runtime replaces the state marker with `null` in live mode and an escaped final JSON object in static mode. It replaces the bootstrap marker with polling of `/api/state` in live mode and `render(embedded);` in static mode. Keep fetching, timers, and API calls out of the authored render function so they are absent from the final file. Use inline styles, scripts, and visual assets; the helper serves only the HTML and its two JSON endpoints, not arbitrary asset files. Native dashboards with different dependencies may retain their own serving/export implementation if they satisfy the lifecycle contract.

Render updates into the existing document. Handle absent values without throwing or inventing progress. Use text nodes or appropriate escaping for project-provided strings. A `panel-error` event on `window` carries a readable error in `event.detail` when polling or rendering fails; use it to display connection/staleness state if useful. Show the snapshot's mode and capture time so the final view is clearly identifiable.

## Snapshot data

- `mode`: `live` or `static` for views.
- `captured_at`, `terminal`, `artifact`: capture time, terminal flag, and run directory.
- `documents`: raw JSON objects or arrays, keyed by their document name.
- `document_meta`: each document's path, existence, modification time, size, and read error. Missing or partially written data must not appear as a healthy current measurement.
- `derived`: lifecycle status, PID and liveness, plus optional legacy progress shortcuts. These shortcuts do not define the page layout. Use raw project fields for domain-specific stages, values, units, histories, and outputs.

The default document names are `runner`, `progress`, `metrics`, `config`, `panel_job`, and `panel_manifest`. Add or map project files with repeated `--document NAME=PATH`. Relative JSON paths resolve against the artifact directory; absolute paths are also supported. Files may be created by the job after launch. Only files explicitly selected for monitoring should be included.

`--document progress=state/current.json` maps a project's object to the conventional lifecycle/progress role. An additional name such as `queue` is exposed as `snapshot.documents.queue` without contributing to terminal-state detection. `panel_job`, `panel_manifest`, and `panel_runtime` are reserved and cannot be overridden. Named documents are included in terminal settling and final snapshots.

## Launch and acceptance

Pass `--view PATH` to the portable launcher; relative view paths resolve against the project root. Pass `-View PATH` to the Windows backend. `panel_runtime.py` also accepts `--view PATH` directly and retains `--template` as a compatibility alias for a supplied file. Live/static mode requires a view; JSON-only and snapshot-only modes require none. Existing commands that relied on the removed built-in HTML must now provide a project-authored view.

Verify that project-specific information actually changes when the authoritative JSON changes, that missing/error states are readable, and that the final file preserves the same useful presentation without network requests. A raw-JSON diagnostic section can be useful, but should not substitute for presenting the project's actual workflow.
