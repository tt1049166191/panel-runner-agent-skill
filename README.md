# Panel Runner — Universal Agent Skill

[中文](#中文说明) · [English](#english)

## 中文说明

`panel-runner` 是一个面向任意本地长时任务的通用 Agent Skill，例如构建、批处理、数据转换、下载、渲染、导出、训练或本地服务。它兼容能够读取 `SKILL.md`、执行本地命令并持续访问同一文件系统的智能体客户端，可在 Windows、macOS 和 Linux 上使用。

当用户在项目运行或进度监控语境中提到 **panel** 时，它会让任务脱离当前智能体会话运行，并提供只读实时面板。它尤其适合希望任务启动后关闭 Codex 或其他智能体客户端的用户：在确认后台任务与监控稳定后，项目会在同一台本地计算机上继续独立执行。

### 主要功能

- 使用纯 Python 跨平台启动器，将项目和生命周期监控进程与智能体终端分离。
- 自动记录任务 PID、命令、退出码和终态；项目已有 `runner.json`、`progress.json` 或 `metrics.json` 时也会实时聚合显示。
- 任务进入成功、失败、取消或用户停止等终态后，将最终状态保存为可离线打开的静态 `panel.final.html`。
- 根据 PID、完整命令行和独立进程组校验身份后再清理后台，避免误杀其他进程。
- 用户明确说“不要面板”时，切换到 JSON-only 模式：不开端口、不启动网页，仅监听 JSON 并负责终态清理。
- Windows 可选用隐藏、Limited、无触发器的计划任务后端，获得更稳健的本地脱离运行能力。

### 支持范围

该 Skill 不绑定某个模型、智能体品牌或账号。只要客户端支持 Agent Skills/`SKILL.md` 并允许本地文件与进程操作，就可以读取和执行它。`agents/openai.yaml` 只是可选的 Codex 界面元数据，不会建立账号绑定。

完整后台生命周期需要：

- Python 3.10 或更高版本；
- 本地 Windows、macOS 或 Linux 主机；
- 智能体有权访问项目目录、启动本地进程和监听 localhost。

如果智能体运行在会随会话销毁的云端沙箱、临时容器或 ephemeral worktree 中，关闭会话可能同时销毁运行环境；任何 detached launcher 都无法绕过这个宿主生命周期限制。

### 安装

将仓库复制到智能体客户端支持的个人 Skills 目录。以 Codex 为例：

```text
Windows: %USERPROFILE%\.codex\skills\panel-runner
macOS/Linux: ~/.codex/skills/panel-runner
```

其他客户端请按其 Agent Skills/`SKILL.md` 目录规范安装。也可以让智能体直接读取本仓库中的 `SKILL.md`。

### 使用示例

```text
用 panel 独立运行这个耗时项目，我关闭智能体后它也要继续运行。
```

```text
用 panel-runner 独立运行这个任务，但不要面板，直接读取 JSON 告诉我进度。
```

### 跨平台启动器

智能体应先运行 DryRun：

```bash
python scripts/launch_detached.py \
  --project-root /path/to/project \
  --artifact artifacts/run-001 \
  --expected-command-substring train.py \
  --dry-run \
  -- python train.py --output artifacts/run-001
```

确认后去掉 `--dry-run`。JSON-only 模式增加 `--no-panel`。

Windows 上如需使用计划任务增强后端，可先执行：

```powershell
.\scripts\install_panel_tasks.ps1 <参数> -DryRun
```

### 项目状态约定

普通命令无需改造：内置 supervisor 会自动写入 PID、命令、退出码和终态。若项目希望展示更丰富的步骤、速度、ETA 或指标，可以额外以 UTF-8 原子写入 `runner.json`、`progress.json` 和 `metrics.json`。Skill 会把实际命令、PID、平台和输出路径记录到 `panel_manifest.json`，并拒绝任务冲突、端口冲突或无法验证身份的进程清理。

## English

`panel-runner` is a universal Agent Skill for any long-running local job, including builds, batch processing, data conversion, downloads, rendering, exports, training, and local services. It works with agent clients that can read `SKILL.md`, execute local commands, and retain access to the same filesystem, on Windows, macOS, and Linux.

When **panel** is mentioned in an execution or progress-monitoring context, the skill detaches the job from the current agent session and exposes a read-only live dashboard. It is especially useful for users who want to close Codex or another agent client after launch: once the detached job and monitor are verified healthy, the project continues on the same local machine.

### Features

- Uses a pure-Python cross-platform launcher to detach both the job and lifecycle monitor from the agent terminal.
- Automatically records the job PID, command, exit code, and terminal state, while also aggregating existing `runner.json`, `progress.json`, or `metrics.json` files when present.
- Freezes terminal state into an offline `panel.final.html` on success, failure, cancellation, or user stop.
- Cleans up only after verifying PID, full command line, and process-group identity.
- Supports JSON-only mode with no HTTP port or web panel.
- Retains an optional hidden, Limited, triggerless Windows Task Scheduler backend for stronger local persistence.

### Compatibility

The skill is not tied to a model, agent vendor, or account. Any client that supports Agent Skills/`SKILL.md` and permits local filesystem and process access can interpret it. `agents/openai.yaml` is optional Codex UI metadata and does not bind an account.

Full lifecycle execution requires:

- Python 3.10 or later;
- a local Windows, macOS, or Linux host;
- permission to access the project, start local processes, and listen on localhost.

If the agent runs inside a cloud sandbox, temporary container, or ephemeral worktree that is destroyed with the session, closing the session may destroy the host itself. No detached launcher can outlive that host lifecycle.

### Installation

Copy the repository into the personal Skills directory supported by the agent client. For Codex, for example:

```text
Windows: %USERPROFILE%\.codex\skills\panel-runner
macOS/Linux: ~/.codex/skills/panel-runner
```

For other clients, follow their Agent Skills/`SKILL.md` directory convention, or point the agent directly at this repository's `SKILL.md`.

### Examples

```text
Run this long job with a panel and keep it running after I close the agent client.
```

```text
Use panel-runner to run this independently, but do not start a panel; report progress from the JSON files.
```

### Portable launcher

Run a DryRun first:

```bash
python scripts/launch_detached.py \
  --project-root /path/to/project \
  --artifact artifacts/run-001 \
  --expected-command-substring train.py \
  --dry-run \
  -- python train.py --output artifacts/run-001
```

Remove `--dry-run` after review. Add `--no-panel` for JSON-only mode.

On Windows, the optional Task Scheduler backend starts with:

```powershell
.\scripts\install_panel_tasks.ps1 <arguments> -DryRun
```

### Runtime contract

An ordinary command requires no modification: the built-in supervisor records its PID, command, exit code, and terminal state. A project may additionally atomically write UTF-8 `runner.json`, `progress.json`, and `metrics.json` files to expose richer steps, speed, ETA, or metrics. The skill records the exact command, PIDs, platform, and output paths in `panel_manifest.json`, and refuses live-run collisions, occupied ports, or process cleanup without a verified identity.

## Repository contents

```text
panel-runner/
├── SKILL.md
├── agents/openai.yaml
├── assets/panel.html
├── references/lifecycle-contract.md
├── scripts/
    ├── launch_detached.py
    ├── job_supervisor.py
    ├── install_panel_tasks.ps1
    └── panel_runtime.py
└── tests/smoke_detached.py
```

The portable runtime uses only the Python standard library. The optional Windows backend uses built-in PowerShell and Task Scheduler tools.
