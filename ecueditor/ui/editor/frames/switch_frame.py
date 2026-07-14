"""Switch-table frame: styled preset radios + tolerant hex + save resync (spec §5, B6)."""
from __future__ import annotations
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QRadioButton, QButtonGroup, QLabel
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.ui.editor.frames.header import FrameHeader


def _parse_state_hex(data: str) -> bytes:
    """Forgiving, best-effort state-data parse: strip spaces; odd-length gets a leading zero;
    on any remaining garbage return the longest even hex prefix (B6). This concatenates the
    whole string and parses it as one hex blob, whereas core's active_state splits per-token;
    the two agree for real uniform 2-hex-digit tokens and diverge only on malformed
    1-digit tokens."""
    s = data.replace(" ", "")
    if len(s) % 2:
        s = "0" + s
    try:
        return bytes.fromhex(s)
    except ValueError:
        for end in range(len(s) - 2, 0, -2):
            try:
                return bytes.fromhex(s[:end])
            except ValueError:
                continue
        return b""


class SwitchFrame(QWidget):
    grid = None                                   # contract: no grid on switch frames
    edited = Signal()                             # dirty-tracking funnel (grid-None frames)

    def __init__(self, table, parent=None, **_ignored) -> None:
        super().__init__(parent)
        self.setObjectName("tableFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._table = table
        self.header = FrameHeader(table.definition, warning_style=True)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QRadioButton] = {}
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(self.header)
        body = QVBoxLayout(); body.setContentsMargins(14, 8, 14, 12); body.setSpacing(6)
        dim = current_theme().text_disabled
        for name, data in table.definition.states:
            btn = QRadioButton(f"{name}  ({data})")            # radio text stays UI font
            btn.setToolTip(f"raw bytes: {data}")
            self._group.addButton(btn); body.addWidget(btn)
            self._buttons[name] = btn
            btn.toggled.connect(lambda on, n=name: self._apply(n) if on else None)
        hint = QLabel("Selecting a state writes its bytes; Undo All reverts.")
        hint.setStyleSheet(f"color: {dim};")
        body.addWidget(hint)
        host = QWidget(); host.setLayout(body)
        lay.addWidget(host); lay.addStretch(1)
        self.resync_from_table()

    # --- api -----------------------------------------------------------------
    def buttons(self) -> list[QRadioButton]:
        return list(self._buttons.values())

    def check_state(self, name: str) -> None:
        self._buttons[name].setChecked(True)

    def checked_state_name(self) -> str | None:
        for name, btn in self._buttons.items():
            if btn.isChecked():
                return name
        return None

    def active_state_name(self) -> str | None:
        return self._table.active_state()

    def resync_from_table(self) -> None:
        active = self._table.active_state()
        for name, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.setChecked(name == active)
            btn.blockSignals(False)

    def _apply(self, name: str) -> None:
        payload = _parse_state_hex(dict(self._table.definition.states)[name])
        for i, byte in enumerate(payload):
            if i < len(self._table.cells):
                self._table.cells[i].set_raw(byte, clamp=True)
        self.edited.emit()                        # notify the shell's dirty tracking


class BitwiseSwitchFrame(QWidget):
    grid = None
    edited = Signal()                             # dirty-tracking funnel (grid-None frames)

    def __init__(self, table, parent=None, **_ignored) -> None:
        super().__init__(parent)
        self.setObjectName("tableFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        from PySide6.QtWidgets import QCheckBox
        self._table = table
        self.header = FrameHeader(table.definition, warning_style=True)
        self._boxes: dict[str, "QCheckBox"] = {}
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(self.header)
        body = QVBoxLayout(); body.setContentsMargins(14, 8, 14, 12); body.setSpacing(6)
        for name, position in table.definition.bits:
            box = QCheckBox(f"{name}  (bit {position})")
            box.setChecked(table.bit_value(name))
            box.toggled.connect(lambda on, n=name: self._set_bit(n, on))
            self._boxes[name] = box; body.addWidget(box)
        host = QWidget(); host.setLayout(body)
        lay.addWidget(host); lay.addStretch(1)

    def _set_bit(self, name: str, on: bool) -> None:
        self._table.set_bit(name, on)
        self.edited.emit()                        # notify the shell's dirty tracking

    def checkbox(self, name: str):
        return self._boxes[name]

    def resync_from_table(self) -> None:
        for name, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(self._table.bit_value(name))
            box.blockSignals(False)
