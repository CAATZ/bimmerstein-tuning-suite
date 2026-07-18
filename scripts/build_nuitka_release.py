from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecueditor import __version__  # noqa: E402
from ecueditor.metadata import (  # noqa: E402
    PRODUCT_NAME,
    PUBLISHER,
    WINDOWS_APP_STEM,
    windows_numeric_version,
)
from scripts.collect_dependency_licenses import (  # noqa: E402
    NUITKA_BUILD_DISTRIBUTIONS,
    RUNTIME_DISTRIBUTIONS,
)


_ENVIRONMENT_PROBE = """
import json
from importlib import metadata
import platform
import sys

names = json.loads(sys.argv[1])
print(json.dumps({
    "platform": platform.platform(),
    "python": sys.version,
    "distributions": [
        [metadata.distribution(name).metadata.get("Name") or name,
         metadata.distribution(name).version]
        for name in names
    ],
}))
"""


def nuitka_environment_text(
    python_executable: Path,
    *,
    version: str = __version__,
) -> str:
    distributions = (*RUNTIME_DISTRIBUTIONS, *NUITKA_BUILD_DISTRIBUTIONS)
    completed = subprocess.run(
        [
            str(python_executable),
            "-c",
            _ENVIRONMENT_PROBE,
            json.dumps(distributions),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    details: dict[str, Any] = json.loads(completed.stdout)
    rows = [
        f"{PRODUCT_NAME} {version} Nuitka build environment",
        f"Platform: {details['platform']}",
        f"Python: {details['python']}",
        "Backend: Nuitka standalone",
        "",
        "Python distributions:",
    ]
    rows.extend(
        f"{name}=={distribution_version}"
        for name, distribution_version in details["distributions"]
    )
    return "\n".join(rows) + "\n"


def nuitka_command(
    *,
    python_executable: Path,
    output_dir: Path,
    report: Path,
    dependency_licenses: Path,
    environment_file: Path,
    version: str = __version__,
) -> list[str]:
    numeric_version = windows_numeric_version(version)
    copyright_text = f"Copyright (C) 2026 {PUBLISHER} and contributors"
    data_files = (
        (ROOT / "LICENSE", "LICENSE"),
        (ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
        (ROOT / "RELEASE_NOTES.md", "RELEASE_NOTES.md"),
        (
            ROOT / "output" / "pdf" / "BimmerStein-Tuning-Suite-User-Manual.pdf",
            "BimmerStein-Tuning-Suite-User-Manual.pdf",
        ),
        (environment_file, "BUILD_ENVIRONMENT.txt"),
    )
    command = [
        str(python_executable),
        "-m",
        "nuitka",
        "--mode=standalone",
        "--msvc=latest",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--include-package=ecueditor",
        "--include-package=scipy._external.array_api_compat.numpy",
        "--windows-console-mode=disable",
        "--include-windows-runtime-dlls=yes",
        f"--include-data-dir={ROOT / 'resources'}=resources",
        f"--include-data-dir={dependency_licenses}=DEPENDENCY_LICENSES",
        f"--windows-icon-from-ico={ROOT / 'resources' / 'icons' / 'app.ico'}",
        f"--output-filename={WINDOWS_APP_STEM}.exe",
        f"--output-dir={output_dir}",
        f"--report={report}",
        f"--company-name={PUBLISHER}",
        f"--product-name={PRODUCT_NAME}",
        f"--file-description={PRODUCT_NAME}",
        f"--file-version={numeric_version}",
        f"--product-version={numeric_version}",
        f"--copyright={copyright_text}",
    ]
    command.extend(f"--include-data-file={source}={target}" for source, target in data_files)
    command.append(str(ROOT / "packaging" / "nuitka_entry.py"))
    return command


def _copy_public_plugins(destination: Path) -> None:
    source = ROOT / "plugins"
    destination.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return
    for plugin in source.glob("*.py"):
        if plugin.name.startswith(("_", "demo")):
            continue
        shutil.copy2(plugin, destination / plugin.name)


def build_nuitka_application(
    *,
    python_executable: Path,
    output_dir: Path,
    cache_dir: Path,
    dependency_licenses: Path,
    environment_file: Path,
    version: str = __version__,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    report = output_dir / "nuitka-report.xml"
    environment = os.environ.copy()
    environment["NUITKA_CACHE_DIR"] = str(cache_dir)
    temp_dir = output_dir / "temp"
    temp_dir.mkdir()
    environment["TEMP"] = str(temp_dir)
    environment["TMP"] = str(temp_dir)
    command = nuitka_command(
        python_executable=python_executable,
        output_dir=output_dir,
        report=report,
        dependency_licenses=dependency_licenses,
        environment_file=environment_file,
        version=version,
    )
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)

    built_app = output_dir / "nuitka_entry.dist"
    executable = built_app / f"{WINDOWS_APP_STEM}.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Nuitka did not produce {executable}")
    _copy_public_plugins(built_app / "plugins")
    return built_app
