$ErrorActionPreference = "Stop"

$TaskName = "CodexPetLimitRings"
$InstallDir = Join-Path $env:LOCALAPPDATA "CodexPetLimitRings"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
  Where-Object { $_.CommandLine -like "*codex-pet-limit-rings-windows.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

if (Test-Path $InstallDir) {
  Remove-Item -Recurse -Force $InstallDir
}

Write-Host "Uninstalled Codex Pet Limit Rings for Windows."
