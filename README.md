# codex-pet-limit-rings

Codex pets are tiny ambient companions for the work happening in Codex. This project adds one more layer to that idea: your pet can quietly show how much Codex capacity you have left, without turning the app into a dashboard.

The original experience is a small macOS companion app. This fork also includes a Windows companion that watches where the Codex pet is, draws two polished rings around it, and keeps those rings attached to the pet as it moves. It does not patch Codex, change pet art, or modify the Codex app bundle.

It works with whatever Codex pet you like. Built-in pet, custom pet, tiny dog, robot, weather daemon, or anything else: the app does not care. It only follows the pet window that Codex is already showing.

![Codex Pet Limit Rings around a Codex pet](docs/assets/codex-pet-limit-rings-screenshot.png)

## What You See

The rings are designed to be glanceable:

- The outer ring shows the short-window limit remaining.
- The inner ring shows the weekly limit remaining.
- Color moves from calm green/blue to amber and red as capacity gets low.
- Hovering over the pet or rings shows the exact percentages at the current ring endpoints.
- A small menu-bar icon lets you hide the rings, refresh data, or quit.

When the Codex pet is closed, the rings disappear. When the pet comes back, they come back too. On multi-display setups, the rings stay with the pet instead of jumping to whichever screen is focused.

Because the rings are drawn in a separate transparent overlay, they do not need pet-specific sprites, masks, metadata, or configuration. Change pets in Codex and the rings follow the new one automatically.

## Windows Fork

This fork adds a Windows-native companion path while keeping the same companion boundary:

- `tools/codex-pet-limit-rings-windows.py` reads the same local Codex state and usage files as the macOS app.
- `tools/install-limit-rings.ps1` installs the companion into `%LOCALAPPDATA%\CodexPetLimitRings`.
- A Windows Scheduled Task named `CodexPetLimitRings` starts the companion at login.
- The overlay prefers the live Codex pet window on multi-display desktops, then applies Codex's persisted `mascot` offset so the rings stay anchored to the visible pet.
- The Windows renderer uses Pillow to draw high-resolution rings, endpoint percentage badges, and model-limit bubbles, then displays them through a per-pixel-alpha layered window. This avoids color-key fringes, purple transparent padding, and constant redraw flicker.
- If Pillow is not installed, the app can fall back to a simpler Tkinter Canvas renderer, but the polished Windows visual path expects `tools/requirements-windows.txt`.

## Why It Works This Way

The important design choice is the companion boundary. A menu item inside Codex itself would mean patching Electron app files and redoing that patch after app updates. That is brittle and hard to open source.

`codex-pet-limit-rings` stays outside the Codex app. It reads local Codex state, asks ChatGPT for live usage data using the local Codex/ChatGPT token, and renders its own transparent always-on-top window around the pet. The result is reversible, inspectable, and easy for another Codex agent to install or modify.

Pet wakeups are handled by a lightweight filesystem watcher on Codex's local global-state file, with a slow fallback timer as a safety net. That lets the rings snap back when the pet is re-enabled without constantly polling for position changes.

## Quick Start

Install the rings as a login item:

```bash
tools/install-limit-rings.sh
```

You should see a small rings icon in the macOS menu bar. Use that menu to toggle `Show Rings`, refresh the latest usage data, or quit.

On Windows, install the polished renderer dependency and register the Scheduled Task:

```powershell
python -m pip install -r tools\requirements-windows.txt
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install-limit-rings.ps1
```

Verify the Windows companion:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\run-limit-rings.ps1 -Status
Get-ScheduledTask -TaskName CodexPetLimitRings
```

Then use any Codex pet normally. No pet setup step is required.

Run a development build without installing the login item:

```bash
tools/run-limit-rings.sh
```

On Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\run-limit-rings.ps1
```

Uninstall everything the installer adds:

```bash
tools/uninstall-limit-rings.sh
```

On Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\uninstall-limit-rings.ps1
```

## Give This Repo To Codex

This repository is structured so a Codex agent can pick it up from a GitHub link.

Ask the agent:

```text
Use the bundled codex-pet-limit-rings skill from this repository. Install the rings companion for my Codex pet, verify the LaunchAgent is running, and confirm the rings stay anchored to the pet.
```

The agent should read:

- `AGENTS.md` for the project contract.
- `skills/codex-pet-limit-rings/SKILL.md` for the install, debug, and validation workflow.
- `docs/limit-rings.md` for the data and rendering model.

To install the bundled skill into local Codex:

```bash
tools/install-codex-skill.sh
```

## Data And Privacy

The app reads only local Codex files and one ChatGPT usage endpoint:

- `~/.codex/.codex-global-state.json` tells it whether the pet is open and where it is.
- `~/.codex/auth.json` provides the local bearer token used to read live usage from ChatGPT.
- `~/.codex/logs_2.sqlite` is used as a cached fallback if live usage is unavailable.

It does not require an OpenAI API key. It does not send pet images, screenshots, prompts, or repo contents anywhere.

## Project Shape

```text
tools/
  codex-pet-limit-rings.swift      native macOS companion app
  codex-pet-limit-rings-windows.py Windows companion overlay
  install-limit-rings.sh           build, install, and start at login
  install-limit-rings.ps1          install and start the Windows Scheduled Task
  uninstall-limit-rings.sh         remove the app and login item
  uninstall-limit-rings.ps1        remove the Windows Scheduled Task and app files
  run-limit-rings.sh               development launch
  run-limit-rings.ps1              Windows development launch/status
  build-limit-rings.sh             app bundle builder
  install-codex-skill.sh           copy the bundled skill into ~/.codex/skills
  requirements-windows.txt         Windows polished renderer dependency

skills/codex-pet-limit-rings/
  SKILL.md                         Codex-agent workflow for this project

docs/
  limit-rings.md                   implementation contract and data flow

experiments/weather-pets/
  earlier weather-pet renderer     kept as a separate experiment
```

## Development

Build the app:

```bash
tools/build-limit-rings.sh
```

Render a static preview PNG:

```bash
swiftc tools/codex-pet-limit-rings.swift -o tmp/codex-pet-limit-rings -framework AppKit -lsqlite3
tmp/codex-pet-limit-rings --preview tmp/limit-rings-preview.png --size 164
```

Validate the shell scripts:

```bash
bash -n tools/*.sh
```

Validate the Windows companion:

```powershell
python -m pip install -r tools\requirements-windows.txt
python -m py_compile tools\codex-pet-limit-rings-windows.py
powershell -NoProfile -ExecutionPolicy Bypass -File tools\run-limit-rings.ps1 -Status
python tools\codex-pet-limit-rings-windows.py --preview tmp\windows-rings-preview.png --size 306
```

## Experiments

The original exploration included a Python renderer for weather-mutated Codex pets. That work now lives under `experiments/weather-pets/` so the public repo can stay focused on limit rings while preserving the larger idea: Codex pets can become ambient interfaces for state, context, and mood.

## License

MIT. See `LICENSE`.
