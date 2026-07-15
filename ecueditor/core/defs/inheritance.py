"""Definition-inheritance chain walk + role-based axis merge.

Ported from `ms41def.py` (`chain_for`, `pick_rom`, `_ancestor_chain`, `resolve_tables`,
lines ~93-183) with the same three laws: BASE-vs-derived scoping (most-derived
non-None attribute wins per table name), axes merged by ROLE (X/Y) rather than
name, and `omit="true"` deleting a table from the effective set. The chain-walk
functions (`_ancestor_chain`, `_resolved_addr_count`, `pick_rom`, `chain_for`) are
ported verbatim; the per-key merge in `resolve_tables` is extended per the plan to
also accumulate description/userlevel/locked/logparam, Switch `<state>` children,
and BitwiseSwitch `<bit>` children. The final step constructs the frozen
dataclasses from `ecueditor.core.defs.model` once the merge is complete, instead
of returning plain dicts.
"""
from __future__ import annotations
import logging
from xml.etree import ElementTree as ET

from ecueditor.core.defs.model import RomDefinition, RomId, TableDef, AxisDef, ScaleDef
from ecueditor.core.defs.parser import _hexint, _dec, _filesize
from ecueditor.core.errors import DefinitionError

log = logging.getLogger(__name__)

_TABLE_TYPES = {"1D", "2D", "3D", "Switch", "BitwiseSwitch"}


def _role(type_attr: str | None) -> str | None:
    t = (type_attr or "").lower()
    if "x axis" in t:
        return "X"
    if "y axis" in t:
        return "Y"
    return None


def _scale_of(node: ET.Element) -> ScaleDef | None:
    sc = node.find("scaling")
    if sc is None:
        return None

    def f(name: str, default: float) -> float:
        v = sc.get(name)
        return float(v) if v not in (None, "") else default

    return ScaleDef(
        units=sc.get("units") or "",
        expression=sc.get("expression") or "x",
        to_byte=sc.get("to_byte") or "",
        format=sc.get("format") or "0.00",
        fine_increment=f("fineincrement", 1.0),
        coarse_increment=f("coarseincrement", 2.0),
    )


def _description_of(node: ET.Element) -> str | None:
    """Return either supported RomRaider description spelling.

    The compact fixtures and some imported definitions use a ``description``
    attribute.  The MS41 definition corpus uses a child ``<description>``
    element, often with meaningful line breaks.  A child element wins when both
    are present because it is the native, potentially multi-line form.
    """
    child = node.find("description")
    if child is not None:
        return "".join(child.itertext()).strip()
    return node.get("description")


def _ancestor_chain(by_xid: dict[str, list[ET.Element]], rom_el: ET.Element) -> list[ET.Element]:
    """Walk base from a specific rom ELEMENT, picking the most-tables variant at each
    ancestor (no recursion into pick_rom). Returns [root ... rom_el]."""
    ch, cur, seen = [rom_el], rom_el.get("base"), set()
    while cur and cur in by_xid and cur not in seen:
        seen.add(cur)
        el = max(by_xid[cur], key=lambda r: len(r.findall("table")))
        ch.append(el)
        cur = el.get("base")
    return ch[::-1]


def _resolved_addr_count(by_xid: dict[str, list[ET.Element]], rom_el: ET.Element) -> int:
    """How many distinct table NAMEs get a storageaddress through this element's chain."""
    names: dict[str, int] = {}
    for layer in _ancestor_chain(by_xid, rom_el):
        for t in layer.findall("table"):
            nm = t.get("name")
            if not nm:
                continue
            if t.get("omit") == "true":
                names.pop(nm, None)
                continue
            if t.get("storageaddress"):
                names[nm] = 1
    return len(names)


def pick_rom(by_xid: dict[str, list[ET.Element]], xid: str) -> ET.Element | None:
    """Choose the rom element whose full inheritance chain resolves the MOST addressed
    tables. This correctly handles a CAL-ID published under two romid framings with
    different bases."""
    lst = by_xid.get(xid)
    if not lst:
        return None
    if len(lst) == 1:
        return lst[0]
    return max(lst, key=lambda r: _resolved_addr_count(by_xid, r))


def chain_for(by_xid: dict[str, list[ET.Element]], xid: str) -> list[str]:
    """inheritance chain, ROOT first ... derived last (RomRaider apply order)."""
    ch: list[str] = []
    cur: str | None = xid
    seen: set[str] = set()
    while cur and cur in by_xid and cur not in seen:
        ch.append(cur)
        seen.add(cur)
        r = pick_rom(by_xid, cur)
        cur = r.get("base") if r is not None else None
    return ch[::-1]


def _merge_axis(a: dict, ax: ET.Element) -> None:
    """Merge one axis-layer XML node's attributes into the accumulator dict `a`
    (most-derived non-None wins per key, applied layer by layer in chain order)."""
    if ax.get("name") is not None:
        a["name"] = ax.get("name")
    for k in ("storageaddress", "storagetype", "endian", "sizex", "sizey", "type", "logparam"):
        v = ax.get(k)
        if v is not None:
            a[k] = v
    sc = _scale_of(ax)
    if sc is not None:
        a["scale"] = sc
    data = ax.findall("data")
    if data:
        a["static"] = [d.text for d in data]


def _build_axis(a: dict, role: str, parent_size_x: int | None, parent_size_y: int | None,
                table_name: str | None = None) -> AxisDef:
    size = _dec(a.get("sizex")) if role == "X" else _dec(a.get("sizey"))
    if size is None:
        size = parent_size_x if role == "X" else parent_size_y
    static_values: tuple[float | str, ...] | None = None
    raw_static = a.get("static")
    if raw_static:
        parsed: list[float | str] = []
        for v in raw_static:
            if v is None:
                continue
            s = v.strip()
            try:
                parsed.append(float(s))
            except ValueError:
                # non-numeric <data> — pervasive in real defs (static-axis prose used as
                # row/column labels, e.g. 'Byte 4' in BMWMS41BASE): a normal condition, hence
                # debug. Kept (not dropped) as a stripped string so the UI can render it as a
                # header label; build_table still builds no axis sub-Table for it (table.py).
                log.debug("keeping non-numeric static <data> %r as a label on %s axis of table %r",
                          v, role, table_name)
                parsed.append(s)
        static_values = tuple(parsed) or None
    return AxisDef(
        role=role,  # type: ignore[arg-type]
        storage_address=_hexint(a.get("storageaddress")),
        storage_type=a.get("storagetype"),
        endian=a.get("endian"),
        size=size,
        scale=a.get("scale"),
        static_values=static_values,
        name=a.get("name"),
        logparam=a.get("logparam"),
    )


def resolve_tables(by_xid: dict[str, list[ET.Element]], xid: str) -> tuple[dict[str, dict], list[str]]:
    """Return ({name: merged-attrs}, chain) for the CAL-ID `xid`, merging the chain
    (most-derived wins per key), axes keyed by ROLE (X/Y). Mirrors ms41def.resolve_tables
    but additionally accumulates states/bits/description/userlevel/locked/logparam so the
    frozen dataclasses can be built from this dict in one shot."""
    ch = chain_for(by_xid, xid)
    eff: dict[str, dict] = {}
    for layer in ch:
        rom_el = pick_rom(by_xid, layer)
        if rom_el is None:
            continue
        for tbl in rom_el.findall("table"):
            nm = tbl.get("name")
            if not nm:
                continue
            if tbl.get("omit") == "true":
                eff.pop(nm, None)
                continue
            e = eff.setdefault(nm, {"name": nm, "axes": {}, "states": [], "bits": []})
            for k in ("storageaddress", "sizex", "sizey", "storagetype", "endian", "type",
                      "category", "userlevel", "locked", "logparam",
                      "swapxy", "flipx", "flipy"):
                v = tbl.get(k)
                if v is not None:
                    e[k] = v
            description = _description_of(tbl)
            if description is not None:
                e["description"] = description
            sc = _scale_of(tbl)
            if sc is not None:
                e["scale"] = sc
            # Switch states (re-declared per layer that carries them; last layer with
            # <state> children wins wholesale, matching how a derived override that
            # repeats states would replace rather than append).
            states = tbl.findall("state")
            if states:
                e["states"] = [(s.get("name"), s.get("data")) for s in states]
            bits = tbl.findall("bit")
            if bits:
                e["bits"] = [(b.get("name"), _dec(b.get("position"))) for b in bits]
            # child <table> nodes are axes, keyed by ROLE (X/Y) -- never by name.
            for ax in tbl.findall("table"):
                role = _role(ax.get("type"))
                if role is None:
                    continue
                a = e["axes"].setdefault(role, {})
                _merge_axis(a, ax)
    return eff, ch


def _table_type(raw: str | None) -> str:
    if raw in _TABLE_TYPES:
        return raw
    raise DefinitionError(f"unknown/missing table type {raw!r}")


def _build_table(e: dict) -> TableDef:
    ttype = _table_type(e.get("type"))
    axes = e.get("axes", {})
    nm = e["name"]
    x_axis = _build_axis(axes["X"], "X", _dec(e.get("sizex")), _dec(e.get("sizey")), nm) if "X" in axes else None
    y_axis = _build_axis(axes["Y"], "Y", _dec(e.get("sizex")), _dec(e.get("sizey")), nm) if "Y" in axes else None

    userlevel = e.get("userlevel")
    locked = e.get("locked")

    return TableDef(
        name=e["name"],
        type=ttype,  # type: ignore[arg-type]
        category=e.get("category"),
        storage_address=_hexint(e.get("storageaddress")),
        storage_type=e.get("storagetype"),
        endian=e.get("endian"),
        size_x=_dec(e.get("sizex")),
        size_y=_dec(e.get("sizey")),
        scale=e.get("scale"),
        x_axis=x_axis,
        y_axis=y_axis,
        description=e.get("description"),
        states=tuple(e.get("states") or ()),
        bits=tuple(e.get("bits") or ()),
        logparam=e.get("logparam"),
        user_level=int(userlevel) if userlevel is not None else 1,
        locked=(locked == "true") if locked is not None else False,
        swap_xy=str(e.get("swapxy", "false")).lower() == "true",
        flip_x=str(e.get("flipx", "false")).lower() == "true",
        flip_y=str(e.get("flipy", "false")).lower() == "true",
    )


def _checksum_type(by_xid: dict[str, list[ET.Element]], chain: list[str]) -> str | None:
    """Optional <checksum type="..."> child of the resolved rom, or its base chain
    (most-derived layer that declares one wins). None when absent (MS41 defs)."""
    result: str | None = None
    for layer in chain:
        rom_el = pick_rom(by_xid, layer)
        if rom_el is None:
            continue
        cs = rom_el.find("checksum")
        if cs is not None and cs.get("type") is not None:
            result = cs.get("type")
    return result


def _rom_id_of(rom_el: ET.Element) -> RomId:
    rid = rom_el.find("romid")
    mm = rid.find("memmodel") if rid is not None else None
    return RomId(
        xmlid=rid.findtext("xmlid") or "" if rid is not None else "",
        internal_id_address=_hexint(rid.findtext("internalidaddress")) if rid is not None else None,
        internal_id_string=rid.findtext("internalidstring") if rid is not None else None,
        ecuid=rid.findtext("ecuid") if rid is not None else None,
        filesize=_filesize(rid.findtext("filesize")) if rid is not None else None,
        memmodel=mm.text if mm is not None else None,
        memmodel_endian=mm.get("endian") if mm is not None else None,
        no_ram_offset=(rid.find("noramoffset") is not None) if rid is not None else False,
    )


def resolve_rom(nodes_by_xmlid: dict[str, list[ET.Element]], xmlid: str) -> RomDefinition:
    eff, chain = resolve_tables(nodes_by_xmlid, xmlid)
    tables: dict[str, TableDef] = {}
    for name, e in eff.items():
        try:
            tables[name] = _build_table(e)
        except DefinitionError as exc:
            # Mirrors ms41def.py's own tolerance (cmd_list defaults a missing type to "?"
            # rather than aborting): a stub <table> entry that never picks up a type
            # anywhere in the chain can't become a valid TableDef, so it is dropped
            # instead of failing the whole rom resolution (real corpus: the
            # 'MAF Plausibility Check' stubs in xmlids 41/60).
            log.warning("dropping table %r while resolving rom %r: %s", name, xmlid, exc)
            continue
    leaf = pick_rom(nodes_by_xmlid, xmlid)
    if leaf is None:
        raise DefinitionError(f"no <rom> with xmlid {xmlid!r}")
    return RomDefinition(
        romid=_rom_id_of(leaf),
        tables=tables,
        checksum_type=_checksum_type(nodes_by_xmlid, chain),
    )
