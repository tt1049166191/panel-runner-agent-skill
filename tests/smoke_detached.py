from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "launch_detached.py"


JOB_SOURCE = r'''
from __future__ import annotations
from pathlib import Path
import sys
import time

artifact = Path(sys.argv[1])
artifact.mkdir(parents=True, exist_ok=True)
time.sleep(0.5)
print("arbitrary background job completed", flush=True)
if len(sys.argv) > 2 and sys.argv[2] == "fail":
    raise SystemExit(3)
'''


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def wait_for(path: Path, predicate, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            last = read_json(path)
            if predicate(last):
                return last
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting for {path}; last={last!r}")


def wait_for_closed_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.settimeout(0.5)
            if stream.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"panel port remained open: {port}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="panel-runner-smoke-") as temporary:
        project = Path(temporary)
        job = project / "smoke_job.py"
        job.write_text(JOB_SOURCE, encoding="utf-8")

        dry_artifact = project / "dry-run"
        dry = subprocess.run(
            [
                sys.executable,
                str(LAUNCHER),
                "--project-root",
                str(project),
                "--artifact",
                str(dry_artifact),
                "--expected-command-substring",
                "smoke_job.py",
                "--dry-run",
                "--",
                sys.executable,
                str(job),
                str(dry_artifact),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        assert json.loads(dry.stdout)["dry_run"] is True
        assert not dry_artifact.exists()

        artifact = project / "live-run"
        port = free_port()
        launched = subprocess.run(
            [
                sys.executable,
                str(LAUNCHER),
                "--project-root",
                str(project),
                "--artifact",
                str(artifact),
                "--port",
                str(port),
                "--settle-seconds",
                "0.5",
                "--orphan-grace-seconds",
                "2",
                "--health-timeout-seconds",
                "10",
                "--expected-command-substring",
                "smoke_job.py",
                "--",
                sys.executable,
                str(job),
                str(artifact),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        launch_state = json.loads(launched.stdout)
        assert launch_state["status"] == "launched"
        runtime = wait_for(
            artifact / "panel_runtime.json",
            lambda value: value.get("status") == "terminal_cleaned",
        )
        assert runtime["final_status"] == "completed"
        job_state = read_json(artifact / "panel_job.json")
        assert job_state["status"] == "completed"
        assert job_state["exit_code"] == 0
        static = artifact / "panel.final.html"
        html = static.read_text(encoding="utf-8")
        assert '"mode":"static"' in html
        assert "fetch('/api/state'" not in html
        assert "__PANEL_STATIC_STATE_JSON__" not in html

        wait_for_closed_port(port)

        headless_artifact = project / "headless-run"
        headless = subprocess.run(
            [
                sys.executable,
                str(LAUNCHER),
                "--project-root",
                str(project),
                "--artifact",
                str(headless_artifact),
                "--no-panel",
                "--settle-seconds",
                "0.5",
                "--orphan-grace-seconds",
                "2",
                "--expected-command-substring",
                "smoke_job.py",
                "--",
                sys.executable,
                str(job),
                str(headless_artifact),
                "fail",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        assert json.loads(headless.stdout)["url"] is None
        headless_runtime = wait_for(
            headless_artifact / "panel_runtime.json",
            lambda value: value.get("status") == "terminal_cleaned",
        )
        assert headless_runtime["final_status"] == "failed"
        assert read_json(headless_artifact / "panel_job.json")["exit_code"] == 3
        assert headless_runtime["static_output"] is None
        assert not (headless_artifact / "panel.final.html").exists()

    print(json.dumps({"status": "ok", "platform": sys.platform}))


if __name__ == "__main__":
    main()
