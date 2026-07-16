# BimmerStein Tuning Suite 0.1.0 Beta 3

**ECU Calibration and Data Logging**

Released 2026-07-15

Beta 3 is a branding and Windows shortcut hotfix built on the editor, Map Studio, reliability, and
presentation improvements introduced in Beta 2. It remains beta software and is intended for
testing and feedback.

## Highlights

- A new circular red-and-black BS identity is used consistently by the application, title bars,
  Windows executable, installer, shortcuts, and user manual.
- Versioned installed icon files and explicit shortcut icon targets prevent Windows Explorer from
  retaining obsolete artwork after an in-place upgrade.
- Integrated Map Studio for curves and two-axis maps, including padded-region detection, expansion
  to the opening table's destination grid, Linear/Bilinear and shape-preserving PCHIP interpolation,
  and one bounded Limited-linear extrapolation method.
- Editable Source and Result workspaces with copy, paste, adjustments, anomaly detection, repair,
  smoothing, local undo/redo, Changes review, safety reports, and transactional apply-to-ROM.
- Responsive table sizing and coarse axis labels across the main editor and Map Studio, with
  improved selection, zoom/fit stability, edited-cell indicators, and Rainbow table coloring.
- Harmonized 3D surfaces, axis ordering and flip controls, natural orbit/pan/zoom interaction, live
  selected-cell readouts, and safer visualization teardown.
- Correct multi-ROM targeting for close, Save As, compare, and reload-from-disk workflows.
- High-DPI vector and multi-resolution Windows icon assets keep the product mark sharp from compact
  title bars through large shortcut views.

## Reliability and safety

- ROM save and reload operations are transactional and preserve the active document on failure.
- Map Studio validates destination shape and axes before applying and rolls back bytes, cells,
  aliases, and history if an apply callback fails.
- Logger, protocol, transport, checksum, definition-importer, and analysis-plugin boundaries now
  isolate malformed plugins and cleanup failures without swallowing user interrupts.
- Runtime resource discovery works from source, installed wheels, and frozen Windows builds.
- Release packaging explicitly includes the manual, fonts, icons, plugins, licenses, and required
  runtime resources while excluding development-only and private files.

## Safety and scope

- Always keep an untouched backup of every BIN before editing or saving it.
- BimmerStein Tuning Suite edits files on disk and reads live ECU data. It does not flash or write
  to the ECU.
- Definition XML files are supplied by the user and are not bundled with the application.
- Treat this beta as non-production software and verify saved files before using them elsewhere.

## Known limitations

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
