# Third-party notices and source provenance

BimmerStein Tuning Suite has an independent Git history and a Python/PySide6 architecture. It is not a Git fork of
RomRaider. It does, however, contain credited adaptations and compatible implementations informed by
RomRaider and by the project-local MS41 Flasher reference.

Original BimmerStein Tuning Suite code is copyright (C) 2026 CAATZ and contributors.

## RomRaider

- Project: RomRaider
- Source: <https://github.com/RomRaider/RomRaider>
- Reference commit used by this project: `dafe0c36c1a68efadbeedb2825f3855463fdbc35`
- Copyright: `Copyright (C) 2006-2022 RomRaider.com` (from the referenced source headers)
- License: GNU General Public License, version 2 or (at your option) any later version
  (`GPL-2.0-or-later`)

`ecueditor/core/dyno/physics.py` translates and adapts the humid-air-density, vehicle-power, drag,
rolling-resistance, and related virtual-dyno formulas and constants from RomRaider's
`DynoControlPanel.java`. Other dyno workflow behavior and compatible file formats were implemented
with RomRaider as a behavioral reference.

The definition importer, logger protocol support, clipboard formats, and editor workflows also name
RomRaider where they intentionally implement compatible formats or documented behavior. Those
compatibility references do not imply that every such file is a source translation.

Because the distributed application includes the adapted dyno material, BimmerStein Tuning Suite as a whole is
distributed under `GPL-2.0-or-later`. The complete GPL version 2 text is in `LICENSE`.

BimmerStein Tuning Suite is not sponsored, endorsed by, or affiliated with the RomRaider project.

## MS41 Projects / Flasher

The following files contain code ported from the sibling `MS41 Projects/Flasher` reference project:

- `ecueditor/core/checksum/builtins/ms41.py` adapts `Flasher/checksum.py`.
- `ecueditor/core/comms/transport/halfduplex.py` adapts the K-line echo handling from
  `Flasher/ds2.py`.

The Flasher reference and BimmerStein Tuning Suite are both authored by CAATZ. Flasher is a separate project
licensed under the MIT License, so its original notice is retained here:

> MIT License
>
> Copyright (c) 2026 CAATZ
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute,
> sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
> NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
> OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## ECU Map Studio

`ecueditor/core/mapstudio/` and `ecueditor/ui/mapstudio/` adapt numerical algorithms and review
workflows from the separately developed ECU Map Studio project. Standalone clipboard, project-file,
nearest-neighbor, comparison/merge, and selection-math features were not incorporated.

- Project: ECU Map Studio
- Copyright: `Copyright (c) 2026 CAATZ`
- License: MIT License

ECU Map Studio carries the same complete MIT notice retained immediately above for the MS41 Flasher
reference. That copyright and permission notice applies independently to the adapted ECU Map Studio
material.

## JetBrains Mono

The JetBrains Mono font files under `resources/fonts/` are licensed under the SIL Open Font License
1.1. Their complete license and copyright notice are retained in `resources/fonts/OFL.txt`.

## Lucide icons

The Lucide icon subset under `resources/icons/` is licensed under the ISC License. Its complete
copyright and license notice are retained in `resources/icons/LICENSE-lucide.txt`.

## Python and frozen-application dependencies

Python packages installed through `pyproject.toml` retain their own licenses. Frozen beta builds bundle
the exact dependency license files collected from the build environment under `DEPENDENCY_LICENSES/`,
along with an indexed version and license inventory. The source release includes the build scripts that
generate that inventory and the frozen application.
