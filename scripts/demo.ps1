# Local web demo for Windows PowerShell 5.1+ and PowerShell 7.
param(
    [Parameter(Position = 0)]
    [ValidateSet('configure', 'setup', 'sync', 'up', 'stop', 'status', 'logs')]
    [string]$Action = 'up'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
$savedEnvironment = @{}
try {
    # The dedicated private file wins over stale settings from another session.
    foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'VOC_DEMO_*' -or $_.Name -like 'COMPOSE_*' })) {
        $savedEnvironment[$item.Name] = $item.Value
        Remove-Item "Env:$($item.Name)"
    }
    $privateFile = Join-Path $repo 'config\demo.local.env'
    if ($Action -eq 'configure') {
        if (-not (Test-Path -LiteralPath $privateFile)) {
            Copy-Item -LiteralPath 'config/demo.local.env.example' -Destination $privateFile
        }
        Start-Process notepad.exe -ArgumentList ('"' + $privateFile + '"')
        Write-Host 'Completa y guarda las llaves en config/demo.local.env. Luego ejecuta: .\scripts\demo.ps1 setup'
        return
    }
    if (-not (Test-Path -LiteralPath $privateFile)) {
        throw 'Primero ejecuta: .\scripts\demo.ps1 configure'
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Instala Docker Desktop, inicialo con contenedores Linux y abre otra terminal.'
    }
    & docker compose version
    if ($LASTEXITCODE -ne 0) { throw 'Se requiere Docker Compose v2 con soporte de --wait.' }
    $osType = & docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or "$osType".Trim() -ne 'linux') {
        throw 'Inicia Docker Desktop con contenedores Linux y vuelve a ejecutar el comando.'
    }
    $script:ComposeArgs = @('compose', '--project-directory', $repo, '--env-file', $privateFile,
        '-p', 'voc-local-demo', '-f', 'docker/app/demo/compose.yaml')
    function Invoke-Compose {
        & docker @script:ComposeArgs @args
        if ($LASTEXITCODE -ne 0) {
            throw 'El comando Docker fallo. Revisa el mensaje anterior; los datos existentes se conservan.'
        }
    }
    switch ($Action) {
        'setup' {
            Invoke-Compose build app
            Invoke-Compose run --rm --no-deps prepare sync
            Write-Host 'Preparacion completa. Ejecuta: .\scripts\demo.ps1 up'
        }
        'sync' { Invoke-Compose run --rm --no-deps prepare sync }
        'up' {
            # Local validation only; neither sync nor AWS login is run at startup.
            Invoke-Compose run --rm --no-deps prepare verify
            $check = "import sys; from voc_app.settings import Settings; s=Settings.from_env(); sys.exit(0 if s.llm_api_key.strip() and not s.llm_api_key.startswith('op://') else 'Completa VOC_DEMO_OPENROUTER_API_KEY en config/demo.local.env')"
            Invoke-Compose run --rm --no-deps --entrypoint python app -c $check
            Invoke-Compose up --detach --wait --wait-timeout 180 app
            $binding = Invoke-Compose port app 8000
            $port = ("$binding".Trim() -split ':')[-1]
            $url = "http://localhost:$port"
            Write-Host "Chat: $url/  |  Dashboard: $url/dashboard/"
            Start-Process "$url/"
        }
        'stop' { Invoke-Compose down }
        'status' { Invoke-Compose ps -a }
        'logs' { Invoke-Compose logs --tail 80 app }
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) { Set-Item "Env:$name" $savedEnvironment[$name] }
    Pop-Location
}
