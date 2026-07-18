# Building BimmerStein Tuning Suite

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

For a Nuitka build, install its build-only dependencies in the Python environment selected for that
backend:

```powershell
python -m pip install "nuitka>=4" ordered-set zstandard
```

The build-environment inventories shipped with each beta record the exact Python, backend, and
dependency versions used for each binary. Inno Setup 6 is also required to build the installer EXE.

## Complete release

```powershell
python scripts/build_release.py --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

That command preserves the established PyInstaller-only build. To build the transitional dual
release, explicitly select both backends and the Python environment containing Nuitka:

```powershell
python scripts/build_release.py --backend both `
  --nuitka-python ".nuitka-venv\Scripts\python.exe" `
  --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

Use `--backend nuitka` for only the Nuitka packages. `--nuitka-cache <directory>` can preserve the
compiler cache between clean release builds. The command creates `release\<version>\` with the
selected unpacked applications, portable ZIPs, installer EXEs, corresponding-source ZIP, release
notes, build-environment inventories, and SHA-256 checksums.

For a portable and source build without the installer:

```powershell
python scripts/build_release.py --backend both `
  --nuitka-python ".nuitka-venv\Scripts\python.exe" --no-installer
```

Both backends produce standalone directories rather than single-file executables. Each launcher EXE
requires the DLLs and resources beside it. Distribute the complete portable ZIP or installer, not
`BimmerStein-Tuning-Suite.exe` by itself.

Windows builds must be produced on Windows. A future Linux package should be compiled natively on
Linux from the same source; Nuitka does not cross-compile a Windows build into a Linux binary.

## Verification

The corresponding-source archive contains the application and every input needed to rebuild the
release. Development-only tests and internal project records are not part of that distribution
archive.

Before publishing a build, maintainers run the complete automated test suite, inspect the payloads,
launch the portable executable, test the installer and uninstaller, and verify every file listed in
`SHA256SUMS.txt`.
