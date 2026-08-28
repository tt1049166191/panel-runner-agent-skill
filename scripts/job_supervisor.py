from __future__ import annotations

"""Run an arbitrary command and publish a minimal JSON lifecycle record."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


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


def child_process_options() -> dict[str, Any]:
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish lifecycle JSON for an arbitrary job")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--expected-command-substring", required=True)
    parser.add_argument("job_command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    artifact = args.artifact.resolve()
    state_path = artifact / "panel_job.json"
    command = list(args.job_command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("job command is required after --")
    command_display = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    hint = args.expected_command_substring.strip()
    if not hint or hint.casefold() not in command_display.casefold():
        raise SystemExit("job command does not contain the expected identity substring")

    base: dict[str, Any] = {
        "schema_version": "panel_job_supervisor_v1",
        "status": "starting",
        "supervisor_pid": os.getpid(),
        "project_root": str(project_root),
        "artifact": str(artifact),
        "command": command,
        "command_display": command_display,
        "expected_command_substring": hint,
        "started_at": now_iso(),
    }
    atomic_write_json(state_path, base)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
            **child_process_options(),
        )
        running = {**base, "status": "running", "pid": process.pid, "job_pid": process.pid}
        atomic_write_json(state_path, running)
        returncode = process.wait()
        final = {
            **running,
            "status": "completed" if returncode == 0 else "failed",
            "exit_code": returncode,
            "finished_at": now_iso(),
        }
        atomic_write_json(state_path, final)
        return int(returncode)
    except BaseException as error:
        atomic_write_json(
            state_path,
            {
                **base,
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
                "finished_at": now_iso(),
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
