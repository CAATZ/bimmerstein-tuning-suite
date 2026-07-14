from __future__ import annotations
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QWidget)

from ecueditor.core.loggerdef.channel import LoggerChannel
from ecueditor.core.logger.engine import Sample
from ecueditor.core.logger.recorder import CsvRecorder


class CsvLogSession:
    """Manage one CSV recorder and its current output filename."""
    def __init__(self, out_dir: Path) -> None:
        self._out_dir = Path(out_dir)
        self._recorder: CsvRecorder | None = None
        self._path: Path | None = None

    @property
    def is_active(self) -> bool:
        return self._recorder is not None

    @property
    def out_dir(self) -> Path:
        return self._out_dir

    @out_dir.setter
    def out_dir(self, value: Path | str) -> None:
        self._out_dir = Path(value)

    def start(self, channels: Sequence[LoggerChannel], *, absolute_time: bool,
              name_infix: str = "") -> Path:
        self._recorder = CsvRecorder(self._out_dir, absolute_time=absolute_time,
                                     name_infix=name_infix)
        self._path = self._recorder.start(list(channels))
        return self._path

    def current_filename(self) -> str:
        return self._path.name if self._path is not None else ""

    def on_sample(self, sample: Sample) -> None:
        if self._recorder is not None:
            self._recorder.write(sample)

    def stop(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None
        self._path = None


class LogControlsBar(QWidget):
    startRequested = Signal(str, bool)     # (name_infix, absolute_time)
    stopRequested = Signal()
    switchTriggerChanged = Signal(bool, str)   # (enabled, switch_channel_id)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.log_button = QPushButton("Start File Logging")
        self.log_button.setCheckable(True)
        self.name_infix_edit = QLineEdit()
        self.name_infix_edit.setPlaceholderText("log name infix")
        self.absolute_time_check = QCheckBox("Absolute time")
        self.switch_trigger_check = QCheckBox("Switch trigger")
        self.switch_combo = QComboBox()
        self._logging = False

        row = QHBoxLayout(self)
        row.addWidget(self.log_button)
        row.addWidget(QLabel("Name:"))
        row.addWidget(self.name_infix_edit)
        row.addWidget(self.absolute_time_check)
        row.addWidget(self.switch_trigger_check)
        row.addWidget(self.switch_combo)
        row.addStretch(1)

        self.log_button.clicked.connect(self.toggle_logging)
        self.switch_trigger_check.toggled.connect(self._emit_switch_trigger)
        self.switch_combo.currentTextChanged.connect(lambda _: self._emit_switch_trigger())

    @property
    def is_logging(self) -> bool:
        return self._logging

    def set_switch_channels(self, channels: Sequence[LoggerChannel]) -> None:
        self.switch_combo.clear()
        self.switch_combo.addItems([c.id for c in channels])

    def toggle_logging(self) -> None:
        self._logging = not self._logging
        if self._logging:
            self.log_button.setText("Stop File Logging")
            self.log_button.setChecked(True)
            self.startRequested.emit(self.name_infix_edit.text(),
                                     self.absolute_time_check.isChecked())
        else:
            self.log_button.setText("Start File Logging")
            self.log_button.setChecked(False)
            self.stopRequested.emit()

    def _emit_switch_trigger(self) -> None:
        self.switchTriggerChanged.emit(self.switch_trigger_check.isChecked(),
                                       self.switch_combo.currentText())
