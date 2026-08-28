from __future__ import annotations

"""Read-only JSON panel plus terminal lifecycle watcher.

The project owns runner/progress/metrics JSON. This helper only reads those
documents and writes panel_manifest/panel_runtime/final HTML artifacts.
"""

import argparse
import ctypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse


DEFAULT_DOCUMENTS = ("runner.json", "progress.json", "metrics.json", "config.json")
TERMINAL_WORDS = {
    "complete",
    "completed",
    "success",
    "succeeded",
    "failed",
    "error",
    "stopped",
    "cancelled",
    "canceled",
    "terminated",
    "screened",
}
ACTIVE_WORDS = {
    "running",
    "starting",
    "training",
    "evaluating",
    "processing",
    "watching",
    "progress",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )


def read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value, None
        return {}, "root is not an object"
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"{type(error).__name__}: {error}"


def safe_stat(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "exists": True,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(
                timespec="seconds"
            ),
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        return {"exists": False, "size_bytes": 0, "modified_at": None, "mtime_ns": None}


def first_value(documents: dict[str, dict[str, Any]], keys: tuple[str, ...]) -> Any:
    for document_name in ("progress", "runner", "metrics", "config"):
        document = documents.get(document_name, {})
        for key in keys:
            value = document.get(key)
            if value is not None and value != "":
                return value
    return None


def normalize_status(value: Any) -> str:
    return str(value or "unknown").strip().lower()


def is_terminal_status(status: str, additional: set[str]) -> bool:
    normalized = normalize_status(status)
    if normalized in additional:
        return True
    words = {word for word in re.split(r"[^a-z0-9]+|_+", normalized) if word}
    if words & TERMINAL_WORDS:
        return True
    return "screened_out" in normalized


def is_active_status(status: str) -> bool:
    words = {word for word in re.split(r"[^a-z0-9]+|_+", normalize_status(status)) if word}
    return bool(words & ACTIVE_WORDS)


def as_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(query_limited_information, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def windows_command_line(pid: int) -> str | None:
    if os.name != "nt" or pid <= 0:
        return None
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = (
        "$p=Get-CimInstance Win32_Process -Filter \"ProcessId="
        + str(int(pid))
        + "\" -ErrorAction SilentlyContinue; if($p){[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$p.CommandLine}"
    )
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    command_line = result.stdout.strip()
    return command_line or None


def run_schtasks(arguments: list[str]) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "skipped": "not_windows", "arguments": arguments}
    try:
        result = subprocess.run(
            ["schtasks.exe", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip()[-2000:],
            "stderr": result.stderr.strip()[-2000:],
            "arguments": arguments,
        }
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}", "arguments": arguments}


def cleanup_runtime(
    *,
    job_task_name: str | None,
    lifecycle_task_name: str | None,
    pid: int | None,
    expected_command_substring: str | None,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if lifecycle_task_name:
        actions.append(
            {
                "kind": "disable_lifecycle_task",
                "task": lifecycle_task_name,
                "result": run_schtasks(["/Change", "/TN", lifecycle_task_name, "/Disable"]),
            }
        )
    if job_task_name:
        actions.append(
            {
                "kind": "end_job_task",
                "task": job_task_name,
                "result": run_schtasks(["/End", "/TN", job_task_name]),
            }
        )
        actions.append(
            {
                "kind": "disable_job_task",
                "task": job_task_name,
                "result": run_schtasks(["/Change", "/TN", job_task_name, "/Disable"]),
            }
        )

    pid_result: dict[str, Any] = {"pid": pid, "alive_before": process_alive(pid)}
    if pid_result["alive_before"]:
        command_line = windows_command_line(int(pid or 0))
        pid_result["command_line"] = command_line
        hint = (expected_command_substring or "").strip()
        if int(pid or 0) == os.getpid():
            pid_result["refused"] = "pid_is_lifecycle_process"
        elif not hint:
            pid_result["refused"] = "missing_expected_command_substring"
        elif not command_line:
            pid_result["refused"] = "command_line_unavailable"
        elif hint.casefold() not in command_line.casefold():
            pid_result["refused"] = "command_identity_mismatch"
        else:
            try:
                result = subprocess.run(
                    ["taskkill.exe", "/PID", str(int(pid or 0)), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                pid_result["kill"] = {
                    "ok": result.returncode == 0,
                    "returncode": result.returncode,
                    "stdout": result.stdout.strip()[-2000:],
                    "stderr": result.stderr.strip()[-2000:],
                }
            except (OSError, subprocess.SubprocessError) as error:
                pid_result["kill"] = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    time.sleep(0.25)
    pid_result["alive_after"] = process_alive(pid)
    actions.append({"kind": "verified_pid_cleanup", "result": pid_result})
    return {"attempted_at": now_iso(), "actions": actions}


class SnapshotReader:
    def __init__(self, artifact: Path, terminal_statuses: set[str]):
        self.artifact = artifact.resolve()
        self.terminal_statuses = terminal_statuses

    def snapshot(self, mode: str) -> dict[str, Any]:
        documents: dict[str, dict[str, Any]] = {}
        document_meta: dict[str, dict[str, Any]] = {}
        for filename in DEFAULT_DOCUMENTS:
            path = self.artifact / filename
            value, error = read_json(path)
            key = Path(filename).stem
            documents[key] = value
            document_meta[key] = {"path": str(path), **safe_stat(path), "read_error": error}

        status = normalize_status(first_value(documents, ("status", "state")))
        step = as_number(first_value(documents, ("step", "global_step", "batch")))
        total = as_number(first_value(documents, ("total_steps", "total_optimizer_steps", "steps")))
        fraction = as_number(first_value(documents, ("fraction_complete", "progress_fraction")))
        if fraction is None and step is not None and total and total > 0:
            fraction = step / total
        if fraction is not None:
            fraction = min(1.0, max(0.0, fraction))
        pid_value = first_value(documents, ("pid", "process_id"))
        try:
            pid = int(pid_value) if pid_value is not None else None
        except (TypeError, ValueError):
            pid = None
        terminal = is_terminal_status(status, self.terminal_statuses)
        return {
            "schema_version": "panel_runtime_snapshot_v1",
            "mode": mode,
            "captured_at": now_iso(),
            "artifact": str(self.artifact),
            "terminal": terminal,
            "derived": {
                "status": status,
                "phase": first_value(documents, ("phase", "stage", "message")),
                "step": step,
                "total_steps": total,
                "fraction_complete": fraction,
                "epoch": first_value(documents, ("epoch", "current_epoch")),
                "epochs": first_value(documents, ("epochs", "total_epochs")),
                "speed": first_value(
                    documents,
                    ("windows_per_second", "samples_per_second", "steps_per_second"),
                ),
                "eta_seconds": first_value(documents, ("eta_seconds",)),
                "updated_at": first_value(documents, ("updated_at", "heartbeat_at")),
                "pid": pid,
                "pid_alive": process_alive(pid),
            },
            "documents": documents,
            "document_meta": document_meta,
        }

    def fingerprint(self) -> tuple[tuple[str, int | None, int], ...]:
        values: list[tuple[str, int | None, int]] = []
        for filename in DEFAULT_DOCUMENTS:
            path = self.artifact / filename
            stat = safe_stat(path)
            values.append((filename, stat.get("mtime_ns"), int(stat.get("size_bytes", 0))))
        return tuple(values)


def escaped_script_json(value: dict[str, Any]) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_html(template: str, snapshot: dict[str, Any] | None) -> str:
    payload = "null" if snapshot is None else escaped_script_json(snapshot)
    state_marker = "__PANEL_STATIC_STATE_JSON__"
    bootstrap_marker = "__PANEL_BOOTSTRAP__"
    if state_marker not in template or bootstrap_marker not in template:
        raise ValueError("template is missing a panel state/bootstrap marker")
    if snapshot is None:
        bootstrap = """
async function tick(){
  try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw new Error(`HTTP ${response.status}`);render(await response.json())}
  catch(error){document.querySelector('#phase').textContent=`读取失败：${error}`}
  finally{setTimeout(tick,2000)}
}
tick();
""".strip()
    else:
        bootstrap = "render(embedded);"
    return template.replace(state_marker, payload, 1).replace(bootstrap_marker, bootstrap, 1)


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], reader: SnapshotReader, live_html: str):
        self.reader = reader
        self.live_html = live_html.encode("utf-8")
        super().__init__(address, handler_factory())


def handler_factory():
    class Handler(BaseHTTPRequestHandler):
        server: PanelServer

        def send_payload(self, status: int, content_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/health":
                payload = json.dumps({"ok": True, "time": now_iso()}).encode("utf-8")
                self.send_payload(200, "application/json; charset=utf-8", payload)
                return
            if path == "/api/state":
                payload = json.dumps(
                    self.server.reader.snapshot("live"), ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
                self.send_payload(200, "application/json; charset=utf-8", payload)
                return
            if path in {"/", "/index.html"}:
                self.send_payload(200, "text/html; charset=utf-8", self.server.live_html)
                return
            self.send_error(404)

        def log_message(self, *_: Any) -> None:
            return

    return Handler


def terminal_watcher(
    *,
    reader: SnapshotReader,
    runtime_path: Path,
    template: str,
    static_output: Path | None,
    settle_seconds: float,
    poll_seconds: float,
    job_task_name: str | None,
    lifecycle_task_name: str | None,
    expected_command_substring: str | None,
    orphan_grace_seconds: float,
    stop_event: threading.Event,
    server: PanelServer | None,
) -> None:
    terminal_first_seen: float | None = None
    stable_since: float | None = None
    previous_fingerprint: tuple[tuple[str, int | None, int], ...] | None = None
    orphan_first_seen: float | None = None
    while not stop_event.is_set():
        snapshot = reader.snapshot("headless" if server is None else "live")
        derived = snapshot["derived"]
        possible_orphan = (
            is_active_status(str(derived.get("status", "")))
            and derived.get("pid") is not None
            and not bool(derived.get("pid_alive"))
        )
        orphan_first_seen = (orphan_first_seen or time.monotonic()) if possible_orphan else None
        orphaned = bool(
            orphan_first_seen is not None
            and time.monotonic() - orphan_first_seen >= orphan_grace_seconds
        )
        if snapshot["terminal"] or orphaned:
            terminal_first_seen = terminal_first_seen or time.monotonic()
            fingerprint = reader.fingerprint()
            if fingerprint != previous_fingerprint:
                stable_since = time.monotonic()
                previous_fingerprint = fingerprint
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= settle_seconds:
                final_snapshot = reader.snapshot("static" if static_output else "headless")
                if orphaned and not final_snapshot["terminal"]:
                    final_snapshot["terminal"] = True
                    final_snapshot["derived"]["source_status"] = final_snapshot["derived"]["status"]
                    final_snapshot["derived"]["status"] = "orphaned_process_missing"
                static_error: str | None = None
                if static_output is not None:
                    try:
                        atomic_write_text(static_output, render_html(template, final_snapshot))
                    except Exception as error:  # Preserve cleanup evidence even if HTML fails.
                        static_error = f"{type(error).__name__}: {error}"
                runtime: dict[str, Any] = {
                    "schema_version": "panel_runtime_v1",
                    "status": "terminal_finalizing",
                    "terminal_first_seen_monotonic": terminal_first_seen,
                    "finalized_at": now_iso(),
                    "final_status": final_snapshot["derived"]["status"],
                    "artifact": str(reader.artifact),
                    "static_output": str(static_output) if static_output else None,
                    "static_error": static_error,
                    "cleanup": cleanup_runtime(
                        job_task_name=job_task_name,
                        lifecycle_task_name=lifecycle_task_name,
                        pid=final_snapshot["derived"].get("pid"),
                        expected_command_substring=expected_command_substring,
                    ),
                }
                runtime["status"] = "terminal_cleaned"
                atomic_write_json(runtime_path, runtime)
                stop_event.set()
                if server is not None:
                    server.shutdown()
                return
        else:
            terminal_first_seen = None
            stable_since = None
            previous_fingerprint = None
        stop_event.wait(max(0.2, poll_seconds))


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Read-only JSON panel and terminal watcher")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--template", type=Path, default=skill_root / "assets" / "panel.html")
    parser.add_argument("--static-output", type=Path)
    parser.add_argument("--runtime-state", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--orphan-grace-seconds", type=float, default=60.0)
    parser.add_argument("--terminal-status", action="append", default=[])
    parser.add_argument("--job-task-name")
    parser.add_argument("--lifecycle-task-name")
    parser.add_argument("--expected-command-substring")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--skip-static", action="store_true")
    parser.add_argument("--snapshot-only", action="store_true")
    parser.add_argument("--staticize-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact = args.artifact.resolve()
    if not artifact.is_dir():
        raise SystemExit(f"artifact directory does not exist: {artifact}")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("panel host must remain local")
    if not (1 <= args.port <= 65535):
        raise SystemExit("port must be between 1 and 65535")
    for task_name in (args.job_task_name, args.lifecycle_task_name):
        if task_name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,120}", task_name):
            raise SystemExit(f"unsafe Scheduled Task name: {task_name!r}")
    reader = SnapshotReader(
        artifact,
        {normalize_status(value) for value in args.terminal_status if value.strip()},
    )
    if args.snapshot_only:
        print(json.dumps(reader.snapshot("snapshot_only"), ensure_ascii=False, indent=2, allow_nan=False))
        return 0

    template = args.template.resolve().read_text(encoding="utf-8")
    static_output = None if args.skip_static else (args.static_output or artifact / "panel.final.html").resolve()
    runtime_path = (args.runtime_state or artifact / "panel_runtime.json").resolve()
    if args.staticize_only:
        if static_output is None:
            raise SystemExit("--staticize-only cannot be combined with --skip-static")
        snapshot = reader.snapshot("static")
        atomic_write_text(static_output, render_html(template, snapshot))
        print(json.dumps({"status": "staticized", "output": str(static_output)}, ensure_ascii=False))
        return 0

    stop_event = threading.Event()
    server: PanelServer | None = None
    mode = "headless" if args.headless else "live"
    if not args.headless:
        server = PanelServer((args.host, args.port), reader, render_html(template, None))

    atomic_write_json(
        runtime_path,
        {
            "schema_version": "panel_runtime_v1",
            "status": "watching",
            "mode": mode,
            "started_at": now_iso(),
            "pid": os.getpid(),
            "artifact": str(artifact),
            "url": None if args.headless else f"http://{args.host}:{args.port}",
            "static_output": str(static_output) if static_output else None,
            "job_task_name": args.job_task_name,
            "lifecycle_task_name": args.lifecycle_task_name,
        },
    )
    watcher = threading.Thread(
        target=terminal_watcher,
        kwargs={
            "reader": reader,
            "runtime_path": runtime_path,
            "template": template,
            "static_output": static_output,
            "settle_seconds": max(0.0, args.settle_seconds),
            "poll_seconds": max(0.2, args.poll_seconds),
            "job_task_name": args.job_task_name,
            "lifecycle_task_name": args.lifecycle_task_name,
            "expected_command_substring": args.expected_command_substring,
            "orphan_grace_seconds": max(0.0, args.orphan_grace_seconds),
            "stop_event": stop_event,
            "server": server,
        },
        daemon=True,
    )
    watcher.start()
    if args.headless:
        while not stop_event.wait(1.0):
            pass
    else:
        print(
            json.dumps(
                {"status": "serving", "url": f"http://{args.host}:{args.port}", "artifact": str(artifact)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()
            stop_event.set()
    watcher.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
