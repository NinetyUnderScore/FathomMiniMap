# Fathom MiniMap

[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)](#requirements)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)](#building-from-source)
[![OCR](https://img.shields.io/badge/OCR-RapidOCR-2ea44f)](https://github.com/RapidAI/RapidOCR)

A standalone minimap overlay for **Fathom** (Roblox) that reads the in-game
coordinate readout with OCR and draws a live, zoomable map window.

No injection or memory reading, all processes are ran without ever interacting
with the Roblox game client directly.

> **Disclaimer:** This is an unofficial, fan-made tool, not affiliated with or
> published by Roblox Corporation or the developers of Fathom. Use of this
> tool has been confirmed as allowed by a Fathom developer.

---

## Table of contents

- [Features](#features)
- [Download](#download)
- [Usage](#usage)
- [Controls](#controls)
- [Building from source](#building-from-source)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)

## Features

- **Live map** - captures the coordinate readout ~7 times per second and
  draws an error corrected marked on the map with a directional arrow.
- **Limbo** - shows where limbo begins and ends.
- **Farlands** - shows where the farlands begin.
- **Floating point barriers** - optional floating point barrier rendering
  at every power of 2
- **Waypoints** - saved to
  `waypoints.json`. Waypoint names and coordinates are drawn on the map and
  can be toggled independently.
- **Pylon reading** - the waypoint editor can read a pylon's coordinate
  straight from the game's Pylon menu.
- **Compass** - an optional click-through overlay centred on the top
  edge of the game window, with a marker for the selected waypoint.

## Download

Head to the [Releases](../../releases/latest) page and download
`FathomMiniMap.exe`. It is a portable, self-contained build; **Python is not
required**.

1. Start the game.
2. Run `FathomMiniMap.exe`. The map recgonizes the game window and starts
   reading immediately.
3. Keep the game client at least **1200 px wide** so the coordinate readout
   is fully visible.

Windows may show a SmartScreen warning on first launch because the binary is
unsigned. Choose *More info → Run anyway*. If you do not trust the prebuilt
binaries and would rather compile it yourself, see the section on [building from source](#building-from-source).

## Usage

The minimap opens as a small always-on-top window. Your position updates
automatically; there is nothing to configure to get started.

The title bar contains five controls:

| Control     | What it does                                                   |
| ----------- | -------------------------------------------------------------- |
| `Waypoints` | Opens the waypoint editor (list, create, edit, delete).         |
| `Origin`    | Centres the view on the world origin and locks it there.        |
| `Player`    | Locks the view to your position (default).                      |
| `Zoom`      | Fits the view to the smallest power-of-2 square around you.     |
| `Menu ▾`    | Toggles overlays and panels (see below).                        |

The **Menu** contains:

- **Axis lines** - the x/z axes and diagonals through the origin.
- **Floating point barriers** - power-of-2 chunk borders.
- **Debug panel** - the OCR diagnostics strip at the bottom.
- **Compass** - the click-through compass overlay.
- **Waypoint names** / **Waypoint coordinates** - map label visibility.

### Waypoints

Open the editor from the `Waypoints` button. Each waypoint has a name, X/Y/Z
coordinates and a type (pylon, starred, or paper).

- **Use current position** fills X/Z from your latest OCR reading.
- **Read Pylon menu** reads the open Pylon menu in-game.
- **Save**, **New** and **Delete** manage the list.

Waypoints are stored in `waypoints.json` next to the executable (or next to
the source when running from Python). The file is created the first time a
waypoint is saved.

## Controls

| Input                    | Action                                    |
| ------------------------ | ----------------------------------------- |
| Left mouse drag          | Pan the map                               |
| Mouse wheel              | Zoom towards the cursor (window focused)  |
| Right click              | Reset zoom and re-lock onto the player    |
| Drag bottom-right corner | Resize the window                         |

## Building from source

Python is only needed to build or run from source. The released `.exe`
bundles the interpreter and all dependencies.

### Requirements

- Windows 10/11: the capture and overlay code uses Win32 APIs.
- Python 3.9 or newer.

### Run from source

```bash
git clone https://github.com/YOUR-USERNAME/fathomMiniMap.git
cd fathomMiniMap
python -m pip install -r requirements.txt
python run.py
```

### Build the executable

```bash
python -m pip install pyinstaller
python build_exe.py              # one file: dist/FathomMiniMap.exe
python build_exe.py --onedir     # folder build (faster startup, easier debugging)
python build_exe.py --console    # keep a console window for debug output
```

The build must run on Windows, since it packages `pywin32` and Win32-only
code. The resulting `dist/FathomMiniMap.exe` is self-contained and can be
attached to a GitHub release as-is.

All OCR model files are bundled by `--collect-all rapidocr_onnxruntime`, so
the executable works offline.

## Configuration

Every tunable is stored within [`config.py`](config.py), grouped by area: capture
margins, OCR settings, grid, zoom, colours and UI. The defaults are tuned for
a 1920×1080 client; the capture regions themselves are relative to the game
window, so they follow it when it is moved or resized.

If OCR reads badly on your setup, enable the **Debug panel** to see the
captured region and the processing pipeline, then adjust the margins and
thresholds in `config.py`.

Test OCR against screenshots directly:

```bash
python run.py --test screenshot.png
```

This prints the raw and parsed readings and saves a `debug_*.png` showing the
processed capture.

## Troubleshooting

**The map is not updating.**
Make sure the game window is not minimised and is at least 1200 px wide.
Check the Debug panel for a warning about the client size.

**Coordinates are read incorrectly.**
Open the Debug panel and inspect the captured region. On unusual resolutions
or UI scales, adjust `POSITION_MARGINS` / `FACING_MARGINS` in `config.py`.

**Windows flags the executable.**
The build is unsigned, so SmartScreen and some antivirus products warn. The
source is available to build yourself; use `--onedir` if a one-file build is
being quarantined.

**Waypoints do not save.**
The file is written next to the executable. Avoid running it from a
permission-protected folder such as `C:\Program Files`; a normal user folder
or a portable directory works.

## Project structure

| File                  | Responsibility                                          |
| --------------------- | ------------------------------------------------------- |
| `run.py`              | Entry point and OCR test mode                           |
| `map_window.py`       | Main window, map rendering and interaction              |
| `compass_window.py`   | Click-through compass overlay                           |
| `waypoint_window.py`  | Waypoint editor and persistence                         |
| `ocr_reader.py`       | Screen capture loop, OCR and coordinate parsing         |
| `game_window.py`      | Locates the game client and resolves capture regions    |
| `config.py`           | All tunable settings                                    |
| `ui_common.py`        | Shared UI helpers and small utilities                   |
| `build_exe.py`        | PyInstaller build script                                |

## Notes

- Screen capture requires the game window to be visible; a minimised window
  cannot be read.
- The minimap is display-only and never sends input to the game.
- Built with [RapidOCR](https://github.com/RapidAI/RapidOCR) and
  [PyInstaller](https://pyinstaller.org/).
