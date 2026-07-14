# BimmerStein Tuning Suite 0.1.0 Beta 1

**ECU Calibration and Data Logging**

This is a public testing release. It is intended to validate the editor, definition compatibility,
table workflows, themes, window behavior, live logging, and virtual dyno across more Windows systems
and MS41 files before the first stable release.

## Safety and scope

- Always keep an untouched backup of every BIN before editing or saving it.
- BimmerStein Tuning Suite edits files on disk and reads live ECU data. It does not flash or write
  to the ECU.
- Definition XML files are supplied by the user and are not bundled with the application.
- Treat this beta as non-production software and verify saved files before using them elsewhere.

## Known limitations

- Multi-byte live-logger channel endianness still benefits from validation against additional real
  capture sessions and ECU versions.
- The Windows executables are not code-signed, so Windows may display an unknown-publisher warning.
- Flashing, Subaru SSM, OBD-II/ELM327, J2534, and Bluetooth transports are not implemented.

## Useful bug-report details

Please include the ECU/ROM version, Windows version, display scaling percentage, application theme,
definition file version, table or parameter name, and exact steps needed to reproduce the problem.
Screenshots are especially useful for layout or scaling issues. Do not attach proprietary or personal
files unless you intend to share them.

## License

BimmerStein Tuning Suite is distributed under `GPL-2.0-or-later`. The complete project and dependency notices are
included with both the portable build and installer.
