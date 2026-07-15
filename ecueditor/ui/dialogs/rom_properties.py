from __future__ import annotations
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLabel, QDialogButtonBox


class RomPropertiesDialog(QDialog):
    def __init__(self, rom, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ROM Properties")
        rid = rom.definition.romid
        report = rom.checksum_report()
        rows = [
            ("File", str(rom.path) if rom.path else "(unsaved)"),
            ("Definition (xmlid)", rid.xmlid),
            ("ECU id", rid.ecuid or "—"),
            ("File size", f"{len(rom.data)} bytes"),
            ("Memory model", rom.memory_model.name),
            ("Endian", rom.endian_default),
            ("Checksum", getattr(rom.checksum_manager, "name", "none")
                         + ("" if report is None else (" — OK" if report.ok else " — FAILED"))),
            ("Tables", str(len(rom.definition.tables))),
            ("Unsaved changes", "yes" if rom.is_dirty() else "no"),
        ]
        lay = QVBoxLayout(self)
        form = QFormLayout(); lay.addLayout(form)
        self._rows = rows
        for label, value in rows:
            v = QLabel(value); v.setTextInteractionFlags(
                v.textInteractionFlags() | v.textInteractionFlags().TextSelectableByMouse)
            form.addRow(label, v)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept); lay.addWidget(bb)

    def summary_text(self) -> str:
        return "\n".join(f"{k}: {v}" for k, v in self._rows)
