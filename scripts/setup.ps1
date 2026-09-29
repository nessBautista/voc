# Windows project setup. Host tools must already be installed. Run from PowerShell; use Git Bash for daily just commands.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

# Prerequisites are installed separately. Preserve the user's runtime and context.
foreach ($tool in @('git', 'just', 'docker')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Missing prerequisite: $tool. Install it separately and reopen PowerShell before rerunning setup."
    }
}
$gitBashCandidates = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($gitCommand) {
    $gitBashCandidates += Join-Path (Split-Path (Split-Path $gitCommand.Source -Parent) -Parent) 'bin\bash.exe'
}
if (-not ($gitBashCandidates | Where-Object { Test-Path $_ })) {
    throw 'Git Bash is required for daily just commands. Complete Git for Windows setup separately, then rerun.'
}
$ready = $false
try {
    & docker info *> $null
    $ready = $LASTEXITCODE -eq 0
} catch { # Windows PowerShell can throw when the daemon is unavailable.
}
if (-not $ready) {
    throw 'Container runtime is not ready. Start your existing runtime with Linux containers, then rerun setup.'
}
& docker compose version
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required. Complete runtime setup separately, then rerun.' }
$osType = & docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $osType.Trim() -ne 'linux') {
    throw 'Use Linux containers before running setup.'
}
# Docker may download this Python image; no host software is installed.
$repo = (Get-Location).Path
& docker run --rm --mount "type=bind,source=$repo,target=/workspace" `
    --workdir /workspace python:3.12-slim-bookworm `
    python scripts/configure.py --member $env:USERNAME
if ($LASTEXITCODE -ne 0) { throw 'Configuration failed; review the preceding Docker error and rerun.' }
Start-Process notepad.exe -ArgumentList ('"' + (Join-Path $repo 'config\aws.local.env') + '"')
Write-Host 'Save your AWS keys in the opened file. Then open Git Bash in this repository and run: just up'
Write-Host 'If just is not found, close/reopen Git Bash so it reloads the installed PATH.'

# Open the shell used by Justfile recipes, already positioned in this checkout.
$bash = $gitBashCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($bash) {
    $gitBashApp = Join-Path (Split-Path (Split-Path $bash -Parent) -Parent) 'git-bash.exe'
    if (Test-Path $gitBashApp) {
        Start-Process $gitBashApp -ArgumentList ('--cd="' + $repo + '"')
    }
}
