"""Status-bar chips (spec §4; chip QSS kinds from design/qss.py)."""
from __future__ import annotations
from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout


class Chip(QLabel):
    def __init__(self, text: str = "", kind: str = "neutral", parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("chipKind", kind)

    def set_kind(self, kind: str) -> None:
        self.setProperty("chipKind", kind)
        style = self.style()
        style.unpolish(self); style.polish(self)          # re-evaluate the attribute selector


def _chip_for_region(r) -> Chip:
    name = "Cal" if r.name == "Calibration" else r.name
    if r.name == "Verify switch":
        text = {"on": "Verify ON", "off": "Verify OFF"}.get(r.status, "Verify unknown")
        kind = {"on": "ok", "off": "warn"}.get(r.status, "neutral")
    elif r.status == "ok":
        text = f"{name} {r.detail} ✓" if r.name == "Calibration" else f"{name} ✓"
        kind = "ok"
    elif r.status == "mismatch":
        text = f"{name} {r.detail} ✗" if r.name == "Calibration" else f"{name} ✗"
        kind = "danger"
    else:  # n/a
        text = f"{name} n/a (MS41.3)" if "MS41.3" in r.detail else f"{name} n/a"
        kind = "neutral"
    chip = Chip(text, kind)
    chip.setToolTip(r.detail)
    return chip


class ChecksumChips(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0); self._lay.setSpacing(4)

    def set_report(self, report) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        if report is None:
            self._lay.addWidget(Chip("No ROM", "neutral"))
            return
        for region in report.regions:
            self._lay.addWidget(_chip_for_region(region))

    def chip_texts(self) -> list[str]:
        out: list[str] = []
        for i in range(self._lay.count()):
            item = self._lay.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, QLabel):          # all chips are QLabel (Chip); narrows for .text()
                out.append(w.text())
        return out
