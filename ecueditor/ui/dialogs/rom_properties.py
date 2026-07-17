from __future__ import annotations
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLabel, QDialogButtonBox


class RomPropertiesDialog(QDialog):
    def __init__(self, rom, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ROM Properties")
        rid = rom.definition.romid
        report = rom.checksum_report()
        definitions = ", ".join(
            f"{section.definition.romid.xmlid} ({section.label})"
            for section in rom.sections
        )
        memory_models = ", ".join(
            f"{section.memory_model.name} ({section.label})"
            for section in rom.sections
        )
        endians = ", ".join(
            f"{section.definition.romid.memmodel_endian or 'little'} ({section.label})"
            for section in rom.sections
        )
        table_count = sum(len(rom.section_definitions(section.key)) for section in rom.sections)
        rows = [
            ("File", str(rom.path) if rom.path else "(unsaved)"),
            ("Definition (xmlid)", definitions),
            ("ECU id", rid.ecuid or "—"),
            ("File size", f"{len(rom.data)} bytes"),
            ("Memory model", memory_models),
            ("Endian", endians),
            ("Checksum", getattr(rom.checksum_manager, "name", "none")
                         + ("" if report is None else (" — OK" if report.ok else " — FAILED"))),
            ("Tables", str(table_count)),
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
