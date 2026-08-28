from __future__ import annotations

"""Cross-platform detached launcher for a project job and panel watcher."""

import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
import urllib.request


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(query_limited_information, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def detached_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def spawn_detached(
    command: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.Popen[bytes]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_stream = stdout_path.open("ab", buffering=0)
    stderr_stream = stderr_path.open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=stdout_stream,
            stderr=stderr_stream,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
            **detached_kwargs(),
        )
    finally:
        stdout_stream.close()
        stderr_stream.close()
    return process


def stop_owned_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        os.killpg(process.pid, 15)
    except OSError:
        process.terminate()


def port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as stream:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            stream.bind((host, port))
        return True
    except OSError:
        return False


def wait_for_health(url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"panel watcher exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("ok") is True:
                    return
                last_error = f"unexpected health payload: {payload!r}"
        except Exception as error:  # Network exception types vary by platform.
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(0.25)
    raise RuntimeError(f"panel health check timed out: {last_error}")


def wait_for_job_identity(
    state_path: Path, supervisor: subprocess.Popen[bytes], timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                last = value
                if isinstance(value.get("job_pid"), int):
                    return value
                if value.get("status") in {"error", "failed"}:
                    raise RuntimeError(f"job supervisor failed: {value}")
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            pass
        if supervisor.poll() is not None and not last:
            raise RuntimeError(f"job supervisor exited with code {supervisor.returncode}")
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for job identity: {last}")


def resolve_command(command: list[str], project_root: Path) -> list[str]:
    if not command:
        raise ValueError("job command is required after --")
    resolved = list(command)
    executable = Path(resolved[0]).expanduser()
    if executable.is_absolute() and not executable.exists():
        raise ValueError(f"job executable does not exist: {executable}")
    if not executable.is_absolute() and any(separator in resolved[0] for separator in ("/", "\\")):
        candidate = (project_root / executable).resolve()
        if not candidate.exists():
            raise ValueError(f"job executable does not exist: {candidate}")
        resolved[0] = str(candidate)
    return resolved


def resolve_python(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or any(separator in value for separator in ("/", "\\")):
        resolved = candidate.resolve()
    else:
        discovered = shutil.which(value)
        if not discovered:
            raise ValueError(f"Python interpreter is not on PATH: {value}")
        resolved = Path(discovered).resolve()
    if not resolved.is_file():
        raise ValueError(f"Python interpreter does not exist: {resolved}")
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a project and its JSON panel independently of the current agent"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--expected-command-substring", required=True)
    parser.add_argument("--no-panel", action="store_true")
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--orphan-grace-seconds", type=float, default=60.0)
    parser.add_argument("--health-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--terminal-status", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("job_command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise SystemExit(f"project root does not exist: {project_root}")
    artifact = args.artifact.expanduser()
    if not artifact.is_absolute():
        artifact = project_root / artifact
    artifact = artifact.resolve()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("panel host must remain local")
    if not (1 <= args.port <= 65535):
        raise SystemExit("port must be between 1 and 65535")
    hint = args.expected_command_substring.strip()
    if not hint:
        raise SystemExit("expected command substring is required")
    command = list(args.job_command)
    if command and command[0] == "--":
        command = command[1:]
    try:
        command = resolve_command(command, project_root)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    command_display = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    if hint.casefold() not in command_display.casefold():
        raise SystemExit("expected command substring is not present in the job command")

    skill_root = Path(__file__).resolve().parent.parent
    runtime = skill_root / "scripts" / "panel_runtime.py"
    supervisor = skill_root / "scripts" / "job_supervisor.py"
    template = skill_root / "assets" / "panel.html"
    try:
        python = resolve_python(args.python)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    for required in (runtime, supervisor, template):
        if not required.is_file():
            raise SystemExit(f"required file does not exist: {required}")

    manifest_path = artifact / "panel_manifest.json"
    existing: dict[str, Any] = {}
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    for key in ("job_pid", "lifecycle_pid"):
        value = existing.get(key)
        if isinstance(value, int) and process_alive(value):
            raise SystemExit(f"refusing to collide with live {key}={value} in {manifest_path}")
    if not args.no_panel and not port_available(args.host, args.port):
        raise SystemExit(f"panel port is already in use: {args.host}:{args.port}")

    logs = artifact / "logs"
    static_output = artifact / "panel.final.html"
    runtime_state = artifact / "panel_runtime.json"
    panel_command = [
        str(python),
        "-B",
        "-u",
        str(runtime),
        "--artifact",
        str(artifact),
        "--template",
        str(template),
        "--runtime-state",
        str(runtime_state),
        "--expected-command-substring",
        hint,
        "--settle-seconds",
        str(max(0.0, args.settle_seconds)),
        "--orphan-grace-seconds",
        str(max(0.0, args.orphan_grace_seconds)),
    ]
    for status in args.terminal_status:
        panel_command.extend(["--terminal-status", status])
    if args.no_panel:
        panel_command.extend(["--headless", "--skip-static"])
    else:
        panel_command.extend(
            [
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--static-output",
                str(static_output),
            ]
        )

    supervisor_command = [
        str(python),
        "-B",
        "-u",
        str(supervisor),
        "--project-root",
        str(project_root),
        "--artifact",
        str(artifact),
        "--expected-command-substring",
        hint,
        "--",
        *command,
    ]

    manifest: dict[str, Any] = {
        "schema_version": "panel_detached_manifest_v1",
        "status": "planned" if args.dry_run else "starting",
        "created_at": now_iso(),
        "platform": sys.platform,
        "project_root": str(project_root),
        "artifact": str(artifact),
        "mode": "json_only" if args.no_panel else "live_panel",
        "url": None if args.no_panel else f"http://{args.host}:{args.port}",
        "python": str(python),
        "job_command": command,
        "job_command_display": command_display,
        "expected_command_substring": hint,
        "panel_command": panel_command,
        "supervisor_command": supervisor_command,
        "runtime_state": str(runtime_state),
        "static_output": None if args.no_panel else str(static_output),
        "logs": {
            "job_stdout": str(logs / "job.stdout.log"),
            "job_stderr": str(logs / "job.stderr.log"),
            "panel_stdout": str(logs / "panel.stdout.log"),
            "panel_stderr": str(logs / "panel.stderr.log"),
        },
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False))
        return 0

    artifact.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, manifest)
    panel_process = spawn_detached(
        panel_command,
        cwd=project_root,
        stdout_path=logs / "panel.stdout.log",
        stderr_path=logs / "panel.stderr.log",
    )
    manifest["lifecycle_pid"] = panel_process.pid
    manifest["lifecycle_started_at"] = now_iso()
    atomic_write_json(manifest_path, manifest)
    try:
        if args.no_panel:
            time.sleep(0.5)
            if panel_process.poll() is not None:
                raise RuntimeError(f"headless watcher exited with code {panel_process.returncode}")
        else:
            wait_for_health(
                f"http://{args.host}:{args.port}/api/health",
                panel_process,
                max(1.0, args.health_timeout_seconds),
            )
        supervisor_process = spawn_detached(
            supervisor_command,
            cwd=project_root,
            stdout_path=logs / "job.stdout.log",
            stderr_path=logs / "job.stderr.log",
        )
    except Exception:
        stop_owned_process(panel_process)
        manifest["status"] = "launch_failed"
        manifest["failed_at"] = now_iso()
        atomic_write_json(manifest_path, manifest)
        raise

    manifest["supervisor_pid"] = supervisor_process.pid
    try:
        job_state = wait_for_job_identity(artifact / "panel_job.json", supervisor_process)
    except Exception:
        stop_owned_process(supervisor_process)
        stop_owned_process(panel_process)
        manifest["status"] = "launch_failed"
        manifest["failed_at"] = now_iso()
        atomic_write_json(manifest_path, manifest)
        raise
    manifest["status"] = "running"
    manifest["job_pid"] = job_state["job_pid"]
    manifest["job_started_at"] = now_iso()
    manifest["dry_run"] = False
    atomic_write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "status": "launched",
                "job_pid": manifest["job_pid"],
                "supervisor_pid": supervisor_process.pid,
                "lifecycle_pid": panel_process.pid,
                "url": manifest["url"],
                "manifest": str(manifest_path),
                "runtime_state": str(runtime_state),
                "static_output": manifest["static_output"],
            },
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
