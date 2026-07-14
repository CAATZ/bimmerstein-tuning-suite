# BimmerStein Tuning Suite

**ECU Calibration and Data Logging**

An independent Python + PySide6 ROM calibration editor, live logger, and virtual dyno with RomRaider-compatible
formats and workflows, built first for the BMW MS41 ECU. It has its own Git history and architecture;
it is not a Git fork of RomRaider. It is read-only toward the ECU: it edits definitions and calibration
data on disk and reads live data over a transport, but it does not write/flash the ECU.

## Screenshots

<!-- TODO: editor window, logger dashboard, dyno tab -->

Screenshots are not committed yet. When captured, they will live under `resources/screenshots/`.

## Install

Windows, Python 3.11:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[gui,comms]
```

Extras (from `pyproject.toml`):

- `gui` — PySide6 + pyqtgraph + Matplotlib + numpy; required to run the editor/logger/dyno windows.
- `comms` — pyserial; required for serial-based ECU transports.
- `d2xx` (optional) — ftd2xx; only needed for FTDI D2XX-based transports.
- `dev` — pytest, pytest-qt, mypy, ruff; needed to run the test suite and health gate below.

## Run

GUI app:

```powershell
python -m ecueditor
```

Headless CLI:

```powershell
ecueditor-cli --help
```

(Both are entry points declared in `pyproject.toml`: `python -m ecueditor` runs
`ecueditor/__main__.py`; `ecueditor-cli` maps to `ecueditor.core.cli:main`.)

## Run the tests

```powershell
python -m pytest -q
```

Tests marked `ref_assets` read the local MS41 reference corpus (bins/defs from the sibling
MS41 Projects repo) and skip automatically if that corpus is absent.

One-shot health gate (ruff + mypy + pytest + core-purity check):

```powershell
python scripts/check.py
```

## Documentation

- [Implementation roadmap](docs/superpowers/plans/ROADMAP.md)
- [Architecture](docs/architecture.md)
- [Build and beta release](BUILDING.md)
- [Handoff guide](docs/handoff-guide.md)
- [Contributing](CONTRIBUTING.md)
- [Design spec](docs/superpowers/specs/2026-07-07-ecu-editor-design.md)

## License and provenance

BimmerStein Tuning Suite is free software distributed under the GNU General Public License, version 2 or (at your
option) any later version (`GPL-2.0-or-later`). See [LICENSE](LICENSE) for the complete terms.
Copyright (C) 2026 CAATZ and contributors.

Some implementation, especially the virtual-dyno physics formulas, is adapted from RomRaider. Other
parts implement compatible public formats or documented behavior without sharing RomRaider's Git
history. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for exact source references, retained
copyright notices, and the separately licensed bundled font.

The current testing line is **0.1.0 Beta 1** (`0.1.0b1`). Beta builds are non-production releases:
keep untouched BIN backups and review [RELEASE_NOTES.md](RELEASE_NOTES.md) before testing.

## Status / scope

Read-only toward the ECU: flashing, SSM, and OBD are reserved plugin slots, not implemented.
Live logging is replay- and capture-proven only so far. The pre-hardware hardening and logger-window
teardown work are complete; the first real K-line session must still validate framing and individual
multi-byte channel endianness on hardware. See [`docs/backlog.md`](docs/backlog.md) for the remaining
validation work, deferred features, and reserved scope.
