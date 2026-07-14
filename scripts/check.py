from __future__ import annotations
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- health gates -----------------------------------------------------------
GATES: list[tuple[str, list[str]]] = [
    ("ruff",          [sys.executable, "-m", "ruff", "check", "."]),
    ("mypy",          [sys.executable, "-m", "mypy", "ecueditor/core", "ecueditor/ui/design",
                        "ecueditor/ui/workspace", "ecueditor/ui/editor/frames",
                        "ecueditor/ui/dialogs/definition_manager.py",
                        "ecueditor/ui/logger/gauges"]),
    ("pytest",        [sys.executable, "-m", "pytest", "-q"]),
    ("core-purity",   [sys.executable, "-m", "pytest", "tests/test_core_purity.py", "-q"]),
]

def run_gates() -> int:
    failures: list[str] = []
    for name, cmd in GATES:
        print(f"\n=== {name}: {' '.join(cmd)} ===", flush=True)
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            failures.append(name)
    print("\n" + ("ALL GREEN" if not failures else f"FAILED: {', '.join(failures)}"))
    return 1 if failures else 0

# --- markdown internal-link linter -----------------------------------------
_LINK = re.compile(r"\]\(([^)\s]+?)(?:#[^)]*)?\)")
_DEFAULT_LINK_ROOTS = ["docs", "README.md", "CONTRIBUTING.md"]

def check_links(paths: list[str]) -> int:
    roots = [ROOT / p for p in (paths or _DEFAULT_LINK_ROOTS)]
    md_files: list[Path] = []
    missing_roots: list[str] = []
    for r in roots:
        if r.suffix == ".md":
            if r.is_file():
                md_files.append(r)
            else:
                missing_roots.append(str(r))     # e.g. CONTRIBUTING.md before Task 8 — skip, don't crash
        elif r.is_dir():
            md_files += list(r.rglob("*.md"))
        else:
            missing_roots.append(str(r))
    for mr in missing_roots:
        print("MISSING ROOT (skipped):", Path(mr).name)
    broken: list[str] = []
    for md in md_files:
        base = md.parent
        for m in _LINK.finditer(md.read_text(encoding="utf-8")):
            tgt = m.group(1)
            if tgt.startswith("#"):                       # in-page anchor, not a file target
                continue
            if tgt.startswith(("http://", "https://", "mailto:")):
                continue
            if not (base / tgt).resolve().exists():
                broken.append(f"{md.relative_to(ROOT)} -> {tgt}")
    for b in broken:
        print("BROKEN LINK:", b)
    print("links OK" if not broken else f"{len(broken)} broken link(s)")
    return 1 if broken else 0

# --- SKILL.md frontmatter linter -------------------------------------------
def check_frontmatter(paths: list[str]) -> int:
    targets = [ROOT / p for p in paths] if paths else []
    bad: list[str] = []
    for sk in targets:
        text = sk.read_text(encoding="utf-8")
        if not text.startswith("---"):
            bad.append(f"{sk.relative_to(ROOT)}: no frontmatter block"); continue
        block = text.split("---", 2)[1]
        keys = {ln.split(":", 1)[0].strip() for ln in block.splitlines() if ":" in ln and not ln.startswith(" ")}
        for required in ("name", "description"):
            if required not in keys:
                bad.append(f"{sk.relative_to(ROOT)}: missing '{required}'")
    for b in bad:
        print("FRONTMATTER:", b)
    print("frontmatter OK" if not bad else f"{len(bad)} problem(s)")
    return 1 if bad else 0

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="BimmerStein Tuning Suite health + docs gate")
    p.add_argument("--links", nargs="*", metavar="PATH", help="check internal md links (default: project docs)")
    p.add_argument("--frontmatter", nargs="*", metavar="SKILL", help="lint SKILL.md frontmatter")
    args = p.parse_args(argv)
    if args.links is not None:
        return check_links(args.links)
    if args.frontmatter is not None:
        return check_frontmatter(args.frontmatter)
    return run_gates()

if __name__ == "__main__":
    raise SystemExit(main())
