# BimmerStein Tuning Suite 0.1.0 Beta 4

**ECU Calibration and Data Logging**

Released 2026-07-17

Beta 4 expands full-image editing beyond the original MS41 framing while preserving conservative
checksum and definition-matching rules. It remains beta software intended for testing and feedback.

## Highlights

- Full files can show separate **Partial BIN** and **Full BIN** tree sections, keeping the normal
  calibration tables alongside tables, parameters, and switches that exist only in a full image.
- Definition-proven linear mappings support paired partial/full RomRaider definitions such as the
  supplied MS42, MS43, and MS45 family sets. Standalone partial files retain the normal tree.
- The editor only combines framings when duplicate ROM identities and multiple concrete table
  addresses establish one consistent in-bounds offset. Ambiguous definitions remain single-section.
- Editing, undo, aliases, Save, Save As, comparison, table search, and inspection retain each
  section's own address and endian rules while sharing one working BIN buffer.
- Reload now validates a combined document against its native Full BIN definition, then resyncs
  both sections without replacing already-open table or 3D views.
- Expected typeless RomRaider override stubs are ignored quietly instead of flooding the console;
  explicitly malformed table types remain visible as warnings.

## Checksum safety

- Native automatic checksum correction remains limited to verified MS41 partial and full framings.
- A non-MS41 BIN receives no MS41 checksum correction. It saves requested edits byte-for-byte unless
  its definition or an installed plugin explicitly selects a compatible checksum manager.
- Always verify saved non-MS41 files with an appropriate family-specific tool before flashing them
  with separate software.

## Existing editor and logger capabilities

- Integrated Map Studio provides interpolation, limited-linear extrapolation, repair, smoothing,
  local history, changes review, safety reports, and transactional apply-to-ROM.
- Responsive calibration tables, 3D surfaces, shared-axis editing, comparison tools, and dockable
  workspace modes remain available.
- DS2 live polling, recording, graphs, gauges, dashboards, and virtual-dyno analysis remain part of
  the beta.
- Versioned application icons continue to prevent stale Windows shortcut artwork after upgrades.

## Safety and scope

- Always keep an untouched backup of every BIN before editing or saving it.
- BimmerStein Tuning Suite edits files on disk and reads live ECU data. It does not flash or write
  to the ECU.
- Definition XML files are supplied by the user and are not bundled with the application.
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
