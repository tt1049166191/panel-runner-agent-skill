---
name: panel-runner
description: Run and monitor long-lived Windows projects when the user mentions panel in a project-execution or progress-monitoring context. Launch the job independently of Codex, expose a read-only live JSON panel, freeze a final static HTML snapshot, and safely stop verified background processes; also supports an explicit no-panel JSON-only mode. Do not use for scientific figure panels or ordinary UI layout discussion.
---

# Panel Runner

Treat `panel` as a lifecycle request when it refers to running, monitoring, resuming, or finishing a local project. Keep automatic selection enabled.

## Choose the mode

- Default to **live panel**: detach the project from Codex, serve a read-only localhost dashboard backed by authoritative JSON files, and supervise terminal cleanup.
- If the user says **不要面板**, **no panel**, or equivalent, use **JSON-only** mode: do not open a port or serve HTML. Keep a headless lifecycle watcher independent of Codex, and have Codex read the JSON files when reporting progress.
- A request that only asks for current status is read-only. Do not start, restart, register, stop, or replace background tasks unless the user also asked to run or change them.

## Establish the project contract first

1. Read the repository instructions and durable ledger before material work. If the ledger points to live `runner.json`, `progress.json`, or `metrics.json`, those files override snapshots.
2. Resolve the exact project root, working directory, interpreter, job launcher, artifact directory, terminal statuses, progress JSON paths, and expected command substring.
3. Reuse a compliant project-native dashboard or launcher when one exists. Do not replace it merely to use this skill's helpers.
4. Require the job to write JSON atomically and to publish a terminal status on success, failure, cancellation, and user stop. The panel is a reader; it must never send control commands back to the job.
5. Use unique task names and a project-local manifest. Refuse collisions instead of overwriting an existing task. Registration or launch must remain within the user's requested scope and applicable approval boundary.

Read [references/lifecycle-contract.md](references/lifecycle-contract.md) before changing task, JSON, cleanup, or staticization semantics.

## Run independently of Codex

On Windows, prefer hidden, `Limited`, triggerless Scheduled Tasks. A triggerless task continues after Codex closes but does not unexpectedly restart at logon. Register one exact job task and one lifecycle task:

- In live-panel mode, the lifecycle task serves the dashboard and watches terminal state.
- In JSON-only mode, the lifecycle task runs headless and opens no port.

Use [scripts/install_panel_tasks.ps1](scripts/install_panel_tasks.ps1) when the project fits its contract. Run `-DryRun` first. Start the lifecycle task before the job task; in live mode, require a successful `/api/health` check before starting the job.

The generic dashboard helper is [scripts/panel_runtime.py](scripts/panel_runtime.py), with [assets/panel.html](assets/panel.html) as its live/static template. Project-native implementations may be retained if they meet the same invariants.

## Observe progress

- Read `runner.json`, `progress.json`, and `metrics.json` as UTF-8 and report their modification times. Never infer liveness from a stale ledger snapshot alone.
- Cross-check a claimed `running` status against the recorded PID, exact task state, or heartbeat age. Say when state is stale or contradictory.
- In live mode, report the localhost URL, authoritative JSON paths, manifest path, and eventual static HTML path.
- In JSON-only mode, do not create or open a panel. Use `panel_runtime.py --snapshot-only` or read the JSON documents directly.

## Finalize terminal state

Terminal cleanup is part of completion, including success, failure, cancellation, screening-out, and user stop:

1. Wait for the JSON files to remain stable for the configured settle interval.
2. In live mode, embed the final snapshot into a standalone HTML file with polling disabled. The file must remain readable after the HTTP server exits.
3. Disable the exact lifecycle task, end and disable the exact job task, and terminate only a still-live process whose PID and command line match the recorded identity. Never kill by executable name, port alone, wildcard, or an unverified PID.
4. Let the lifecycle process exit, then verify the port is closed, recorded PIDs are absent, and tasks are no longer running. Do not unregister tasks or delete logs/artifacts by default.
5. Update the project ledger and append its required activity record after a material launch, terminal transition, or cleanup.

If staticization or cleanup is incomplete, report the exact failed invariant and leave the runtime manifest/logs intact for recovery.
