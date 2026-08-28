# Panel lifecycle contract

Use this reference when adapting launchers, progress JSON, Scheduled Tasks, cleanup, or final HTML behavior.

## Required artifacts

Keep runtime evidence inside the chosen artifact directory:

- `runner.json`: stable run identity and coarse status; include `pid`, `status`, command or entry-point identity, and immutable run configuration when practical.
- `progress.json`: frequently updated status; include `status`, `updated_at` or heartbeat, progress counters, and speed/ETA when available.
- `metrics.json`: append-only or replacement-safe evaluation history; never let a dashboard read race corrupt the writer.
- `panel_manifest.json`: project root, artifact root, exact task names, launcher path, interpreter, expected command substring, mode, URL, JSON paths, and static output.
- `panel_runtime.json`: lifecycle state, finalization time, cleanup attempts, warnings, and final static path.
- `panel.final.html`: standalone terminal snapshot in live-panel mode.

Additional project JSON files may be shown, but the job remains their only writer.

## JSON writer rules

Write UTF-8 JSON to a sibling temporary file, flush and close it, then replace the destination atomically. Use finite JSON numbers. Every exit path should try to publish one of these terminal meanings:

- success: `complete`, `completed`, `success`, or `succeeded`
- failure: `failed` or `error`
- stopped: `stopped`, `cancelled`, `canceled`, or `terminated`
- gated exit: `screened_out`

Compound statuses such as `stopped_by_user` or `training_complete_validation_selected` are acceptable. Do not label a recoverable retry as terminal while its supervisor intends to relaunch it.

## Separation of responsibilities

The job owns model/data/output state. The panel only reads JSON and optional hardware telemetry. It must not write commands, thresholds, selections, or control flags into job state.

The lifecycle watcher may write only panel-owned manifest/runtime/static files and may perform the predeclared terminal cleanup. It must not change scientific results or selection decisions.

## Windows task policy

- Prefer two hidden, `Limited`, triggerless tasks: `<prefix>_Job` and `<prefix>_Panel` or `<prefix>_Watcher`.
- Use exact task names and `MultipleInstances IgnoreNew`.
- Do not use `AtLogOn`, periodic triggers, wildcard task operations, or infinite restart settings by default.
- Refuse a task-name collision. Pick a new prefix after inspecting the existing task; do not silently replace it.
- A project-specific launcher may implement bounded resume/retry only when its checkpoints and retry semantics are already safe.

## Safe process cleanup

At terminal state, staticize before shutdown. Then:

1. Disable the lifecycle task so it cannot relaunch.
2. End and disable the exact job task.
3. If the JSON PID is still alive, retrieve its command line and compare it with the manifest's expected command substring.
4. Terminate the PID tree only after the match. Refuse the kill if the PID is the lifecycle process, the command line cannot be read, or the identity does not match.

Task-name cleanup and PID cleanup are complementary: the task operation stops the registered owner, while identity validation protects against PID reuse or unrelated Python processes.

## Static HTML acceptance criteria

The final HTML must:

- contain the final JSON snapshot inline;
- make no network or localhost request;
- show a visible static/final badge and capture time;
- retain progress, metrics, artifact path, and raw JSON inspection;
- open after the panel process and job task are stopped.

Keep the dynamic template and final snapshot separate unless the project explicitly chooses an atomic replacement strategy.

## JSON-only mode

JSON-only means no HTTP server and no browser launch. A headless lifecycle watcher may still run independently to perform terminal cleanup. Codex reports progress by reading the authoritative JSON files and their modification times. At terminal state, verify that the job and watcher have exited; no HTML artifact is required.

## Minimum completion report

Report:

- final status and terminal reason;
- artifact and authoritative JSON paths;
- final static HTML path, or explicitly `no panel / no HTML`;
- job/lifecycle task states and PID verification result;
- whether the localhost port is closed;
- ledger/activity record updated, when the project requires one.
