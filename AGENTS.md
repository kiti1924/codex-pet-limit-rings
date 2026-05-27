# Codex Pet Limit Rings Agent Notes

## Goal

This repository packages `codex-pet-limit-rings`: a native companion app that draws usage-limit rings around the current Codex pet without patching Codex. The original app is macOS-native; the Windows port uses a small Python/Tkinter overlay and Windows Scheduled Tasks.

## Primary Contract

- Keep the Codex app bundle unmodified.
- Treat `tools/codex-pet-limit-rings.swift` as the macOS app source.
- Treat `tools/codex-pet-limit-rings-windows.py` as the Windows app source.
- Treat `tools/install-limit-rings.sh` and `tools/uninstall-limit-rings.sh` as the macOS public install/uninstall path.
- Treat `tools/install-limit-rings.ps1` and `tools/uninstall-limit-rings.ps1` as the Windows public install/uninstall path.
- Treat `skills/codex-pet-limit-rings/SKILL.md` as the reusable Codex-agent workflow.
- Keep weather-pet code under `experiments/weather-pets/`; it is not the main package.

## Done When

For app changes, verify:

```bash
bash -n tools/*.sh
swiftc tools/codex-pet-limit-rings.swift -o tmp/codex-pet-limit-rings -framework AppKit -lsqlite3
tmp/codex-pet-limit-rings --preview tmp/limit-rings-preview.png --size 164
```

On Windows, verify:

```powershell
python -m pip install -r tools/requirements-windows.txt
python -m py_compile tools/codex-pet-limit-rings-windows.py
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run-limit-rings.ps1 -Status
python tools/codex-pet-limit-rings-windows.py --preview tmp/windows-rings-preview.png --size 306
```

For packaged installs, also run `tools/install-limit-rings.sh` and verify:

```bash
pgrep -fl CodexPetLimitRings
launchctl print "gui/$(id -u)/com.codex-pet.limit-rings" >/dev/null
```

For Windows packaged installs, also run `tools/install-limit-rings.ps1` and verify:

```powershell
Get-ScheduledTask -TaskName CodexPetLimitRings
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*pythonw.exe" }
```

Do not commit `tmp/`, local logs, screenshots, user Codex state, or generated private pet assets.
