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
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecueditor import __version__  # noqa: E402
from ecueditor.metadata import PRODUCT_NAME, WINDOWS_APP_STEM  # noqa: E402
from scripts.collect_dependency_licenses import (  # noqa: E402
    RUNTIME_DISTRIBUTIONS,
    collect_dependency_licenses,
)


_SOURCE_ROOT_FILES = (
    "BUILDING.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "RELEASE_NOTES.md",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
)
_SOURCE_TREES = ("ecueditor", "plugins", "resources")
_SOURCE_SCRIPTS = (
    "packaging/ecueditor.iss",
    "packaging/ecueditor.spec",
    "scripts/__init__.py",
    "scripts/build_app_icon.py",
    "scripts/build_release.py",
    "scripts/check.py",
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


def _copy_source_tree(destination: Path, licenses: Path, environment_file: Path) -> None:
    for source in source_files(ROOT):
        target = destination / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copytree(licenses, destination / "DEPENDENCY_LICENSES")
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


def build_release(*, output_root: Path, iscc: Path | None, installer: bool = True) -> Path:
    version = __version__
    release_dir = (output_root / version).resolve()
    build_root = (ROOT / ".tmp" / f"release-{version}").resolve()
    _reset_directory(release_dir, allowed_parent=output_root)
    _reset_directory(build_root, allowed_parent=ROOT / ".tmp")

    licenses = build_root / "DEPENDENCY_LICENSES"
    collect_dependency_licenses(licenses)
    environment_file = build_root / "BUILD_ENVIRONMENT.txt"
    environment_file.write_text(_build_environment_text(), encoding="utf-8")

    pyinstaller_dist = build_root / "pyinstaller-dist"
    pyinstaller_work = build_root / "pyinstaller-work"
    build_env = os.environ.copy()
    build_env["ECUEDITOR_DEPENDENCY_LICENSES"] = str(licenses)
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
    portable_dir = release_dir / portable_name
    shutil.copytree(built_app, portable_dir)
    shutil.copy2(environment_file, portable_dir / environment_file.name)
    portable_zip = release_dir / f"{portable_name}.zip"
    _zip_tree(portable_dir, portable_zip)

    source_name = f"{WINDOWS_APP_STEM}-{version}-Source"
    source_stage = build_root / source_name
    source_stage.mkdir()
    _copy_source_tree(source_stage, licenses, environment_file)
    source_zip = release_dir / f"{source_name}.zip"
    _zip_tree(source_stage, source_zip)

    artifacts = [portable_zip, source_zip, portable_dir / executable.name]
    if installer:
        compiler = _find_iscc(iscc)
        numeric_version = "0.1.0.1"
        _run([
            str(compiler),
            f"/DAppVersion={version}",
            f"/DAppNumericVersion={numeric_version}",
            f"/DSourceDir={portable_dir}",
            f"/DOutputDir={release_dir}",
            "packaging/ecueditor.iss",
        ])
        setup = release_dir / f"{WINDOWS_APP_STEM}-{version}-Windows-x64-Setup.exe"
        if not setup.is_file():
            raise FileNotFoundError("Inno Setup did not produce the expected installer")
        artifacts.append(setup)

    shutil.copy2(ROOT / "RELEASE_NOTES.md", release_dir / "RELEASE_NOTES.md")
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
    args = parser.parse_args(argv)
    release_dir = build_release(
        output_root=args.output_root,
        iscc=args.iscc,
        installer=not args.no_installer,
    )
    print(f"Release ready: {release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
