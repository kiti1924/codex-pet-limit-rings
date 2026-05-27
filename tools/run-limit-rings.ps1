param(
  [switch]$Status
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppSource = Join-Path $RepoRoot "tools\codex-pet-limit-rings-windows.py"
$Python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $Python -or $Status) {
  $Python = (Get-Command python.exe -ErrorAction Stop).Source
}

$Args = @($AppSource)
if ($Status) {
  $Args += "--status"
}

& $Python @Args
