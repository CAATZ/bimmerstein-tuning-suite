from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class ProfileEntry:
    id: str
    units: str | None = None
    livedata: bool = False
    graph: bool = False
    dash: bool = False


@dataclass
class LoggerProfile:
    protocol: str | None = None
    port: str | None = None
    logfile_dir: str | None = None
    parameters: list[ProfileEntry] = field(default_factory=list)
    switches: list[ProfileEntry] = field(default_factory=list)
    externals: list[ProfileEntry] = field(default_factory=list)


def _entry_el(parent: ET.Element, tag: str, e: ProfileEntry) -> None:
    el = ET.SubElement(parent, tag, {"id": e.id})
    if e.units:
        el.set("units", e.units)
    for flag, on in (("livedata", e.livedata), ("graph", e.graph), ("dash", e.dash)):
        if on:
            el.set(flag, "selected")


def save_profile(path: str | Path, profile: LoggerProfile) -> None:
    root = ET.Element("profile")
    if profile.protocol:
        root.set("protocol", profile.protocol)
    if profile.port:
        ET.SubElement(root, "serial", {"port": profile.port})
    if profile.logfile_dir:
        ET.SubElement(root, "logfilelocation", {"dir": profile.logfile_dir})
    for tag, entries in (("parameters", profile.parameters),
                         ("switches", profile.switches),
                         ("externals", profile.externals)):
        if entries:
            container = ET.SubElement(root, tag)
            # explicit map — tag[:-1] would mangle "switches" into "switche"
            child = {"parameters": "parameter", "switches": "switch",
                     "externals": "external"}[tag]
            for e in entries:
                _entry_el(container, child, e)
    ET.ElementTree(root).write(Path(path), encoding="utf-8", xml_declaration=True)


def _parse_entries(container: ET.Element | None) -> list[ProfileEntry]:
    out: list[ProfileEntry] = []
    if container is None:
        return out
    for el in container:
        out.append(ProfileEntry(
            id=el.get("id", ""),
            units=el.get("units"),
            livedata=el.get("livedata") == "selected",
            graph=el.get("graph") == "selected",
            dash=el.get("dash") == "selected",
        ))
    return out


def load_profile(path: str | Path) -> LoggerProfile:
    root = ET.parse(Path(path)).getroot()
    serial = root.find("serial")
    logloc = root.find("logfilelocation")
    return LoggerProfile(
        protocol=root.get("protocol"),
        port=serial.get("port") if serial is not None else None,
        logfile_dir=logloc.get("dir") if logloc is not None else None,
        parameters=_parse_entries(root.find("parameters")),
        switches=_parse_entries(root.find("switches")),
        externals=_parse_entries(root.find("externals")),
    )


def apply_profile(panel, profile: LoggerProfile) -> None:
    """Apply profile selections to the poll and presentation views."""
    for e in (*profile.parameters, *profile.switches, *profile.externals):
        if not (e.livedata or e.graph or e.dash):
            continue
        try:
            panel.check(e.id)
        except KeyError:
            continue
        for view, on in (("livedata", e.livedata), ("graph", e.graph), ("dash", e.dash)):
            panel.set_view_checked(e.id, view, on)


def profile_from_panel(panel, *, port: str | None = None,
                       logfile_dir: str | None = None) -> LoggerProfile:
    """Snapshot the selection panel into a RomRaider-shaped logger profile."""
    views = {v: set(panel.view_ids(v)) for v in ("livedata", "graph", "dash")}
    def entries(pane) -> list[ProfileEntry]:
        return [ProfileEntry(id=cid, units=panel.units_for(cid),
                             livedata=cid in views["livedata"],
                             graph=cid in views["graph"], dash=cid in views["dash"])
                for cid in pane.selected_ids()]
    return LoggerProfile(port=port, logfile_dir=logfile_dir,
                         parameters=entries(panel._parameters),
                         switches=entries(panel._switches),
                         externals=entries(panel._externals))
