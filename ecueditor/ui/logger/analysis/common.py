from __future__ import annotations
from PySide6 import QtWidgets


def confirm_reapply_dialog(parent: QtWidgets.QWidget, text: str) -> bool:
    """Modal yes/no for the apply-once guard (H1). Tabs hold `confirm_reapply` as an injectable
    callable so tests (and scripted use) never pop a dialog."""
    btn = QtWidgets.QMessageBox.question(parent, "Apply again?", text)
    return btn == QtWidgets.QMessageBox.StandardButton.Yes
