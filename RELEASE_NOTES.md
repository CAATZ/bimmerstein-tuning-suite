# BimmerStein Tuning Suite 0.1.0 Beta 5

**ECU Calibration and Data Logging**

Released 2026-07-17

Beta 5 is a focused Map Studio and 3D visualization correction built on Beta 4. It remains beta
software intended for testing and feedback.

## Corrections

- Map Studio now offers **Linear to destination**, which continues the edge slope across the full
  destination grid instead of stopping after the Limited linear distance.
- **Limited linear** remains available when extrapolation must stop after a configured maximum
  number of edge intervals.
- Changing a source region, destination grid, interpolation method, boundary policy, or edge limit
  invalidates the generated preview and requires regeneration before it can be applied.
- Both 3D viewers preserve real breakpoint geometry while presenting regular whole-number axis
  ticks, such as 100-unit Load and 1000-RPM intervals where appropriate.
- Exact calibrated breakpoint values remain available through table cells and selection readouts;
  only the displayed 3D scale labels are coarsened.

## Existing Beta 4 capabilities

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
