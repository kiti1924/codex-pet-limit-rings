$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$InstallDir = Join-Path $env:LOCALAPPDATA "CodexPetLimitRings"
$TaskName = "CodexPetLimitRings"
$AppSource = Join-Path $RepoRoot "tools\codex-pet-limit-rings-windows.py"
$RunScriptSource = Join-Path $RepoRoot "tools\run-limit-rings.ps1"

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
  Where-Object { $_.CommandLine -like "*codex-pet-limit-rings-windows.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Force $AppSource (Join-Path $InstallDir "codex-pet-limit-rings-windows.py")
Copy-Item -Force $RunScriptSource (Join-Path $InstallDir "run-limit-rings.ps1")

$Python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) {
  $Python = (Get-Command python.exe -ErrorAction Stop).Source
}

$AppPath = Join-Path $InstallDir "codex-pet-limit-rings-windows.py"
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$AppPath`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description "Draws usage-limit rings around the Codex desktop pet." | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed Codex Pet Limit Rings for Windows."
Write-Host "Install dir: $InstallDir"
Write-Host "Scheduled task: $TaskName"
