from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecueditor import __version__  # noqa: E402
from ecueditor.metadata import (  # noqa: E402
    PUBLISHER,
    PRODUCT_NAME,
    WINDOWS_APP_STEM,
    display_version,
    windows_numeric_version,
)
from scripts.collect_dependency_licenses import (  # noqa: E402
    RUNTIME_DISTRIBUTIONS,
    collect_dependency_licenses,
)
from scripts.build_nuitka_release import (  # noqa: E402
    build_nuitka_application,
    nuitka_environment_text,
)


_SOURCE_ROOT_FILES = (
    "BUILDING.md",
    "LICENSE",
    "MANIFEST.in",
    "output/pdf/BimmerStein-Tuning-Suite-User-Manual.pdf",
    "README.md",
    "RELEASE_NOTES.md",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
)
_SOURCE_TREES = ("ecueditor", "manual", "plugins", "resources")
_SOURCE_SCRIPTS = (
    "packaging/ecueditor.iss",
    "packaging/ecueditor.spec",
    "packaging/nuitka_entry.py",
    "packaging/build_user_manual.py",
    "scripts/__init__.py",
    "scripts/build_app_icon.py",
    "scripts/build_nuitka_release.py",
    "scripts/build_release.py",
    "scripts/collect_dependency_licenses.py",
)
_EXCLUDED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "screenshots",
}


def source_files(root: Path = ROOT) -> list[Path]:
    candidates: list[Path] = []
    for relative in _SOURCE_ROOT_FILES + _SOURCE_SCRIPTS:
        path = root / relative
        if path.is_file():
            candidates.append(path)
    for relative in _SOURCE_TREES:
        tree = root / relative
        if not tree.is_dir():
            continue
        candidates.extend(
            path for path in tree.rglob("*")
            if path.is_file()
            and not any(part in _EXCLUDED_PARTS for part in path.relative_to(root).parts)
            and path.suffix.lower() not in {".pyc", ".log", ".tmp"}
        )
    return sorted(set(candidates), key=lambda path: path.relative_to(root).as_posix())


def _reset_directory(path: Path, *, allowed_parent: Path) -> None:
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if resolved == parent or parent not in resolved.parents:
        raise RuntimeError(f"Refusing to reset path outside {parent}: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _zip_tree(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, (Path(source.name) / path.relative_to(source)).as_posix())


def _copy_source_tree(
    destination: Path,
    licenses: Path,
    environment_files: tuple[Path, ...],
) -> None:
    for source in source_files(ROOT):
        target = destination / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copytree(licenses, destination / "DEPENDENCY_LICENSES")
    for environment_file in environment_files:
        shutil.copy2(environment_file, destination / environment_file.name)


def _build_environment_text() -> str:
    rows = [
        f"{PRODUCT_NAME} {__version__} build environment",
        f"Platform: {platform.platform()}",
        f"Python: {sys.version}",
        "",
        "Python distributions:",
    ]
    for name in RUNTIME_DISTRIBUTIONS:
        dist = metadata.distribution(name)
        rows.append(f"{dist.metadata.get('Name') or name}=={dist.version}")
    return "\n".join(rows) + "\n"


def _windows_version_info(version: str) -> Any:
    """Return a PyInstaller VersionInfo object derived from canonical metadata."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numeric_version = windows_numeric_version(version)
    numeric_parts = tuple(int(part) for part in numeric_version.split("."))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric_parts, prodvers=numeric_parts),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [
                    StringStruct("CompanyName", PUBLISHER),
                    StringStruct("FileDescription", PRODUCT_NAME),
                    StringStruct("FileVersion", numeric_version),
                    StringStruct("InternalName", WINDOWS_APP_STEM),
                    StringStruct(
                        "LegalCopyright",
                        f"Copyright (C) 2026 {PUBLISHER} and contributors",
                    ),
                    StringStruct("OriginalFilename", f"{WINDOWS_APP_STEM}.exe"),
                    StringStruct("ProductName", PRODUCT_NAME),
                    StringStruct("ProductVersion", display_version(version)),
                ]),
            ]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


def _windows_version_info_text(version: str) -> str:
    """Serialize canonical Windows executable metadata for PyInstaller."""
    return str(_windows_version_info(version))


def _find_iscc(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path(os.environ["INNO_ISCC"]) if os.environ.get("INNO_ISCC") else None,
        Path(r"C:\tmp\ECUEditor-InnoSetup6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    if found:
        return Path(found).resolve()
    raise FileNotFoundError("Inno Setup compiler ISCC.exe was not found; pass --iscc")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_portable(
    *,
    built_app: Path,
    portable_name: str,
    release_dir: Path,
    environment_file: Path,
) -> tuple[Path, Path, Path]:
    portable_dir = release_dir / portable_name
    shutil.copytree(built_app, portable_dir)
    shutil.copy2(environment_file, portable_dir / "BUILD_ENVIRONMENT.txt")
    portable_zip = release_dir / f"{portable_name}.zip"
    _zip_tree(portable_dir, portable_zip)
    executable = portable_dir / f"{WINDOWS_APP_STEM}.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Packaged application is missing {executable.name}")
    return portable_dir, portable_zip, executable


def _compile_installer(
    *,
    compiler: Path,
    source_dir: Path,
    release_dir: Path,
    version: str,
    package_suffix: str,
) -> Path:
    _run([
        str(compiler),
        f"/DAppVersion={version}",
        f"/DAppDisplayVersion={display_version(version)}",
        f"/DAppNumericVersion={windows_numeric_version(version)}",
        f"/DPackageSuffix={package_suffix}",
        f"/DSourceDir={source_dir}",
        f"/DOutputDir={release_dir}",
        "packaging/ecueditor.iss",
    ])
    setup = release_dir / (
        f"{WINDOWS_APP_STEM}-{version}-Windows-x64{package_suffix}-Setup.exe"
    )
    if not setup.is_file():
        raise FileNotFoundError("Inno Setup did not produce the expected installer")
    return setup


def _collect_release_licenses(
    *,
    output: Path,
    include_nuitka: bool,
    nuitka_python: Path | None,
    include_pyinstaller: bool,
) -> None:
    if not include_nuitka:
        collect_dependency_licenses(output)
        return
    if nuitka_python is None:
        raise ValueError("A Nuitka Python executable is required for a Nuitka build")
    command = [
        str(nuitka_python),
        str(ROOT / "scripts" / "collect_dependency_licenses.py"),
        str(output),
    ]
    if include_pyinstaller:
        command.extend(("--backend", "pyinstaller"))
    command.extend(("--backend", "nuitka"))
    _run(command)


def build_release(
    *,
    output_root: Path,
    iscc: Path | None,
    installer: bool = True,
    backend: str = "pyinstaller",
    nuitka_python: Path | None = None,
    nuitka_cache: Path | None = None,
) -> Path:
    if backend not in {"pyinstaller", "nuitka", "both"}:
        raise ValueError(f"Unsupported release backend: {backend}")
    version = __version__
    include_pyinstaller = backend in {"pyinstaller", "both"}
    include_nuitka = backend in {"nuitka", "both"}
    resolved_nuitka_python = (
        (nuitka_python or Path(sys.executable)).resolve() if include_nuitka else None
    )
    if resolved_nuitka_python is not None and not resolved_nuitka_python.is_file():
        raise FileNotFoundError(resolved_nuitka_python)
    nuitka_suffix = "-Nuitka"
    release_dir = (output_root / version).resolve()
    build_root = (ROOT / ".tmp" / f"release-{version}").resolve()
    _reset_directory(release_dir, allowed_parent=output_root)
    _reset_directory(build_root, allowed_parent=ROOT / ".tmp")

    licenses = build_root / "DEPENDENCY_LICENSES"
    _collect_release_licenses(
        output=licenses,
        include_nuitka=include_nuitka,
        nuitka_python=resolved_nuitka_python,
        include_pyinstaller=include_pyinstaller,
    )
    environment_files: list[Path] = []
    artifacts: list[Path] = []
    compiler = _find_iscc(iscc) if installer else None

    if include_pyinstaller:
        environment_file = build_root / "BUILD_ENVIRONMENT.txt"
        environment_file.write_text(_build_environment_text(), encoding="utf-8")
        environment_files.append(environment_file)
        version_file = build_root / f"{WINDOWS_APP_STEM}-version-info.txt"
        version_file.write_text(_windows_version_info_text(version), encoding="utf-8")

        pyinstaller_dist = build_root / "pyinstaller-dist"
        pyinstaller_work = build_root / "pyinstaller-work"
        build_env = os.environ.copy()
        build_env["ECUEDITOR_DEPENDENCY_LICENSES"] = str(licenses)
        build_env["ECUEDITOR_VERSION_FILE"] = str(version_file)
        _run([
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(pyinstaller_dist),
            "--workpath",
            str(pyinstaller_work),
            "packaging/ecueditor.spec",
        ], env=build_env)

        built_app = pyinstaller_dist / WINDOWS_APP_STEM
        executable = built_app / f"{WINDOWS_APP_STEM}.exe"
        if not executable.is_file():
            raise FileNotFoundError(f"PyInstaller did not produce {executable.name}")
        portable_name = f"{WINDOWS_APP_STEM}-{version}-Windows-x64"
        portable_dir, portable_zip, portable_executable = _package_portable(
            built_app=built_app,
            portable_name=portable_name,
            release_dir=release_dir,
            environment_file=environment_file,
        )
        artifacts.extend((portable_zip, portable_executable))
        if compiler is not None:
            artifacts.append(_compile_installer(
                compiler=compiler,
                source_dir=portable_dir,
                release_dir=release_dir,
                version=version,
                package_suffix="",
            ))

    if include_nuitka:
        assert resolved_nuitka_python is not None
        nuitka_environment = build_root / "BUILD_ENVIRONMENT-Nuitka.txt"
        nuitka_environment.write_text(
            nuitka_environment_text(resolved_nuitka_python, version=version),
            encoding="utf-8",
        )
        environment_files.append(nuitka_environment)
        built_nuitka = build_nuitka_application(
            python_executable=resolved_nuitka_python,
            output_dir=build_root / "nuitka-output",
            cache_dir=(nuitka_cache or ROOT / ".tmp" / "nuitka-cache").resolve(),
            dependency_licenses=licenses,
            environment_file=nuitka_environment,
            version=version,
        )
        portable_name = (
            f"{WINDOWS_APP_STEM}-{version}-Windows-x64{nuitka_suffix}"
        )
        portable_dir, portable_zip, portable_executable = _package_portable(
            built_app=built_nuitka,
            portable_name=portable_name,
            release_dir=release_dir,
            environment_file=nuitka_environment,
        )
        artifacts.extend((portable_zip, portable_executable))
        if compiler is not None:
            artifacts.append(_compile_installer(
                compiler=compiler,
                source_dir=portable_dir,
                release_dir=release_dir,
                version=version,
                package_suffix=nuitka_suffix,
            ))

    source_name = f"{WINDOWS_APP_STEM}-{version}-Source"
    source_stage = build_root / source_name
    source_stage.mkdir()
    _copy_source_tree(source_stage, licenses, tuple(environment_files))
    source_zip = release_dir / f"{source_name}.zip"
    _zip_tree(source_stage, source_zip)
    artifacts.append(source_zip)

    shutil.copy2(ROOT / "RELEASE_NOTES.md", release_dir / "RELEASE_NOTES.md")
    for environment_file in environment_files:
        shutil.copy2(environment_file, release_dir / environment_file.name)
    checksums = [
        f"{_sha256(path)}  {path.relative_to(release_dir).as_posix()}"
        for path in artifacts
    ]
    (release_dir / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii")
    return release_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Build the {PRODUCT_NAME} Windows beta release")
    parser.add_argument("--output-root", type=Path, default=ROOT / "release")
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--no-installer", action="store_true")
    parser.add_argument(
        "--backend",
        choices=("pyinstaller", "nuitka", "both"),
        default="pyinstaller",
    )
    parser.add_argument("--nuitka-python", type=Path)
    parser.add_argument("--nuitka-cache", type=Path)
    args = parser.parse_args(argv)
    release_dir = build_release(
        output_root=args.output_root,
        iscc=args.iscc,
        installer=not args.no_installer,
        backend=args.backend,
        nuitka_python=args.nuitka_python,
        nuitka_cache=args.nuitka_cache,
    )
    print(f"Release ready: {release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
