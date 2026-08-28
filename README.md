# Panel Runner for Codex

[中文](#中文说明) · [English](#english)

## 中文说明

`panel-runner` 是一个面向 Windows 长时任务的 Codex Skill。当用户在项目运行或进度监控语境中提到 **panel** 时，它会让任务独立于 Codex 运行，并提供只读实时面板。

它也适合希望任务启动后关闭 Codex 的用户：在确认后台任务与监控均已稳定运行后，项目仍会独立继续执行。

### 主要功能

- 使用隐藏、Limited、无触发器的 Windows 计划任务运行项目；关闭 Codex 后任务仍可继续。
- 实时读取 `runner.json`、`progress.json` 和 `metrics.json`，面板不会反向控制任务。
- 任务结束后，将最终状态保存为可离线打开的静态 `panel.final.html`。
- 按精确任务名、PID 和命令行身份安全停止后台任务及面板，避免误杀其他进程。
- 用户明确说“不要面板”时，切换到 JSON-only 模式：不开端口、不启动网页，仅监听 JSON 并负责终态清理。

### 安装

将本仓库复制到 Codex 的个人技能目录：

```text
%USERPROFILE%\.codex\skills\panel-runner
```

然后在新的 Codex 任务中使用。技能默认允许自动触发，也可以显式输入 `$panel-runner`。

### 使用示例

```text
用 panel 运行这个训练项目，并实时显示进度。
```

```text
用 $panel-runner 独立运行这个任务，但不要面板，直接读取 JSON 告诉我进度。
```

### 运行约定

项目应以 UTF-8 原子写入状态 JSON，并在成功、失败、取消或用户停止时写入明确终态。执行注册或启动前，Codex 会先解析项目目录、解释器、启动脚本、artifact 目录和进程身份，并运行 DryRun。

该技能不会因论文插图中的 “panel” 或普通 UI 面板讨论而触发。

## English

`panel-runner` is a Codex Skill for long-running Windows jobs. When **panel** is mentioned in a project execution or progress-monitoring context, it runs the job independently of Codex and exposes a read-only live dashboard.

It is also useful for users who want to close Codex after a job starts: once the detached job and monitor are verified healthy, the project continues running independently.

### Features

- Runs the project through hidden, Limited, triggerless Windows Scheduled Tasks, so it can continue after Codex closes.
- Reads `runner.json`, `progress.json`, and `metrics.json` in real time without sending control commands back to the job.
- Freezes the final state into an offline `panel.final.html` when the job reaches a terminal state.
- Stops the job and panel using exact task names plus PID and command-line identity checks.
- Supports an explicit JSON-only mode: no HTTP port or web panel, only headless JSON monitoring and terminal cleanup.

### Installation

Copy this repository to the personal Codex skills directory:

```text
%USERPROFILE%\.codex\skills\panel-runner
```

Use it from a new Codex task. Automatic invocation is enabled, or invoke it explicitly with `$panel-runner`.

### Examples

```text
Run this training project with a panel and show live progress.
```

```text
Use $panel-runner to run this independently, but do not start a panel; report progress from the JSON files.
```

### Runtime contract

The project should atomically write UTF-8 status JSON and publish a clear terminal state on success, failure, cancellation, or user stop. Before registration or launch, Codex resolves the project root, interpreter, launcher, artifact directory, and process identity, then performs a DryRun.

The skill does not activate for scientific figure panels or ordinary UI panel discussions.

## Repository contents

```text
panel-runner/
├── SKILL.md
├── agents/openai.yaml
├── assets/panel.html
├── references/lifecycle-contract.md
└── scripts/
    ├── install_panel_tasks.ps1
    └── panel_runtime.py
```

Windows only. The runtime uses Python's standard library and built-in Windows task/process tools.
