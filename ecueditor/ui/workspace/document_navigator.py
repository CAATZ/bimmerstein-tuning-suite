"""Persistent open-document navigator for the MDI workspace."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QTabBar, QToolButton, QWidget

from ecueditor.ui.workspace.document_area import DocumentArea


class DocumentNavigator(QWidget):
    """A thin tab strip that switches MDI children without replacing their window model."""

    def __init__(self, documents: DocumentArea, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("documentNavigator")
        self._documents_area = documents
        self._documents: list[QWidget] = []

        self.tabs = QTabBar(self)
        self.tabs.setObjectName("documentTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setMovable(False)
        self.tabs.setTabsClosable(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideMiddle)

        self.restore_button = QToolButton(self)
        self.restore_button.setObjectName("restoreDocumentButton")
        self.restore_button.setText("Restore")
        self.restore_button.setToolTip("Restore the active window")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.restore_button)

        self.tabs.currentChanged.connect(self._activate_index)
        self.tabs.tabCloseRequested.connect(self._close_index)
        self.restore_button.clicked.connect(documents.restore_active_document)
        documents.documentCountChanged.connect(self._rebuild)
        documents.activeDocumentChanged.connect(self._sync_active)
        documents.documentTitleChanged.connect(self._sync_title)
        documents.workspaceModeChanged.connect(self._sync_mode)
        self._rebuild()
        self._sync_mode(documents.workspace_mode())

    def _rebuild(self, _count: int | None = None) -> None:
        active = self._documents_area.active_document()
        self._documents = self._documents_area.documents()
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(self.tabs.count() - 1)
        for document in self._documents:
            window = self._documents_area.window_for_document(document)
            icon = window.windowIcon() if window is not None else None
            title = self._documents_area.document_title(document)
            if icon is not None and not icon.isNull():
                self.tabs.addTab(icon, title)
            else:
                self.tabs.addTab(title)
            self.tabs.setTabToolTip(self.tabs.count() - 1, title.removesuffix(" ●"))
        if active in self._documents:
            self.tabs.setCurrentIndex(self._documents.index(active))
        self.tabs.blockSignals(False)
        has_documents = bool(self._documents)
        self.restore_button.setEnabled(has_documents)
        self.setVisible(has_documents)

    def _activate_index(self, index: int) -> None:
        if 0 <= index < len(self._documents):
            self._documents_area.set_active_document(self._documents[index])

    def _close_index(self, index: int) -> None:
        if 0 <= index < len(self._documents):
            self._documents_area.close_document(self._documents[index])

    def _sync_active(self, document: QWidget | None) -> None:
        if document not in self._documents:
            return
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(self._documents.index(document))
        self.tabs.blockSignals(False)

    def _sync_title(self, document: QWidget, title: str) -> None:
        if document not in self._documents:
            return
        index = self._documents.index(document)
        self.tabs.setTabText(index, title)
        self.tabs.setTabToolTip(index, title.removesuffix(" ●"))

    def _sync_mode(self, mode: str) -> None:
        focused = mode == "focus"
        self.restore_button.setText("Exit Focus" if focused else "Restore")
        self.restore_button.setToolTip(
            "Return to the previous workspace" if focused else "Restore the active window"
        )
