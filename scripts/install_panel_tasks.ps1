[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$ArtifactDirectory,

    [Parameter(Mandatory = $true)]
    [string]$JobLauncher,

    [Parameter(Mandatory = $true)]
    [string]$Python,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{2,80}$')]
    [string]$TaskPrefix,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedCommandSubstring,

    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [switch]$NoPanel,
    [switch]$InstallOnly,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Resolve-ExistingPath([string]$Path, [string]$Label, [switch]$Leaf) {
    $resolved = Resolve-Path -LiteralPath $Path -ErrorAction Stop
    $value = [IO.Path]::GetFullPath($resolved.Path)
    if ($Leaf -and -not (Test-Path -LiteralPath $value -PathType Leaf)) {
        throw "$Label is not a file: $value"
    }
    if (-not $Leaf -and -not (Test-Path -LiteralPath $value -PathType Container)) {
        throw "$Label is not a directory: $value"
    }
    return $value
}

function Assert-Within([string]$Candidate, [string]$Root, [string]$Label) {
    $rootPrefix = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $Candidate.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain inside the project root: $Candidate"
    }
}

function Quote-Argument([string]$Value) {
    if ($Value.Contains('"')) {
        throw "Double quotes are not supported in Scheduled Task arguments: $Value"
    }
    return '"' + $Value + '"'
}

function Write-Utf8Json([string]$Path, [object]$Value) {
    $json = $Value | ConvertTo-Json -Depth 20
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $encoding)
}

$project = Resolve-ExistingPath -Path $ProjectRoot -Label 'ProjectRoot'
$artifact = Resolve-ExistingPath -Path $ArtifactDirectory -Label 'ArtifactDirectory'
$launcher = Resolve-ExistingPath -Path $JobLauncher -Label 'JobLauncher' -Leaf
$pythonPath = Resolve-ExistingPath -Path $Python -Label 'Python' -Leaf
Assert-Within -Candidate $artifact -Root $project -Label 'ArtifactDirectory'
Assert-Within -Candidate $launcher -Root $project -Label 'JobLauncher'

if ([string]::IsNullOrWhiteSpace($ExpectedCommandSubstring)) {
    throw 'ExpectedCommandSubstring is required for verified PID cleanup.'
}

$scriptRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$skillRoot = [IO.Path]::GetFullPath((Split-Path -Parent $scriptRoot))
$runtime = Resolve-ExistingPath -Path (Join-Path $scriptRoot 'panel_runtime.py') -Label 'panel_runtime.py' -Leaf
$template = Resolve-ExistingPath -Path (Join-Path $skillRoot 'assets\panel.html') -Label 'panel.html' -Leaf
$manifestPath = Join-Path $artifact 'panel_manifest.json'
$runtimePath = Join-Path $artifact 'panel_runtime.json'
$staticOutput = Join-Path $artifact 'panel.final.html'
$jobTask = "${TaskPrefix}_Job"
$lifecycleTask = if ($NoPanel) { "${TaskPrefix}_Watcher" } else { "${TaskPrefix}_Panel" }
$mode = if ($NoPanel) { 'json_only' } else { 'live_panel' }
$url = if ($NoPanel) { $null } else { "http://127.0.0.1:$Port" }

$existingTasks = @()
foreach ($taskName in @($jobTask, $lifecycleTask)) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $existingTasks += $taskName
    }
}

$plan = [ordered]@{
    schema_version = 'panel_task_manifest_v1'
    planned_at = (Get-Date).ToString('o')
    mode = $mode
    project_root = $project
    artifact = $artifact
    job_launcher = $launcher
    python = $pythonPath
    expected_command_substring = $ExpectedCommandSubstring
    job_task = $jobTask
    lifecycle_task = $lifecycleTask
    url = $url
    json_files = @(
        (Join-Path $artifact 'runner.json'),
        (Join-Path $artifact 'progress.json'),
        (Join-Path $artifact 'metrics.json')
    )
    runtime_state = $runtimePath
    static_output = if ($NoPanel) { $null } else { $staticOutput }
    hidden = $true
    run_level = 'Limited'
    trigger = $null
    existing_task_collisions = $existingTasks
    dry_run = [bool]$DryRun
}

if ($DryRun) {
    $plan | ConvertTo-Json -Depth 20
    exit 0
}
if ($existingTasks.Count -gt 0) {
    throw "Scheduled Task name collision; choose a new TaskPrefix after inspecting: $($existingTasks -join ', ')"
}

$powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$jobArguments = @(
    '-NoProfile',
    '-NonInteractive',
    '-WindowStyle',
    'Hidden',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    $launcher
) | ForEach-Object { Quote-Argument $_ }
$jobAction = New-ScheduledTaskAction -Execute $powerShell `
    -Argument ($jobArguments -join ' ') -WorkingDirectory $project

$runtimeArguments = @(
    '-B',
    '-u',
    $runtime,
    '--artifact',
    $artifact,
    '--template',
    $template,
    '--runtime-state',
    $runtimePath,
    '--job-task-name',
    $jobTask,
    '--lifecycle-task-name',
    $lifecycleTask,
    '--expected-command-substring',
    $ExpectedCommandSubstring
)
if ($NoPanel) {
    $runtimeArguments += @('--headless', '--skip-static')
} else {
    $runtimeArguments += @('--host', '127.0.0.1', '--port', [string]$Port, '--static-output', $staticOutput)
}
$lifecycleAction = New-ScheduledTaskAction -Execute $pythonPath `
    -Argument (($runtimeArguments | ForEach-Object { Quote-Argument ([string]$_) }) -join ' ') `
    -WorkingDirectory $project

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$jobSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden
$lifecycleSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden

$registered = @()
try {
    Register-ScheduledTask -TaskName $jobTask -Action $jobAction `
        -Principal $principal -Settings $jobSettings `
        -Description "Independent project job for $project" | Out-Null
    $registered += $jobTask
    Register-ScheduledTask -TaskName $lifecycleTask -Action $lifecycleAction `
        -Principal $principal -Settings $lifecycleSettings `
        -Description "Panel lifecycle watcher for $artifact" | Out-Null
    $registered += $lifecycleTask

    $plan.dry_run = $false
    $plan.registered_at = (Get-Date).ToString('o')
    $plan.started = -not $InstallOnly
    Write-Utf8Json -Path $manifestPath -Value $plan

    if (-not $InstallOnly) {
        Start-ScheduledTask -TaskName $lifecycleTask
        if (-not $NoPanel) {
            $healthy = $false
            for ($attempt = 1; $attempt -le 40; $attempt++) {
                try {
                    $response = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 2
                    if ($response.StatusCode -eq 200) {
                        $healthy = $true
                        break
                    }
                } catch {
                    Start-Sleep -Milliseconds 500
                }
            }
            if (-not $healthy) {
                Stop-ScheduledTask -TaskName $lifecycleTask -ErrorAction SilentlyContinue
                Disable-ScheduledTask -TaskName $lifecycleTask -ErrorAction SilentlyContinue | Out-Null
                throw "Panel health check failed; job was not started. Inspect $runtimePath"
            }
        }
        Start-ScheduledTask -TaskName $jobTask
    }
} catch {
    foreach ($taskName in $registered) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    throw
}

$plan | ConvertTo-Json -Depth 20
