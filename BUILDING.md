# Building BimmerStein Tuning Suite 0.1.0 Beta 1

The corresponding-source archive contains everything needed to rebuild the Windows application and
installer. Use 64-bit Python 3.11 or newer on Windows.

## Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[gui,comms]"
python -m pip install "pyinstaller>=6"
```

The build-environment inventory shipped with each beta records the exact Python and dependency
versions used for that binary. Inno Setup 6 is also required to build the installer EXE.

## Complete release

```powershell
python scripts/build_release.py --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

The command creates `release\0.1.0b1\` with the unpacked application, portable ZIP, installer EXE,
corresponding-source ZIP, release notes, build-environment inventory, and SHA-256 checksums.

For a portable and source build without the installer:

```powershell
python scripts/build_release.py --no-installer
```

The launcher EXE is a one-directory PyInstaller application and requires the DLLs and resources beside
it. Distribute the complete portable ZIP or installer, not `BimmerStein-Tuning-Suite.exe` by itself.

## Verification

The corresponding-source archive contains the application and every input needed to rebuild the
release. Development-only tests and internal project records are not part of that distribution
archive.

Before publishing a build, maintainers run the complete automated test suite, inspect the payloads,
launch the portable executable, test the installer and uninstaller, and verify every file listed in
`SHA256SUMS.txt`.
