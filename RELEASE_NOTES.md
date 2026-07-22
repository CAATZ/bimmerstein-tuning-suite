# BimmerStein Tuning Suite 0.1.0 Beta 10

**ECU Calibration and Data Logging**

Released 2026-07-21

Beta 10 fixes RomRaider-definition compatibility and table-paste behavior, and adds click-again
closing for table entries. It remains beta software intended for testing and feedback.

## Windows packages

- The **PyInstaller** build is available as a Windows installer and portable ZIP.
- The **Nuitka** build is also available as a Windows installer and portable ZIP. The `Nuitka`
  filename suffix identifies the build backend.
- Both builds use the same source, version, legal notices, dependency-license inventory, bundled
  resources, and external-plugin layout.
- Exact Python, backend, and dependency versions are recorded in separate build-environment files,
  with every release artifact covered by `SHA256SUMS.txt`.

## Included changes

- RomRaider 2D definitions now keep their single inherited axis when a derived definition changes
  the axis role tag. Legacy column-oriented curves, including the MS41.1/MS41.0 heavy-throttle
  VANOS table, open with the correct shape.
- Full-table paste can overwrite compatible editable axes; ordinary value-only pastes leave axes
  unchanged.
- Main-table pastes no longer prevent Map Studio from opening. Map Studio Source and Result pastes
  now report rejected edits accurately and apply complete-grid pastes across repeated padding.
- Map Studio reports invalid source data without leaving a broken document behind.
- Clicking an already-open table or parameter in the ROM tree closes its window. Programmatic
  navigation still focuses the existing window.

## Existing capabilities

- Full files can expose separate **Partial BIN** and **Full BIN** definition sections when paired
  RomRaider definitions prove one consistent in-bounds mapping.
- Editing, undo, aliases, Save, Save As, comparison, table search, inspection, and reload preserve
  each section's address and endian rules while sharing one working BIN buffer.
- Integrated Map Studio provides interpolation, extrapolation, repair, smoothing, local history,
  changes review, safety reports, transactional apply-to-ROM, and destination-wide **Linear**,
  **Local trend (4 × 4)**, and **Global trend** extrapolation.
- DS2 live polling, recording, graphs, gauges, dashboards, and virtual-dyno analysis remain part of
  the beta.

## Safety and scope

- Always keep an untouched backup of every BIN before editing or saving it.
- BimmerStein Tuning Suite edits files on disk and reads live ECU data. It does not flash or write
  to the ECU.
- Definition XML files are supplied by the user and are not bundled with the application.
- Native automatic checksum correction remains limited to verified MS41 partial and full framings.
- Treat this beta as non-production software and independently verify every saved file.

## Known limitations

- Checksum algorithms for non-MS41 ECU families are not yet built in.
- Multi-byte live-logger channel endianness still benefits from validation against additional real
  capture sessions and ECU versions.
- Live CAL-ID remains intentionally unavailable until a labeled capture or verified DS2 command
  identifies it without guessing.
- The Windows executables are not code-signed, so Windows may display an unknown-publisher warning.
- Flashing, Subaru SSM, OBD-II/ELM327, J2534, and Bluetooth transports are not implemented.

## Useful acceptance feedback

Please include the ECU/ROM version, Windows version, display-scaling percentage, application theme,
definition-file version, affected table or parameter, and exact reproduction steps. Screenshots are
especially useful for layout or scaling findings. Do not attach proprietary or personal files unless
you intend to share them.

## License

BimmerStein Tuning Suite is distributed under `GPL-2.0-or-later`. Complete project and dependency
notices are included with both the portable build and installer.
