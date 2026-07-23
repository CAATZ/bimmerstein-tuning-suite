# BimmerStein Tuning Suite 0.1.0 Beta 11

**ECU Calibration and Data Logging**

Released 2026-07-22

Beta 11 adds native, definition-aware MAF scaling and a managed transfer-function catalog. It
remains beta software intended for testing and feedback.

## Windows packages

- The **PyInstaller** build is available as a Windows installer and portable ZIP.
- The **Nuitka** build is also available as a Windows installer and portable ZIP. The `Nuitka`
  filename suffix identifies the build backend.
- Both builds use the same source, version, legal notices, dependency-license inventory, bundled
  resources, and external-plugin layout.
- Exact Python, backend, and dependency versions are recorded in separate build-environment files,
  with every release artifact covered by `SHA256SUMS.txt`.

## Included changes

- **MAF Scale** opens directly from recognized MAF tables. Any other editable numeric 256-cell
  table can be used after an explicit manual-destination confirmation.
- Source data can come from the opening table or the embedded MAF catalog. Source, Result, and
  Changes tabs use the destination table's actual shape and voltage axes.
- The **MAF Transfer Functions** manager can edit names, default inside diameters, and complete
  256-point curves, or add and delete user records. RomRaider table copy/paste and local undo/redo
  use the same shortcuts as Map Studio.
- Inches are the default diameter unit. Catalog defaults fill both source and target diameters,
  and the arrow controls change them in 0.25-inch steps.
- MS41, MS43, and custom electrical models are available. Negative output is floored to zero by
  default, while advanced users can disable that policy explicitly.
- Preview and Apply use the active ECU definition's scaling, storage range, shape, and quantization.
  Apply is atomic and undoable; stale previews are blocked.
- The scaler checks a definition-backed 1024/2048 MAF-mode switch when available, but never changes
  it automatically. Missing mode information is reported rather than guessed.
- Table fitting now preserves the deterministic 100% layout when it fits and hides the temporary
  sizing pass to avoid visible expansion flicker.

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

- MAF scaling currently requires an editable numeric destination containing exactly 256 cells.
- Unknown or uncertain catalog tube diameters must be verified against the actual installation.
- MAF mode cannot be verified when the loaded ECU definition does not expose the corresponding
  switch.
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
