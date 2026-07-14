from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import shutil
import ssl
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DISTRIBUTIONS = (
    "charset-normalizer",
    "contourpy",
    "cycler",
    "fonttools",
    "kiwisolver",
    "matplotlib",
    "numpy",
    "packaging",
    "pillow",
    "pyparsing",
    "pyqtgraph",
    "pyserial",
    "PySide6",
    "PySide6_Addons",
    "PySide6_Essentials",
    "python-dateutil",
    "shiboken6",
    "six",
    "PyInstaller",
)
_PYSIDE_DISTRIBUTIONS = {"pyside6", "pyside6_addons", "pyside6_essentials", "shiboken6"}
_LICENSE_NAMES = ("license", "copying", "notice", "authors", "copyright")

_PYSERIAL_BSD = """pySerial
Copyright (C) 2001-2020 Chris Liechti <cliechti@gmx.net>

Redistribution and use in source and binary forms, with or without modification, are permitted
provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions
   and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of
   conditions and the following disclaimer in the documentation and/or other materials provided
   with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse
   or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF
THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def _declared_license(dist: metadata.Distribution) -> str:
    value = (
        dist.metadata.get("License-Expression")
        or dist.metadata.get("License")
        or "See included license files"
    ).strip()
    if "\n" not in value:
        return value
    for line in value.splitlines():
        normalized = line.strip()
        if normalized and any(character.isalnum() for character in normalized):
            return normalized[:200]
    return "See included license files"


def _license_files(dist: metadata.Distribution) -> list[tuple[Path, Path]]:
    found: list[tuple[Path, Path]] = []
    for relative in dist.files or ():
        parts = tuple(part.lower() for part in Path(str(relative)).parts)
        name = parts[-1] if parts else ""
        if not any(name.startswith(prefix) for prefix in _LICENSE_NAMES):
            continue
        if "licenseref-qt-commercial" in name:
            continue
        source = Path(dist.locate_file(relative))
        if source.is_file():
            found.append((source, Path(str(relative))))
    return found


def _copy_distribution_licenses(
    dist: metadata.Distribution, output: Path, project_license: Path
) -> list[str]:
    package_name = dist.metadata.get("Name") or "unknown"
    package_dir = output / f"{_safe_name(package_name)}-{_safe_name(dist.version)}"
    package_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    if package_name.lower() in _PYSIDE_DISTRIBUTIONS:
        target = package_dir / "GPL-2.0-selected.txt"
        shutil.copy2(project_license, target)
        copied.append(target.relative_to(output).as_posix())
    else:
        for source, relative in _license_files(dist):
            relative_target = Path(*(_safe_name(part) for part in relative.parts))
            target = package_dir / relative_target
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target.relative_to(output).as_posix())

    if package_name.lower() == "pyserial" and not copied:
        target = package_dir / "BSD-3-Clause.txt"
        target.write_text(_PYSERIAL_BSD, encoding="utf-8")
        copied.append(target.relative_to(output).as_posix())

    if not copied:
        license_text = _declared_license(dist)
        if len(license_text) < 80:
            raise RuntimeError(f"No complete license text found for {package_name} {dist.version}")
        target = package_dir / "LICENSE-from-package-metadata.txt"
        target.write_text(license_text + "\n", encoding="utf-8")
        copied.append(target.relative_to(output).as_posix())

    return copied


def collect_dependency_licenses(output: Path, *, root: Path = ROOT) -> Path:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Dependency-license output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    project_license = root / "LICENSE"
    if not project_license.is_file():
        raise FileNotFoundError(project_license)

    index: list[str] = [
        "BimmerStein Tuning Suite bundled dependency license inventory",
        "",
        "This inventory is generated from the exact Python environment used for the Windows build.",
        "The original license files below are copied without modification.",
        "",
    ]

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError(f"Python license was not found: {python_license}")
    python_target = output / f"Python-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_target.mkdir()
    shutil.copy2(python_license, python_target / "LICENSE.txt")
    index.extend((
        f"Python {sys.version.split()[0]}",
        "  License files: " + (python_target / "LICENSE.txt").relative_to(output).as_posix(),
        "",
    ))

    for distribution_name in RUNTIME_DISTRIBUTIONS:
        dist = metadata.distribution(distribution_name)
        copied = _copy_distribution_licenses(dist, output, project_license)
        index.extend((
            f"{dist.metadata.get('Name') or distribution_name} {dist.version}",
            f"  Declared license: {_declared_license(dist)}",
            *(f"  License file: {path}" for path in copied),
            "",
        ))

    packaging_dist = metadata.distribution("packaging")
    apache_source = next(
        (source for source, relative in _license_files(packaging_dist)
         if relative.name.upper() == "LICENSE.APACHE"),
        None,
    )
    if apache_source is None:
        raise RuntimeError("Apache-2.0 text required for bundled OpenSSL was not found")
    openssl_version = ssl.OPENSSL_VERSION.replace("OpenSSL ", "", 1).split()[0]
    openssl_dir = output / f"OpenSSL-{_safe_name(openssl_version)}"
    openssl_dir.mkdir()
    shutil.copy2(apache_source, openssl_dir / "Apache-2.0.txt")
    index.extend((
        f"OpenSSL {openssl_version}",
        "  Declared license: Apache-2.0",
        "  License file: " + (openssl_dir / "Apache-2.0.txt").relative_to(output).as_posix(),
        "",
        "Microsoft Visual C++ Runtime",
        "  Microsoft redistributable runtime files are included by the Python and Qt distributions.",
        "",
    ))

    (output / "INDEX.txt").write_text("\n".join(index).rstrip() + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect licenses for the frozen BimmerStein Tuning Suite build"
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    collect_dependency_licenses(args.output)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
