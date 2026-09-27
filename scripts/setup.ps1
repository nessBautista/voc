# Windows host setup. Run from PowerShell; use Git Bash for daily just commands.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}
function Install-Package([string]$Id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Install/update App Installer from Microsoft Store (winget), then rerun this script.'
    }
    Write-Host "Installing $Id. Follow any installer/administrator prompts."
    & winget install --id $Id --exact --source winget
    if ($LASTEXITCODE -ne 0) {
        throw "Installation of $Id did not complete. Finish any requested restart, then rerun this script."
    }
    Refresh-Path
}

if (-not (Get-Command just -ErrorAction SilentlyContinue)) { Install-Package 'Casey.Just' }
$gitBashCandidates = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($gitCommand) {
    $gitBashCandidates += Join-Path (Split-Path (Split-Path $gitCommand.Source -Parent) -Parent) 'bin\bash.exe'
}
if (-not ($gitBashCandidates | Where-Object { Test-Path $_ })) { Install-Package 'Git.Git' }
$desktopCandidates = @(
    "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
    "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
)
$desktop = $desktopCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $desktop) {
    Install-Package 'Docker.DockerDesktop'
    $desktop = $desktopCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $desktop) { throw 'Open Docker Desktop manually, then rerun this script.' }
$env:Path = (Join-Path (Split-Path $desktop -Parent) 'resources\bin') + ';' + $env:Path
Start-Process $desktop
Write-Host 'Finish Docker Desktop setup using Linux containers/WSL 2. A Windows restart may be required.'
Write-Host 'Waiting up to three minutes; rerun this script after any required restart.'
$ready = $false
for ($attempt = 0; $attempt -lt 36; $attempt++) {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        try {
            & docker info *> $null
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        } catch { # Windows PowerShell can throw while the daemon is starting.
        }
    }
    Start-Sleep -Seconds 5
}
if (-not $ready) { throw 'Docker is not ready. Finish WSL/Docker setup, restart if requested, and rerun this script.' }
& docker compose version
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required; finish Docker Desktop installation.' }
$osType = & docker info --format '{{.OSType}}'
if ($osType.Trim() -ne 'linux') { throw 'Switch Docker Desktop to Linux containers and rerun.' }
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
