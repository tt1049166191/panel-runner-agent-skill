---
name: panel-runner
description: "Run and monitor long-lived local projects with a dashboard designed around the project's actual workflow and data. When the user asks for a panel during execution or progress monitoring, detach the job from the agent, build or reuse a project-specific read-only JSON view, freeze its final HTML, and safely clean up verified processes. Cross-platform; supports no-panel JSON-only mode."
---

# Panel Runner

Treat `panel` as a lifecycle request when it refers to running, monitoring, resuming, or finishing a local project. This skill is agent-client agnostic: use it when the current client can load Agent Skills/`SKILL.md`, execute local commands, and retain access to the same host filesystem.

## Choose the mode

- Default to **live panel**: detach the project from the agent, serve a read-only localhost dashboard backed by authoritative JSON files, and supervise terminal cleanup.
- If the user says **不要面板**, **no panel**, or equivalent, use **JSON-only** mode: do not open a port or serve HTML. Keep a headless lifecycle watcher independent of the agent, which reads the JSON files when reporting progress.
- A request that only asks for current status is read-only. Do not start, restart, register, stop, or replace background tasks unless the user also asked to run or change them.

## Establish the project contract first

1. Read the applicable repository instructions. Read or update a durable ledger only if that project requires one; this skill creates no ledger requirement. Authoritative live state takes precedence over historical snapshots.
2. Resolve the exact project root, working directory, interpreter, job launcher, artifact directory, terminal statuses, progress JSON paths, and expected command substring.
3. Reuse a compliant project-native dashboard or launcher when one exists. Do not replace it merely to use this skill's helpers.
4. Reuse project-authored atomic JSON when available. For an arbitrary command with no progress protocol, use `job_supervisor.py` to publish the minimum PID, command, exit code, and terminal state in `panel_job.json`. The panel is a reader; it must never send control commands back to the job.
5. Use a distinct artifact directory and project-local manifest for each run. On the optional Windows backend, also use unique task names. Refuse collisions instead of overwriting a live run. Registration or launch must remain within the user's requested scope and applicable approval boundary.
6. Determine the host lifetime. A detached process can survive closing a local agent application, but cannot survive destruction of its VM, container, cloud worktree, or ephemeral sandbox. Disclose this limit before promising persistence.

Read [references/lifecycle-contract.md](references/lifecycle-contract.md) before changing task, JSON, cleanup, or staticization semantics.

## Design the panel from the project

In live-panel mode, inspect the actual entry points, stages, state-file samples, available metrics, logs, and expected deliverables before writing HTML. Determine what the user needs to see to judge progress, spot a stall, understand a failure, and locate the result.

Choose content and presentation from that evidence: a batch job might need item counts, failures, and throughput; a build might need stage results and diagnostics; rendering might need a frame queue and previews; training might need metric history. These are examples, not required sections. Use the project's vocabulary, units, existing design, and the user's preferences. A valid existing project dashboard should be adapted when useful.

Author the view in the project, typically `panel.view.html`, with a layout and visual hierarchy suited to that task. There is no bundled page or default card layout. Renaming headings, changing colors, or dumping JSON into a generic page is not sufficient project adaptation. Show only supported fields; unknown progress stays unknown rather than becoming a fabricated percentage or ETA. When only lifecycle state exists, build a compact lifecycle view and explain the missing telemetry instead of inventing metrics.

For the supplied runtime, read [references/project-view-contract.md](references/project-view-contract.md) and pass the authored HTML via `--view PATH`. Use repeated `--document NAME=PATH` arguments for additional project JSON, including nested or external paths; render the corresponding raw `snapshot.documents` fields without forcing the project into a training schema. Keep process supervision separate from presentation.

Check the authored view with real or representative state, including an early/missing state, an active update, and the terminal snapshot. Confirm important project information is visible, labels/units are correct, errors and stale data are distinguishable, and the final HTML works offline. JSON-only mode skips view design entirely.

## Run independently of the agent

Use [scripts/launch_detached.py](scripts/launch_detached.py) as the portable default on Windows, macOS, and Linux. In live mode, provide `--view PATH`; the launcher rejects a missing or incompatible view before starting processes. Run it with `--dry-run` first and pass the project command after `--`. It starts the lifecycle watcher before the job, verifies the live-panel health endpoint, writes exact PIDs and commands to `panel_manifest.json`, and returns without owning either child process.

On Windows, [scripts/install_panel_tasks.ps1](scripts/install_panel_tasks.ps1) remains an optional stronger backend when Task Scheduler is available and persistence after closing the agent must be maximally reliable. It registers hidden, `Limited`, triggerless tasks so the job does not unexpectedly restart at logon:

- In live-panel mode, the lifecycle task serves the dashboard and watches terminal state.
- In JSON-only mode, the lifecycle task runs headless and opens no port.

The shared transport and lifecycle helper is [scripts/panel_runtime.py](scripts/panel_runtime.py). It serves the project-authored view and freezes that same view with final data, without choosing the UI. The optional Windows backend accepts the view as `-View PATH` and additional JSON as `-Documents @('NAME=PATH', ...)`. Project-native implementations may be retained if they meet the same invariants.

## Observe progress

- Read the project's authoritative JSON, the explicitly mapped additional documents, and the supervisor's `panel_job.json` when using the portable launcher. Report modification times; never infer liveness from a stale project snapshot alone.
- Cross-check a claimed `running` status against the recorded PID, exact task state, or heartbeat age. Say when state is stale or contradictory.
- In live mode, report the localhost URL, authoritative JSON paths, manifest path, and eventual static HTML path.
- In JSON-only mode, do not create or open a panel. Use `panel_runtime.py --snapshot-only` or read the JSON documents directly.

## Finalize terminal state

Terminal cleanup is part of completion, including success, failure, cancellation, screening-out, and user stop:

1. Wait for the JSON files to remain stable for the configured settle interval.
2. In live mode, embed the final snapshot into a standalone HTML file with polling disabled. The file must remain readable after the HTTP server exits.
3. Terminate only a still-live process whose PID and command line match the recorded identity. On POSIX, use the detached process group when it is safely isolated. On Windows Task Scheduler, also disable the exact lifecycle task and end/disable the exact job task. Never kill by executable name, port alone, wildcard, or an unverified PID.
4. Let the lifecycle process exit, then verify the port is closed and recorded PIDs are absent. Do not delete logs/artifacts or unregister service definitions by default.
5. Follow any repository-specific durable logging or ledger rule, but do not invent one for projects that do not require it.

If staticization or cleanup is incomplete, report the exact failed invariant and leave the runtime manifest/logs intact for recovery.
