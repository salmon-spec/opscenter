param([ValidateSet('demo','semaphore')][string]$Mode = 'demo', [ValidateRange(1024,65535)][int]$Port = 18089)
$ErrorActionPreference = 'Stop'
$moduleRoot = $PSScriptRoot
$repoRoot = [IO.Path]::GetFullPath((Join-Path $moduleRoot '../..'))
$pythonExe = Join-Path $moduleRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    py -3.13 -m venv (Join-Path $moduleRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 is required for this Windows launcher.' }
}
& $pythonExe -m pip install -r (Join-Path $moduleRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$env:PYTHONPATH = (Join-Path $repoRoot 'modules') + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:AUTOMATION_MODE = $Mode
Write-Host "Automation $Mode : http://127.0.0.1:$Port"
Write-Host "Local login token file: $(Join-Path $moduleRoot '.local/admin-token') (unless AUTOMATION_TOKEN_FILE is set)"
Push-Location $repoRoot
try { & $pythonExe -m uvicorn modules.automation.main:create_app --factory --host 127.0.0.1 --port $Port --no-access-log }
finally { Pop-Location }
