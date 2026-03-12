[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$EnvFile = "",
    [switch]$DryRun
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

if (-not $EnvFile) {
    $EnvFile = Join-Path $ProjectRoot ".env.local"
}

if (-not (Test-Path $EnvFile)) {
    throw "Env file does not exist: $EnvFile"
}

if (-not $PythonExe) {
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir "runoob-daily.log"

$PythonScript = Join-Path $ProjectRoot "runoob_daily.py"
$Args = @($PythonScript, "--env-file", $EnvFile)
if ($DryRun) {
    $Args += "--dry-run"
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$StartedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[$StartedAt] Starting run"

Push-Location $ProjectRoot
try {
    & $PythonExe @Args 2>&1 | Tee-Object -FilePath $LogFile -Append
    $ExitCode = $LASTEXITCODE
    $FinishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "[$FinishedAt] Finished with exit code $ExitCode"
    exit $ExitCode
}
finally {
    Pop-Location
}
