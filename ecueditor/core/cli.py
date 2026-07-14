from __future__ import annotations
import argparse
from ecueditor.core.defs.library import DefinitionLibrary
from ecueditor.core.defs.parser import parse_definition_file
from ecueditor.core.rom.image import RomImage
from ecueditor.core.errors import ECUEditorError

def _cmd_roms(a) -> int:
    doc = parse_definition_file(a.deffile)
    for r in doc.rom_ids:
        print(f"{r.xmlid}\t{r.internal_id_string}\tfilesize={r.filesize}")
    return 0

def _open(a) -> RomImage:
    return RomImage.open(a.binfile, DefinitionLibrary([a.deffile]))

def _cmd_tables(a) -> int:
    rom = _open(a)
    for name, t in sorted(rom.tables.items()):
        print(f"{name}\t{t.definition.type}\t0x{t.definition.storage_address:X}")
    return 0

def _cmd_read(a) -> int:
    rom = _open(a)
    if a.name not in rom.tables:
        print(f"error: no table named {a.name!r}")
        return 2
    t = rom.table(a.name)
    print(t.to_text())
    return 0

def _cmd_checksum(a) -> int:
    rom = _open(a)
    if a.correct:
        notes = rom.save(a.binfile)          # flush(no edits)+checksum update+write
        for n in notes: print(n)
        return 0
    ok, details = rom.checksum_status()
    for d in details: print(d)
    return 0 if ok else 1

def _cmd_logsim(a) -> int:
    from ecueditor.core.logger.session import run_replay_session
    channels = [c.strip() for c in a.channels.split(",") if c.strip()]
    result = run_replay_session(a.logger_def, a.script, channels,
                                polls=a.polls, record_dir=a.csv)
    print(f"ECU-ID {result.ecu_id}: polled {len(result.samples)} sample(s)")
    for s in result.samples:
        cols = "  ".join(f"{k}={v}" for k, v in s.values.items())
        print(f"  t={s.timestamp_ms:>7.1f}ms  {cols}")
    return 0

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ecueditor-cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    def _add_def(sp): sp.add_argument("--def", dest="deffile", required=True)
    def _add_bin(sp): sp.add_argument("--bin", dest="binfile", required=True)
    r = sub.add_parser("roms"); _add_def(r); r.set_defaults(fn=_cmd_roms)
    t = sub.add_parser("tables"); _add_def(t); _add_bin(t); t.set_defaults(fn=_cmd_tables)
    rd = sub.add_parser("read"); rd.add_argument("name"); _add_def(rd); _add_bin(rd); rd.set_defaults(fn=_cmd_read)
    ck = sub.add_parser("checksum"); ck.add_argument("--correct", action="store_true")
    _add_def(ck); _add_bin(ck); ck.set_defaults(fn=_cmd_checksum)
    ls = sub.add_parser("logsim", help="replay-driven DS2 logging session (no hardware)")
    ls.add_argument("--logger-def", dest="logger_def", required=True)
    ls.add_argument("--script", required=True)
    ls.add_argument("--channels", required=True, help="comma-separated channel ids, e.g. E2,P8")
    ls.add_argument("--polls", type=int, default=1)
    ls.add_argument("--csv", default=None, help="output dir for a tuning-suite log CSV")
    ls.set_defaults(fn=_cmd_logsim)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except ECUEditorError as exc:
        print(f"error: {exc}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
