# BimmerStein Tuning Suite 0.1.0 Beta 8

**ECU Calibration and Data Logging**

Released 2026-07-18

Beta 8 corrects Map Studio's horizontal-axis typography so it follows the same density and zoom
scale as the main calibration table, its cells, and its vertical axis. It retains the verified
Nuitka and PyInstaller Windows packages and remains beta software intended for testing and feedback.

## Windows packages

- The recommended test package is clearly labeled **Nuitka** and is available as both a Windows
  installer and portable ZIP.
- The established **PyInstaller** installer and portable ZIP remain available in this transitional
  beta so machine-specific behavior can be compared without changing application code.
- Both builds use the same source, version, legal notices, dependency-license inventory, bundled
  resources, and external-plugin layout.
- Exact Python, backend, and dependency versions are recorded in separate build-environment files,
  with every release artifact covered by `SHA256SUMS.txt`.

## Included correction

- Map Studio's horizontal breakpoint labels now use the table's explicit scaled numeric font rather
  than Qt's larger application-level header font. The axis stays aligned with the main table in
  Normal and Compact density and continues to scale correctly with Studio zoom and **Fit**.
- Header-item metrics are synchronized with the active table font so section geometry follows the
  rendered labels instead of retaining an unrelated application-font measurement.
- A rendered-pixel regression now verifies the actual themed horizontal-axis output, in addition to
  checking the stored Qt font and style-option metrics.

## Existing capabilities

- Full files can expose separate **Partial BIN** and **Full BIN** definition sections when paired
  RomRaider definitions prove one consistent in-bounds mapping.
- Editing, undo, aliases, Save, Save As, comparison, table search, inspection, and reload preserve
  each section's address and endian rules while sharing one working BIN buffer.
- Integrated Map Studio provides interpolation, extrapolation, repair, smoothing, local history,
  changes review, safety reports, and transactional apply-to-ROM.
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
