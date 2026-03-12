[CmdletBinding()]
param(
    [string]$TaskName = "RunoobDailyWechat",
    [string]$Time = "08:00",
    [string]$PythonExe = "",
    [string]$EnvFile = "",
    [switch]$DryRun,
    [switch]$PrintOnly
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$Runner = Join-Path $ScriptDir "run-runoob-daily.ps1"

if (-not (Test-Path $Runner)) {
    throw "Runner script does not exist: $Runner"
}

if (-not $EnvFile) {
    $EnvFile = Join-Path $ProjectRoot ".env.local"
}

if (-not (Test-Path $EnvFile)) {
    throw "Env file does not exist: $EnvFile"
}

function Quote-Arg([string]$Value) {
    if ($Value -match '\s') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

$ArgParts = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $Runner,
    "-EnvFile", $EnvFile
)

if ($PythonExe) {
    $ArgParts += @("-PythonExe", $PythonExe)
}

if ($DryRun) {
    $ArgParts += "-DryRun"
}

$ArgumentText = ($ArgParts | ForEach-Object { Quote-Arg $_ }) -join " "
$RunCommand = "powershell.exe $ArgumentText"

$ScheduleArgs = @(
    "/Create",
    "/SC", "DAILY",
    "/TN", $TaskName,
    "/TR", $RunCommand,
    "/ST", $Time,
    "/F"
)

Write-Host "Task name: $TaskName"
Write-Host "Start time: $Time"
Write-Host "Command: $RunCommand"

if ($PrintOnly) {
    Write-Host "PrintOnly enabled. Task was not created."
    return
}

& schtasks.exe @ScheduleArgs
if ($LASTEXITCODE -ne 0) {
    throw "Task creation failed. schtasks exit code: $LASTEXITCODE"
}

Write-Host "Task created. Manual test command:"
$ManualCommand = "powershell -ExecutionPolicy Bypass -File " + (Quote-Arg $Runner) + " -EnvFile " + (Quote-Arg $EnvFile)
Write-Host $ManualCommand
