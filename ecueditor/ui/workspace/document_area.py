"""Movable internal-window workspace for table and 3D documents."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QMdiArea, QMdiSubWindow, QStyle, QWidget

from ecueditor.ui.design.theme_manager import current_theme


@dataclass(frozen=True)
class _ConstrainedWindowSize:
    """Remember a window's intended size while the MDI canvas clips it."""

    desired: QSize
    applied: QSize
    content_driven: bool


class _DocumentSubWindow(QMdiSubWindow):
    """Route native title-bar closes through DocumentArea bookkeeping."""

    closeRequested = Signal(object)

    def _sync_chrome_palette(self, theme=None, *, force: bool = False) -> None:
        """Theme native MDI chrome without attaching a costly QSS rule to the window."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        active_theme = theme or current_theme()
        custom = bool(app.styleSheet())
        key = (custom, id(active_theme), app.palette().cacheKey())
        if not force and getattr(self, "_chrome_palette_key", None) == key:
            return
        self._chrome_palette_key = key
        palette = QPalette(app.palette())
        if custom:
            for group, background in (
                (QPalette.ColorGroup.Active, active_theme.surface2),
                (QPalette.ColorGroup.Inactive, active_theme.surface1),
                (QPalette.ColorGroup.Disabled, active_theme.surface1),
            ):
                palette.setColor(group, QPalette.ColorRole.Highlight, QColor(background))
                palette.setColor(
                    group, QPalette.ColorRole.HighlightedText, QColor(active_theme.text)
                )
            palette.setColor(QPalette.ColorRole.Window, QColor(active_theme.surface1))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(active_theme.text))
        self.setPalette(palette)

    def _outline_color(self) -> QColor | None:
        """Return the lightweight painter outline for active/Compare state."""
        theme = current_theme()
        if self.property("mdiActive") is True or self.property("compareRole") == "primary":
            return QColor(theme.accent)
        if self.property("compareRole") == "secondary":
            color = QColor(theme.accent)
            color.setAlphaF(0.55)
            return color
        return None

    def paintEvent(self, event) -> None:
        self._sync_chrome_palette()
        super().paintEvent(event)
        app = QApplication.instance()
        color = self._outline_color()
        if not isinstance(app, QApplication) or not app.styleSheet() or color is None:
            return
        painter = QPainter(self)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def closeEvent(self, event: QCloseEvent) -> None:
        document = self.widget()
        if document is None:
            event.accept()
            return
        event.ignore()
        self.closeRequested.emit(document)


class DocumentArea(QMdiArea):
    """RomRaider-style MDI canvas while keeping documents as ordinary widgets."""

    activeDocumentChanged = Signal(object)
    documentCountChanged = Signal(int)
    documentClosed = Signal(object)
    documentTitleChanged = Signal(object, str)
    workspaceModeChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("documentArea")
        self.setViewMode(QMdiArea.ViewMode.SubWindowView)
        self.setActivationOrder(QMdiArea.WindowOrder.ActivationHistoryOrder)
        self.setDocumentMode(True)
        self.setOption(QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation, True)
        # An MDI canvas is a bounded desktop, not a panning document. Scrollbars can shift a
        # large table window into negative coordinates when it is reactivated.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setObjectName("documentAreaViewport")
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.viewport().setAutoFillBackground(True)
        self._windows: dict[int, _DocumentSubWindow] = {}
        self._titles: dict[int, str] = {}
        self._dirty: set[int] = set()
        self._active: QWidget | None = None
        self._activation_history: list[int] = []
        self._compare_pair: tuple[int, int] | None = None
        self._workspace_mode = "studio"
        self._workspace_before_focus = "studio"
        self._studio_layout_before_focus: dict[int, tuple[QRect, Qt.WindowState]] = {}
        self._applying_layout = False
        self._pending_content_fit: dict[int, int] = {}
        self._content_fit_signatures: dict[int, tuple] = {}
        self._constrained_window_sizes: dict[int, _ConstrainedWindowSize] = {}
        self._fit_watch_owner: dict[int, int] = {}
        self._content_fit_timer = QTimer(self)
        self._content_fit_timer.setSingleShot(True)
        self._content_fit_timer.setInterval(0)
        self._content_fit_timer.timeout.connect(self._flush_content_fit)
        self.subWindowActivated.connect(self._on_subwindow_activated)

    # --- introspection -------------------------------------------------------
    def documents(self) -> list[QWidget]:
        documents: list[QWidget] = []
        for window in self._windows.values():
            document = window.widget()
            if document is not None:
                documents.append(document)
        return documents

    def active_document(self) -> QWidget | None:
        return self._active

    def window_for_document(self, document: QWidget) -> QMdiSubWindow | None:
        return self._windows.get(id(document))

    def document_title(self, document: QWidget) -> str:
        window = self._windows.get(id(document))
        return window.windowTitle() if window is not None else ""

    def workspace_mode(self) -> str:
        return self._workspace_mode

    def apply_theme(self, theme) -> None:
        """Set the MDI desktop brush, which Qt does not reliably take from QSS."""
        self.setBackground(QBrush(QColor(theme.bg)))
        self.viewport().update()
        for window in self._windows.values():
            window._sync_chrome_palette(theme, force=True)
            window.update()

    def compare_documents(self) -> tuple[QWidget, QWidget] | None:
        """Return the selected side-by-side pair while both documents remain open."""
        if self._compare_pair is None:
            return None
        first = self._windows.get(self._compare_pair[0])
        second = self._windows.get(self._compare_pair[1])
        if first is None or second is None:
            return None
        first_document, second_document = first.widget(), second.widget()
        if first_document is None or second_document is None:
            return None
        return first_document, second_document

    # --- membership ---------------------------------------------------------
    def add_document(
        self,
        document: QWidget,
        title: str,
        icon: QIcon | None = None,
        workspace_kind: str = "generic",
    ) -> None:
        key = id(document)
        if key in self._windows:
            self.set_active_document(document)
            return

        window = _DocumentSubWindow()
        window.setObjectName("documentSubWindow")
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        window.setWindowFlags(
            Qt.WindowType.SubWindow
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        window.setWidget(document)
        window.setWindowTitle(title)
        window.setToolTip(title)
        window.setProperty("documentKind", workspace_kind)
        if icon is not None:
            window.setWindowIcon(icon)
        window.closeRequested.connect(self.close_document)

        self._windows[key] = window
        self._titles[key] = title
        self.addSubWindow(window)
        window._sync_chrome_palette(force=True)
        desired_size = self._size_new_window(window, document)
        window.show()
        for watched in (document, *document.findChildren(QWidget)):
            watched.installEventFilter(self)
            self._fit_watch_owner[id(watched)] = key
        refined_size = self._refine_grid_window_size(window, document)
        if refined_size is not None:
            desired_size = desired_size.expandedTo(refined_size)
        self._remember_constrained_size(
            window,
            desired_size,
            content_driven=workspace_kind in {"grid", "utility", "mapstudio"},
        )
        self._schedule_content_fit(window, force=True)
        self.setActiveSubWindow(window)
        self._remember_activation(document)
        self._set_active(document)
        if self._workspace_mode == "studio":
            self._place_new_studio_window(window)
        else:
            self._apply_workspace_mode()
        self.documentCountChanged.emit(len(self._windows))

    @staticmethod
    def can_close_document(document: QWidget) -> bool:
        guard = getattr(document, "can_close", None)
        return bool(guard()) if callable(guard) else True

    def close_document(self, document: QWidget, *, force: bool = False) -> bool:
        key = id(document)
        window = self._windows.get(key)
        if window is None:
            return False
        if not force and not self.can_close_document(document):
            return False
        self._windows.pop(key)
        self._fit_watch_owner = {
            watched: owner
            for watched, owner in self._fit_watch_owner.items()
            if owner != key
        }
        self._content_fit_signatures.pop(id(window), None)
        self._pending_content_fit.pop(id(window), None)
        self._constrained_window_sizes.pop(id(window), None)

        was_active = document is self._active
        self._titles.pop(key, None)
        self._dirty.discard(key)
        if key in self._activation_history:
            self._activation_history.remove(key)
        if self._compare_pair is not None and key in self._compare_pair:
            self._compare_pair = None
        # Shell registries and integrations must release the live document while its Qt
        # children (notably a table grid registered with the logger overlay) still exist.
        self.documentClosed.emit(document)
        # Hide the complete native child before detaching its body. Otherwise Windows can paint
        # the now-empty QMdiSubWindow with its default white brush for one compositor frame.
        window.hide()
        document.hide()
        self.removeSubWindow(window)
        document.setParent(None)
        document.deleteLater()
        window.deleteLater()

        remaining = list(self._windows.values())
        if not remaining:
            self._set_active(None)
        elif was_active:
            next_window = remaining[-1]
            self.setActiveSubWindow(next_window)
            self._set_active(next_window.widget())
        if remaining and self._workspace_mode != "studio":
            self._apply_workspace_mode()
        self.documentCountChanged.emit(len(remaining))
        return True

    def close_active_document(self) -> None:
        if self._active is not None:
            self.close_document(self._active)

    def close_all_documents(self) -> None:
        for document in list(self.documents()):
            self.close_document(document)

    def set_active_document(self, document: QWidget) -> None:
        window = self._windows.get(id(document))
        if window is None:
            return
        if window.windowState() & Qt.WindowState.WindowMinimized:
            window.showNormal()
        self.setActiveSubWindow(window)
        window.raise_()
        self._remember_activation(document)
        self._set_active(document)

    def set_compare_documents(self, primary: QWidget, secondary: QWidget) -> None:
        """Choose an explicit pair and place it in the side-by-side Compare workspace."""
        primary_key, secondary_key = id(primary), id(secondary)
        if primary_key == secondary_key:
            return
        if primary_key not in self._windows or secondary_key not in self._windows:
            return
        self._compare_pair = (primary_key, secondary_key)
        self.set_active_document(primary)
        self.set_workspace_mode("compare")

    # --- title decoration ---------------------------------------------------
    def set_document_title(self, document: QWidget, title: str) -> None:
        key = id(document)
        if key not in self._windows:
            return
        self._titles[key] = title
        self._refresh_title(document)

    def set_document_dirty(self, document: QWidget, dirty: bool) -> None:
        key = id(document)
        if key not in self._windows:
            return
        if dirty:
            self._dirty.add(key)
        else:
            self._dirty.discard(key)
        self._refresh_title(document)

    def _refresh_title(self, document: QWidget) -> None:
        key = id(document)
        window = self._windows.get(key)
        if window is None:
            return
        title = self._titles[key]
        window.setWindowTitle(f"{title} ●" if key in self._dirty else title)
        window.setToolTip(title)
        self.documentTitleChanged.emit(document, window.windowTitle())

    # --- layout commands ----------------------------------------------------
    def set_workspace_mode(self, mode: str) -> None:
        normalized = mode if mode in {"studio", "compare", "focus"} else "studio"
        if normalized == "focus" and self._workspace_mode != "focus":
            self._workspace_before_focus = self._workspace_mode
            if self._workspace_mode == "studio":
                self._studio_layout_before_focus = {
                    key: (QRect(window.geometry()), window.windowState())
                    for key, window in self._windows.items()
                }
        changed = normalized != self._workspace_mode
        self._workspace_mode = normalized
        if normalized == "studio" and self._studio_layout_before_focus:
            self._restore_studio_layout_after_focus()
        else:
            self._apply_workspace_mode()
        if changed:
            self.workspaceModeChanged.emit(normalized)

    def _restore_studio_layout_after_focus(self) -> None:
        """Restore user-positioned Studio windows instead of recomputing a fresh layout."""
        if self._applying_layout:
            return
        self._applying_layout = True
        try:
            self._clear_compare_roles()
            for key, window in self._windows.items():
                saved = self._studio_layout_before_focus.get(key)
                window.showNormal()
                if saved is None:
                    self._place_new_studio_window(window)
                    continue
                geometry, state = saved
                self._set_bounded_geometry(
                    window,
                    geometry.x(),
                    geometry.y(),
                    geometry.width(),
                    geometry.height(),
                )
                if state & Qt.WindowState.WindowMinimized:
                    window.showMinimized()
                elif state & Qt.WindowState.WindowMaximized:
                    window.showMaximized()
            active_window = (
                self._windows.get(id(self._active)) if self._active is not None else None
            )
            if active_window is not None:
                self.setActiveSubWindow(active_window)
                active_window.raise_()
        finally:
            self._studio_layout_before_focus = {}
            self._applying_layout = False

    def toggle_focus(self) -> None:
        """Enter Focus, or return to the workspace that was active before Focus."""
        if self._workspace_mode == "focus":
            self.set_workspace_mode(self._workspace_before_focus)
        else:
            self.set_workspace_mode("focus")

    def restore_active_document(self) -> None:
        """Expose an unambiguous restore path for Focus and manually maximized children."""
        if self._workspace_mode == "focus":
            self.set_workspace_mode(self._workspace_before_focus)
            return
        active_window = (
            self._windows.get(id(self._active)) if self._active is not None else None
        )
        if active_window is not None:
            active_window.showNormal()

    def tile_documents(self) -> None:
        self.set_workspace_mode("studio")
        self._show_all_normal()
        self.tileSubWindows()

    def cascade_documents(self) -> None:
        self.set_workspace_mode("studio")
        self._show_all_normal()
        self.cascadeSubWindows()

    def minimize_all_documents(self) -> None:
        for window in self._windows.values():
            window.showMinimized()

    def restore_all_documents(self) -> None:
        self.set_workspace_mode("studio")

    def activate_next_document(self) -> None:
        if len(self._windows) > 1:
            self.activateNextSubWindow()

    def activate_previous_document(self) -> None:
        if len(self._windows) > 1:
            self.activatePreviousSubWindow()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._windows and self._workspace_mode in {"compare", "focus"}:
            self._apply_workspace_mode()
        elif self._windows:
            self._bound_studio_windows()

    # --- internals ----------------------------------------------------------
    def _size_new_window(self, window: QMdiSubWindow, document: QWidget) -> QSize:
        # Keep an emergency lower bound for constrained canvases; ordinary Studio geometry is
        # measured from the document's complete current content hint and native window chrome.
        window.setMinimumSize(280, 160)
        kind = str(window.property("documentKind") or "generic")
        minimum = {
            "grid": QSize(340, 160),
            "surface": QSize(500, 320),
            "utility": QSize(340, 140),
            "mapstudio": QSize(680, 420),
        }.get(kind, QSize(420, 280))
        preferred = self._preferred_window_size(window, document, minimum)
        preferred = self._allow_for_constrained_grid_scrollbars(preferred, document)
        available = self.viewport().size()
        max_width = max(280, available.width() - 24)
        max_height = max(160, available.height() - 24)
        window.resize(
            min(preferred.width(), max_width),
            min(preferred.height(), max_height),
        )
        return preferred

    @staticmethod
    def _preferred_window_size(
        window: QMdiSubWindow, document: QWidget, minimum: QSize
    ) -> QSize:
        """Return content plus the native style's actual MDI chrome dimensions."""
        document_hint = document.sizeHint()
        contents = window.contentsRect().size()
        chrome = QSize(
            max(0, window.width() - contents.width()),
            max(0, window.height() - contents.height()),
        )
        return document_hint.expandedTo(minimum) + chrome

    def _allow_for_constrained_grid_scrollbars(
        self, preferred: QSize, document: QWidget
    ) -> QSize:
        """Reserve a scrollbar only when the workspace will actually constrain that axis."""
        grid = getattr(document, "grid", None)
        if grid is None:
            return preferred
        canvas_width, canvas_height = self._canvas_dimensions()
        max_width = max(280, canvas_width - 12)
        max_height = max(160, canvas_height - 12)
        extent = grid.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        base_width, base_height = preferred.width(), preferred.height()
        vertical_needed = base_height > max_height
        horizontal_needed = base_width > max_width
        for _iteration in range(3):
            width = base_width + (extent if vertical_needed else 0)
            height = base_height + (extent if horizontal_needed else 0)
            updated_vertical = height > max_height
            updated_horizontal = width > max_width
            if (updated_vertical, updated_horizontal) == (
                vertical_needed, horizontal_needed
            ):
                return QSize(width, height)
            vertical_needed, horizontal_needed = updated_vertical, updated_horizontal
        return QSize(width, height)

    def _place_new_studio_window(self, window: QMdiSubWindow) -> None:
        """Place only the new Studio window; existing user geometry is authoritative."""
        kind = str(window.property("documentKind") or "generic")
        peers = [
            candidate for candidate in self._windows.values()
            if str(candidate.property("documentKind") or "generic") == kind
        ]
        index = max(0, len(peers) - 1)
        canvas_width, canvas_height = self._canvas_dimensions()
        win_w, win_h = window.width(), window.height()
        if kind == "grid":
            x, y = 12 + index * 38, 12 + index * 34
        elif kind == "surface":
            x = canvas_width - win_w - 12 - index * 26
            y = 42 + index * 30
        elif kind == "utility":
            x = 12 if index % 2 == 0 else canvas_width - win_w - 12
            y = canvas_height - win_h - 12 - (index // 2) * 30
        elif kind == "mapstudio":
            x = (canvas_width - win_w) // 2 + index * 24
            y = (canvas_height - win_h) // 2 + index * 24
        else:
            x, y = 18 + index * 34, 18 + index * 30
        self._set_bounded_geometry(window, x, y, win_w, win_h)

    def fit_studio_windows_to_content(self) -> None:
        """Refit supported Studio documents without changing their positions."""
        if self._workspace_mode != "studio":
            return
        for window in self._windows.values():
            self._fit_studio_window_to_content(window)
            self._schedule_content_fit(window, force=True)

    def _fit_studio_window_to_content(self, window: QMdiSubWindow) -> None:
        preferred = self._content_fit_preferred_size(window)
        if preferred is None:
            return
        document = window.widget()
        desired = QSize(preferred)
        self._set_bounded_geometry(
            window, window.x(), window.y(), preferred.width(), preferred.height()
        )
        if document is not None:
            refined = self._refine_grid_window_size(window, document)
            if refined is not None:
                desired = desired.expandedTo(refined)
        self._remember_constrained_size(window, desired, content_driven=True)
        self._content_fit_signatures[id(window)] = self._content_fit_signature(window)

    def _content_fit_preferred_size(self, window: QMdiSubWindow) -> QSize | None:
        kind = str(window.property("documentKind") or "generic")
        if kind not in {"grid", "utility", "mapstudio"}:
            return None
        document = window.widget()
        minimum = {
            "grid": QSize(340, 160),
            "utility": QSize(340, 140),
            "mapstudio": QSize(680, 420),
        }[kind]
        preferred = (
            self._preferred_window_size(window, document, minimum)
            if document is not None else minimum
        )
        if document is not None:
            preferred = self._allow_for_constrained_grid_scrollbars(preferred, document)
        return preferred

    def _remember_constrained_size(
        self,
        window: QMdiSubWindow,
        desired: QSize,
        *,
        content_driven: bool,
    ) -> None:
        """Retain an intended size only while the current canvas cannot provide it."""
        key = id(window)
        requested = QSize(desired)
        applied = QSize(window.size())
        if requested == applied:
            self._constrained_window_sizes.pop(key, None)
            return
        self._constrained_window_sizes[key] = _ConstrainedWindowSize(
            requested,
            applied,
            content_driven,
        )

    def _content_fit_signature(self, window: QMdiSubWindow) -> tuple:
        document = window.widget()
        if document is None:
            return ()
        hint = document.sizeHint()
        grid = getattr(document, "grid", None)
        if grid is None or grid.model() is None:
            return (hint.width(), hint.height())
        return (
            hint.width(),
            hint.height(),
            tuple(getattr(grid, "_natural_widths", ())),
            tuple(grid.rowHeight(row) for row in range(grid.model().rowCount())),
        )

    def _schedule_content_fit(
        self, window: QMdiSubWindow, *, force: bool = False, passes: int = 2
    ) -> None:
        key = id(window)
        if not force and self._content_fit_signatures.get(key) == \
                self._content_fit_signature(window):
            return
        self._pending_content_fit[key] = max(passes, self._pending_content_fit.get(key, 0))
        if not self._content_fit_timer.isActive():
            self._content_fit_timer.start()

    def _flush_content_fit(self) -> None:
        pending = self._pending_content_fit
        self._pending_content_fit = {}
        if self._workspace_mode != "studio":
            return
        for window in self._windows.values():
            key = id(window)
            passes = pending.get(key, 0)
            if passes:
                self._fit_studio_window_to_content(window)
                if passes > 1:
                    self._pending_content_fit[key] = passes - 1
        if self._pending_content_fit:
            self._content_fit_timer.start()

    def eventFilter(self, watched, event) -> bool:
        # Qt can dispatch events through this override while QMdiArea itself is
        # still being constructed, before our fit-watcher state exists.
        owner = getattr(self, "_fit_watch_owner", {}).get(id(watched))
        if owner is not None and event.type() == QEvent.Type.LayoutRequest \
                and self._workspace_mode == "studio":
            window = self._windows.get(owner)
            if window is not None:
                self._schedule_content_fit(window, passes=1)
        return super().eventFilter(watched, event)

    def _refine_grid_window_size(
        self, window: QMdiSubWindow, document: QWidget
    ) -> QSize | None:
        """Resolve any real one-pixel layout/scrollbar feedback from the polished grid."""
        grid = getattr(document, "grid", None)
        if grid is None or not hasattr(grid, "_natural_widths"):
            return None
        layout = document.layout()
        if layout is not None:
            layout.activate()
        grid.updateGeometries()
        model = grid.model()
        if model is None:
            return None
        # Measure the viewport that cells can actually use.  Adding visible
        # scrollbar extents back here creates a false fixed point: horizontal
        # and vertical bars can each consume just enough room to keep the other
        # visible even though the MDI canvas has space for the complete table.
        base_width = grid.viewport().width()
        base_height = grid.viewport().height()
        desired_width = sum(grid._natural_widths)
        desired_height = sum(grid.rowHeight(row) for row in range(model.rowCount()))
        grow_width = max(0, desired_width - base_width)
        grow_height = max(0, desired_height - base_height)
        if not grow_width and not grow_height:
            return None
        requested = QSize(
            window.width() + grow_width,
            window.height() + grow_height,
        )
        self._set_bounded_geometry(
            window,
            window.x(),
            window.y(),
            requested.width(),
            requested.height(),
        )
        grid.updateGeometries()
        return requested

    def _bound_studio_windows(self) -> None:
        """Keep windows reachable and restore sizes previously clipped by the canvas."""
        if self._applying_layout:
            return
        self._applying_layout = True
        try:
            for window in self._windows.values():
                state = window.windowState()
                if state & (
                    Qt.WindowState.WindowMinimized | Qt.WindowState.WindowMaximized
                ):
                    continue
                key = id(window)
                geometry = window.geometry()
                constrained = self._constrained_window_sizes.get(key)
                if constrained is not None and window.size() != constrained.applied:
                    # A size chosen after the last automatic fit belongs to the user.
                    self._constrained_window_sizes.pop(key, None)
                    constrained = None
                if constrained is not None and constrained.content_driven:
                    self._fit_studio_window_to_content(window)
                    continue
                desired = (
                    QSize(constrained.desired)
                    if constrained is not None
                    else QSize(geometry.size())
                )
                self._set_bounded_geometry(
                    window, geometry.x(), geometry.y(), desired.width(), desired.height()
                )
                self._remember_constrained_size(
                    window, desired, content_driven=False
                )
        finally:
            self._applying_layout = False

    def _apply_workspace_mode(self) -> None:
        if self._applying_layout or not self._windows:
            return
        self._applying_layout = True
        active = self._active
        try:
            if self._workspace_mode == "compare":
                self._layout_compare()
            elif self._workspace_mode == "focus":
                self._layout_focus()
            else:
                self._layout_studio()
            active_window = self._windows.get(id(active)) if active is not None else None
            if active_window is not None:
                self.setActiveSubWindow(active_window)
                active_window.raise_()
        finally:
            self._applying_layout = False

    def _layout_studio(self) -> None:
        self._clear_compare_roles()
        self._show_all_normal()
        width, height = self._canvas_dimensions()
        kind_counts = {
            "grid": 0,
            "surface": 0,
            "utility": 0,
            "mapstudio": 0,
            "generic": 0,
        }
        for window in self._windows.values():
            kind = str(window.property("documentKind") or "generic")
            if kind not in kind_counts:
                kind = "generic"
            index = kind_counts[kind]
            kind_counts[kind] += 1
            document = window.widget()
            minimum = {
                "grid": QSize(340, 160),
                "surface": QSize(720, 500),
                "utility": QSize(340, 140),
                "mapstudio": QSize(680, 420),
                "generic": QSize(420, 280),
            }[kind]
            preferred = (
                self._preferred_window_size(window, document, minimum)
                if document is not None else minimum
            )
            if document is not None:
                preferred = self._allow_for_constrained_grid_scrollbars(
                    preferred, document
                )
            preferred_width = preferred.width()
            preferred_height = preferred.height()
            if kind == "grid":
                win_w, win_h = preferred_width, preferred_height
                x, y = 12 + index * 38, 12 + index * 34
            elif kind == "surface":
                win_w, win_h = preferred_width, preferred_height
                x = width - win_w - 12 - index * 26
                y = 42 + index * 30
            elif kind == "utility":
                win_w, win_h = preferred_width, preferred_height
                x = 12 if index % 2 == 0 else width - win_w - 12
                y = height - win_h - 12 - (index // 2) * 30
            elif kind == "mapstudio":
                win_w, win_h = preferred_width, preferred_height
                x = (width - win_w) // 2 + index * 24
                y = (height - win_h) // 2 + index * 24
            else:
                win_w, win_h = preferred_width, preferred_height
                x, y = 18 + index * 34, 18 + index * 30
            desired = QSize(win_w, win_h)
            self._set_bounded_geometry(window, x, y, win_w, win_h)
            if document is not None:
                refined = self._refine_grid_window_size(window, document)
                if refined is not None:
                    desired = desired.expandedTo(refined)
            self._remember_constrained_size(
                window,
                desired,
                content_driven=kind in {"grid", "utility", "mapstudio"},
            )

    def _layout_compare(self) -> None:
        selected: list[_DocumentSubWindow] = []
        if self._compare_pair is not None:
            selected = [
                self._windows[key]
                for key in self._compare_pair
                if key in self._windows
            ]
        if len(selected) != 2:
            ordered = self._windows_by_recent_activation()
            selected = ordered[-2:]
            if len(selected) == 2:
                self._compare_pair = (id(selected[0].widget()), id(selected[1].widget()))
            else:
                self._compare_pair = None
        self._clear_compare_roles()
        if selected:
            self._set_compare_role(selected[0], "primary")
        if len(selected) > 1:
            self._set_compare_role(selected[1], "secondary")
        for window in self._windows.values():
            if window in selected:
                window.showNormal()
            else:
                window.showMinimized()
        width, height = self._canvas_dimensions()
        margin, gap = 8, 10
        if len(selected) == 1:
            self._set_bounded_geometry(
                selected[0], margin, margin, width - margin * 2, height - margin * 2
            )
            return
        pane_width = (width - margin * 2 - gap) // 2
        pane_height = height - margin * 2
        self._set_bounded_geometry(selected[0], margin, margin, pane_width, pane_height)
        self._set_bounded_geometry(
            selected[1], margin + pane_width + gap, margin, pane_width, pane_height
        )

    def _layout_focus(self) -> None:
        self._clear_compare_roles()
        active_window = self._windows.get(id(self._active)) if self._active is not None else None
        if active_window is None:
            active_window = self._windows_by_recent_activation()[-1]
            self._set_active(active_window.widget())
        for window in self._windows.values():
            if window is active_window:
                window.showNormal()
                window.showMaximized()
            else:
                window.showMinimized()

    def _show_all_normal(self) -> None:
        for window in self._windows.values():
            window.showNormal()

    def _canvas_dimensions(self) -> tuple[int, int]:
        return max(320, self.viewport().width()), max(220, self.viewport().height())

    def _set_bounded_geometry(
        self, window: QMdiSubWindow, x: int, y: int, width: int, height: int
    ) -> None:
        canvas_width, canvas_height = self._canvas_dimensions()
        bounded_width = max(280, min(width, canvas_width - 12))
        bounded_height = max(160, min(height, canvas_height - 12))
        bounded_x = max(0, min(x, canvas_width - bounded_width))
        bounded_y = max(0, min(y, canvas_height - bounded_height))
        window.setGeometry(bounded_x, bounded_y, bounded_width, bounded_height)

    def _remember_activation(self, document: QWidget) -> None:
        key = id(document)
        if key in self._activation_history:
            self._activation_history.remove(key)
        self._activation_history.append(key)

    def _windows_by_recent_activation(self) -> list[_DocumentSubWindow]:
        ordered = [
            self._windows[key]
            for key in self._activation_history
            if key in self._windows
        ]
        for key, window in self._windows.items():
            if key not in self._activation_history:
                ordered.append(window)
        return ordered

    def _set_compare_role(self, window: _DocumentSubWindow, role: str) -> None:
        if window.property("compareRole") == role:
            return
        window.setProperty("compareRole", role)
        window.update()

    def _clear_compare_roles(self) -> None:
        for window in self._windows.values():
            self._set_compare_role(window, "")

    def _on_subwindow_activated(self, window: QMdiSubWindow | None) -> None:
        if self._applying_layout:
            return
        document = window.widget() if window is not None else None
        previous = self._active
        if (
            document is not None
            and self._workspace_mode == "compare"
            and self._compare_pair is not None
            and id(document) not in self._compare_pair
        ):
            anchor = previous if previous is not None and id(previous) in self._compare_pair else None
            if anchor is None:
                pair = self.compare_documents()
                anchor = pair[0] if pair is not None else None
            if anchor is not None and anchor is not document:
                self._compare_pair = (id(anchor), id(document))
        if document is not None:
            self._remember_activation(document)
        self._set_active(document)
        if document is not None and self._workspace_mode in {"compare", "focus"}:
            self._apply_workspace_mode()

    def _set_active(self, document: QWidget | None) -> None:
        if document is self._active:
            return
        self._active = document
        for window in self._windows.values():
            is_active = window.widget() is document
            if window.property("mdiActive") is is_active:
                continue
            window.setProperty("mdiActive", is_active)
            window.update()
        self.activeDocumentChanged.emit(document)
