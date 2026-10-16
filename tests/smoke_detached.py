from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "launch_detached.py"
sys.path.insert(0, str(ROOT / "scripts"))
from panel_runtime import SnapshotReader, parse_document_specs, process_alive, render_html


JOB_SOURCE = r'''
from __future__ import annotations
from pathlib import Path
import json
import sys
import time

artifact = Path(sys.argv[1])
artifact.mkdir(parents=True, exist_ok=True)
state = artifact / "nested" / "queue.json"
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(json.dumps({"files_exported": 7, "total_files": 9}), encoding="utf-8")
time.sleep(2)
print("arbitrary background job completed", flush=True)
if len(sys.argv) > 2 and sys.argv[2] == "fail":
    raise SystemExit(3)
'''


# Test-only export view, deliberately without the former template's DOM IDs/cards.
VIEW_SOURCE = '''<!doctype html><meta charset="utf-8"><title>Export queue</title>
<p id="export-count"></p><p id="capture"></p>
<script>
const embedded=__PANEL_STATIC_STATE_JSON__;
function render(s){
  const queue=s.documents.queue||{};
  document.getElementById('export-count').textContent=
    `Exported ${queue.files_exported ?? '?'} / ${queue.total_files ?? '?'} files`;
  document.getElementById('capture').textContent=`${s.mode} ${s.captured_at}`;
}
__PANEL_BOOTSTRAP__
</script>'''


def check_render(html: str, expected: str, live_state: dict | None = None) -> None:
    """Exercise the supplied view and injected bootstrap in a JS VM when Node is present."""
    node = shutil.which("node")
    if not node:
        return
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    script = r'''
const vm=require('node:vm'),fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8')),elements={},timers=[];
const context={
  document:{getElementById:id=>(elements[id]??={textContent:''})},
  console:{warn(){}},window:{dispatchEvent(){}},CustomEvent:class {},
  setTimeout:callback=>timers.push(callback),
  fetch:async()=>({ok:true,json:async()=>input.live_state})
};
vm.runInNewContext(input.script,context);
setImmediate(()=>{
  if(elements['export-count'].textContent!==input.expected)throw Error('incorrect project fields');
  if(input.live_state){
    input.live_state.documents.queue.files_exported=8;
    const next=timers.shift();if(!next)throw Error('missing live poll');next();
    setImmediate(()=>{
      if(elements['export-count'].textContent!=='Exported 8 / 9 files')throw Error('update not rendered');
    });
  }else if(timers.length)throw Error('static page still polling');
});
'''
    subprocess.run(
        [node, "-e", script],
        input=json.dumps({"script": "\n".join(scripts), "expected": expected, "live_state": live_state}),
        text=True, capture_output=True, timeout=10, check=True,
    )


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


def wait_for_processes_to_exit(launch_state: dict, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [launch_state[key] for key in ("job_pid", "supervisor_pid", "lifecycle_pid")
                 if process_alive(launch_state.get(key))]
        if not alive:
            return
        time.sleep(0.1)
    raise AssertionError(f"processes remained alive after terminal state: {alive}")


def check_windows_dry_runs(project: Path, job: Path, view: Path) -> None:
    if os.name != "nt":
        return
    artifact = project / "task-dry-run"
    artifact.mkdir()
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [
        str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(ROOT / "scripts/install_panel_tasks.ps1"),
        "-ProjectRoot", str(project), "-ArtifactDirectory", str(artifact),
        "-JobLauncher", str(job), "-Python", sys.executable,
        "-TaskPrefix", f"PanelViewSmoke_{os.getpid()}",
        "-ExpectedCommandSubstring", job.name, "-DryRun",
    ]
    live = subprocess.run(
        [*command, "-View", str(view), "-Documents", "queue=nested/queue.json"],
        capture_output=True, text=True, timeout=30,
    )
    assert live.returncode == 0, live.stderr
    plan = json.loads(live.stdout)
    assert plan["view"] == str(view)
    assert Path(plan["documents"]["queue"]) == artifact / "nested/queue.json"
    headless = subprocess.run(
        [*command, "-NoPanel"], capture_output=True, text=True, timeout=30, check=True,
    )
    assert json.loads(headless.stdout)["view"] is None


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="panel-runner-smoke-") as temporary:
        project = Path(temporary)
        job = project / "smoke_job.py"
        job.write_text(JOB_SOURCE, encoding="utf-8")
        view = project / "exports.view.html"
        view.write_text(VIEW_SOURCE, encoding="utf-8")
        check_windows_dry_runs(project, job, view)

        missing_view_artifact = project / "missing-view"
        missing_view = subprocess.run(
            [sys.executable, str(LAUNCHER), "--project-root", str(project),
             "--artifact", str(missing_view_artifact), "--expected-command-substring",
             "smoke_job.py", "--", sys.executable, str(job), str(missing_view_artifact)],
            capture_output=True, text=True, timeout=20,
        )
        assert missing_view.returncode != 0 and "requires --view" in missing_view.stderr
        assert not missing_view_artifact.exists()

        invalid = project / "invalid.html"
        invalid.write_text("<html>missing integration markers</html>", encoding="utf-8")
        invalid_view = subprocess.run(
            [sys.executable, str(LAUNCHER), "--project-root", str(project),
             "--artifact", str(missing_view_artifact), "--view", str(invalid),
             "--expected-command-substring", "smoke_job.py", "--", sys.executable,
             str(job), str(missing_view_artifact)],
            capture_output=True, text=True, timeout=20,
        )
        assert invalid_view.returncode != 0 and "invalid project view" in invalid_view.stderr
        assert not missing_view_artifact.exists()

        # Named project documents retain their native shapes and affect settling,
        # but a subtask's status must not terminate the parent run.
        data = project / "data"
        data.mkdir()
        queue = data / "queue.json"
        history = data / "history.json"
        queue.write_text('{"status":"completed","files_exported":1}', encoding="utf-8")
        history.write_text('[{"files_exported":1}]', encoding="utf-8")
        (data / "panel_job.json").write_text("[]", encoding="utf-8")
        reader = SnapshotReader(data, set(), parse_document_specs(
            ["queue=queue.json", "history=history.json"], data))
        initial = reader.snapshot("live")
        assert initial["documents"]["history"] == [{"files_exported": 1}]
        assert not initial["terminal"]
        fingerprint = reader.fingerprint()
        queue.write_text('{"files_exported":100}', encoding="utf-8")
        assert reader.fingerprint() != fingerprint
        for spec in (["panel_job=other.json"], ["queue=a.json", "queue=b.json"]):
            try:
                parse_document_specs(spec, data)
            except ValueError:
                pass
            else:
                raise AssertionError("reserved/duplicate document alias accepted")
        check_render(render_html(VIEW_SOURCE, initial), "Exported 1 / ? files")

        dry_artifact = project / "dry-run"
        dry = subprocess.run(
            [
                sys.executable,
                str(LAUNCHER),
                "--project-root",
                str(project),
                "--artifact",
                str(dry_artifact),
                "--view",
                str(view),
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
                "--view",
                str(view),
                "--document",
                "queue=nested/queue.json",
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
        live_url = launch_state["url"]
        wait_for(artifact / "nested/queue.json", lambda value: value.get("files_exported") == 7)
        with urllib.request.urlopen(live_url) as response:
            live_html = response.read().decode("utf-8")
        with urllib.request.urlopen(live_url + "/api/state") as response:
            live_state = json.loads(response.read())
        assert "Export queue" in live_html and 'id="epoch"' not in live_html
        assert live_state["documents"]["queue"]["files_exported"] == 7
        check_render(live_html, "Exported 7 / 9 files", live_state)
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
        assert "Export queue" in html and '"files_exported":7' in html
        check_render(html, "Exported 7 / 9 files")

        wait_for_closed_port(port)
        wait_for_processes_to_exit(launch_state)

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
        headless_launch = json.loads(headless.stdout)
        assert headless_launch["url"] is None
        headless_runtime = wait_for(
            headless_artifact / "panel_runtime.json",
            lambda value: value.get("status") == "terminal_cleaned",
        )
        assert headless_runtime["final_status"] == "failed"
        assert read_json(headless_artifact / "panel_job.json")["exit_code"] == 3
        assert headless_runtime["static_output"] is None
        assert not (headless_artifact / "panel.final.html").exists()
        wait_for_processes_to_exit(headless_launch)

    print(json.dumps({"status": "ok", "platform": sys.platform}))


if __name__ == "__main__":
    main()
