from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, Slot
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QTabWidget, QVBoxLayout, QWidget

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from ecueditor.core.mapstudio import (
    CollapsedMap,
    CurveData,
    MapData,
    MapValidationError,
    collapse_duplicate_map,
)
from ecueditor.ui.design.theme_manager import current_theme
from ecueditor.ui.editor.surface3d import (
    SURFACE_DEFAULT_BOX_ASPECT,
    SURFACE_DEFAULT_CAMERA,
    SURFACE_DEFAULT_FOCAL_LENGTH,
    SURFACE_DEFAULT_VIEW_SCALE,
    CoalescedOrbitController,
    SurfaceControlBar,
    SurfaceSelectionFooter,
    coarse_axis_ticks,
    display_index,
    matplotlib_colormap,
    scaled_limits,
    surface_display_flips,
    surface_projection_arrays,
    surface_value_bounds,
)


def _bounded_resize(dialog: QDialog, preferred: QSize, minimum: QSize) -> None:
    screen = dialog.screen()
    if screen is None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
    if screen is None:
        dialog.resize(preferred)
        return
    available = screen.availableGeometry().size()
    width = min(preferred.width(), round(available.width() * 0.9))
    height = min(preferred.height(), round(available.height() * 0.9))
    minimum_width = min(minimum.width(), available.width())
    minimum_height = min(minimum.height(), available.height())
    dialog.resize(max(minimum_width, width), max(minimum_height, height))


def _review_projection(map_data: MapData) -> tuple[CollapsedMap, bool]:
    """Collapse valid padding, or preserve every physical cell when padding diverges."""
    try:
        return collapse_duplicate_map(map_data), False
    except MapValidationError:
        # A direct local edit can intentionally make two repeated physical bins differ.
        # Such a map cannot be collapsed without hiding one of those values, so the
        # visualization falls back to the complete table grid instead of going stale.
        return (
            CollapsedMap(
                map_data,
                np.arange(map_data.columns, dtype=int),
                np.arange(map_data.rows, dtype=int),
                0,
                0,
            ),
            True,
        )


def _style_2d_axis(axes) -> None:
    theme = current_theme()
    axes.set_facecolor(theme.surface1)
    axes.tick_params(colors=theme.text_dim, labelsize=8)
    axes.title.set_color(theme.text)
    axes.xaxis.label.set_color(theme.text_dim)
    axes.yaxis.label.set_color(theme.text_dim)
    for spine in axes.spines.values():
        spine.set_color(theme.border_strong)
    axes.grid(True, color=theme.border_strong, alpha=0.45, linewidth=0.6)


def _style_3d_axis(axes) -> None:
    from matplotlib.colors import to_rgba

    theme = current_theme()
    axes.set_facecolor(theme.bg)
    pane = to_rgba(theme.surface2, 0.72)
    edge = to_rgba(theme.border_strong, 0.9)
    grid = to_rgba(theme.border_strong, 0.55)
    for axis in (axes.xaxis, axes.yaxis, axes.zaxis):
        axis.set_pane_color(pane)
        axis.pane.set_edgecolor(edge)
        axis.line.set_color(theme.text_dim)
        axis._axinfo["grid"]["color"] = grid
        axis._axinfo["grid"]["linewidth"] = 0.6
    axes.tick_params(axis="x", colors=theme.text_dim, labelsize=8, pad=1)
    axes.tick_params(axis="y", colors=theme.text_dim, labelsize=8, pad=1)
    axes.tick_params(axis="z", colors=theme.text_dim, labelsize=8, pad=1)
    axes.xaxis.label.set_color(theme.text)
    axes.yaxis.label.set_color(theme.text)
    axes.zaxis.label.set_color(theme.text)


class CurveReviewDialog(QDialog):
    def __init__(
        self,
        curve: CurveData,
        title: str,
        mask=None,
        parent=None,
        *,
        colormap: str = "rainbow",
        table=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._curve = curve
        self._mask = None if mask is None else np.asarray(mask, dtype=bool)
        self._colormap = colormap
        self._table = table
        self._current_column = max(0, table.currentColumn()) if table is not None else -1
        self._live_updates_connected = False
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(0)
        self._redraw_timer.timeout.connect(self._synchronize_from_table)
        self.setWindowTitle(title)
        _bounded_resize(self, QSize(880, 560), QSize(620, 400))
        theme = current_theme()
        self.figure = Figure(figsize=(8, 4.5), constrained_layout=True, facecolor=theme.bg)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.canvas)
        self._draw()
        app = QApplication.instance()
        manager = app.property("ecueditor_theme_manager") if app is not None else None
        if manager is not None and hasattr(manager, "changed"):
            manager.changed.connect(self._theme_changed)
        if table is not None:
            table.valuesEdited.connect(self._queue_table_sync)
            if hasattr(table, "valuesSynchronized"):
                table.valuesSynchronized.connect(self._queue_table_sync)
            table.currentCellChanged.connect(self._selection_changed)
            self._live_updates_connected = True
            self.finished.connect(self._disconnect_live_updates)

    @Slot(object)
    def _theme_changed(self, _theme) -> None:
        self._draw()

    @Slot()
    def _queue_table_sync(self) -> None:
        if self._live_updates_connected and not self._redraw_timer.isActive():
            self._redraw_timer.start()

    @Slot()
    def _synchronize_from_table(self) -> None:
        if not self._live_updates_connected or self._table is None:
            return
        try:
            values = np.asarray(self._table.values(), dtype=float).reshape(-1)
            x = self._table.x_values()
            if x is None:
                x = self._curve.x
            if x.size != values.size:
                return
            self._curve = CurveData(x, values, self._curve.name)
            mask = self._table.mask_values()
            if mask is None:
                self._mask = None
            else:
                flattened = np.asarray(mask, dtype=bool).reshape(-1)
                self._mask = flattened if flattened.size == values.size else None
        except (MapValidationError, RuntimeError, ValueError):
            return
        self._draw()

    @Slot(int, int, int, int)
    def _selection_changed(self, _row: int, column: int, *_args) -> None:
        self._current_column = max(0, min(self._curve.size - 1, column))
        self._draw()

    @Slot(int)
    def _disconnect_live_updates(self, _result: int) -> None:
        if not self._live_updates_connected or self._table is None:
            return
        self._live_updates_connected = False
        self._redraw_timer.stop()
        for signal, slot in (
            (self._table.valuesEdited, self._queue_table_sync),
            (getattr(self._table, "valuesSynchronized", None), self._queue_table_sync),
            (self._table.currentCellChanged, self._selection_changed),
        ):
            if signal is None:
                continue
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _draw(self) -> None:
        theme = current_theme()
        self.figure.set_facecolor(theme.bg)
        self.axes.clear()
        _style_2d_axis(self.axes)
        self.axes.plot(
            self._curve.x,
            self._curve.values,
            marker="o",
            markersize=4,
            linewidth=1.8,
            color=theme.chart_pens[0],
        )
        if (
            self._mask is not None
            and self._mask.shape == self._curve.values.shape
            and np.any(self._mask)
        ):
            self.axes.scatter(
                self._curve.x[self._mask],
                self._curve.values[self._mask],
                color=theme.warn,
                edgecolors=theme.bg,
                linewidths=0.7,
                zorder=5,
                label="Extrapolated",
            )
            legend = self.axes.legend(frameon=False)
            for text in legend.get_texts():
                text.set_color(theme.text_dim)
        if 0 <= self._current_column < self._curve.size:
            self.axes.scatter(
                [self._curve.x[self._current_column]],
                [self._curve.values[self._current_column]],
                color=theme.warn,
                edgecolors=theme.bg,
                linewidths=0.7,
                zorder=6,
            )
        self.axes.set_xlabel("X axis")
        self.axes.set_ylabel("Calibration value")
        self.axes.set_title(
            self._curve.name or "Curve review",
            loc="left",
            color=theme.text,
        )
        self.canvas.draw_idle()


class MapReviewDialog(QDialog):
    """Theme-aware X/Y slices and full-size 3D proposal review."""

    def __init__(
        self,
        map_data: MapData,
        title: str,
        table,
        mask=None,
        parent=None,
        *,
        colormap: str = "rainbow",
        x_label: str = "X",
        y_label: str = "Y",
        value_units: str = "",
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._table = table
        self._source_map = map_data
        self._source_mask = None if mask is None else np.asarray(mask, dtype=bool).copy()
        self._collapsed, self._padding_conflict = _review_projection(map_data)
        self._map = self._collapsed.map_data
        self._mask = self._collapse_mask()
        self._colormap = colormap
        self._x_label = x_label.strip() or "X"
        self._y_label = y_label.strip() or "Y"
        self._value_units = value_units.strip()
        self._current_row = 0
        self._current_column = 0
        self._surface_selection = None
        self._surface_mask = None
        self._flip_x = False
        self._flip_y = False
        self._surface_flips = surface_display_flips(
            flip_x=self._flip_x,
            flip_y=self._flip_y,
        )
        self._surface_real: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self._surface_positions: tuple[np.ndarray, np.ndarray] | None = None
        self.surface_item = None
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(0)
        self._redraw_timer.timeout.connect(self._synchronize_from_table)
        self.setWindowTitle(title)
        _bounded_resize(self, QSize(1080, 720), QSize(680, 460))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("mapStudioReviewTabs")
        layout.addWidget(self.tabs, 1)

        theme = current_theme()
        surface_page = QWidget()
        surface_layout = QVBoxLayout(surface_page)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        self.surface_controls = SurfaceControlBar(surface_page)
        self.reset_button = self.surface_controls.reset_button
        self.flip_x_button = self.surface_controls.flip_x_button
        self.flip_y_button = self.surface_controls.flip_y_button
        surface_layout.addWidget(self.surface_controls)
        self.figure = Figure(figsize=(9, 6), facecolor=theme.bg)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.surface_axes = self.figure.add_subplot(
            111,
            projection="3d",
            computed_zorder=False,
        )
        self.figure.subplots_adjust(left=0.045, right=0.96, bottom=0.14, top=0.975)
        self._base_axes_position = self.surface_axes.get_position(original=True).frozen()
        self.surface_axes.view_init(
            elev=SURFACE_DEFAULT_CAMERA[0],
            azim=SURFACE_DEFAULT_CAMERA[1],
            roll=SURFACE_DEFAULT_CAMERA[2],
        )
        self._orbit_controller = CoalescedOrbitController(self.canvas)
        self._orbit_controller.bind_axes(self.surface_axes)
        surface_layout.addWidget(self.canvas, 1)
        self.surface_footer = SurfaceSelectionFooter("", surface_page)
        self.selection_label = self.surface_footer.value_label
        surface_layout.addWidget(self.surface_footer)
        self.tabs.addTab(surface_page, "3D Surface")

        slices_page = QWidget()
        slices_layout = QVBoxLayout(slices_page)
        slices_layout.setContentsMargins(0, 0, 0, 0)
        slices_layout.setSpacing(4)
        self.slices_help = QLabel(self._slice_help_text())
        self.slices_help.setObjectName("mapStudioSliceHelp")
        self.slices_help.setWordWrap(True)
        slices_layout.addWidget(self.slices_help)
        self.slice_selection_label = QLabel()
        self.slice_selection_label.setObjectName("mapStudioPlotSelection")
        slices_layout.addWidget(self.slice_selection_label)
        self.slices_figure = Figure(
            figsize=(9, 5), constrained_layout=True, facecolor=theme.bg
        )
        self.slices_canvas = FigureCanvasQTAgg(self.slices_figure)
        self.x_axes = self.slices_figure.add_subplot(121)
        self.y_axes = self.slices_figure.add_subplot(122)
        slices_layout.addWidget(self.slices_canvas, 1)
        self.tabs.addTab(slices_page, "Axis Slices")

        self.reset_button.clicked.connect(self.reset_view)
        self.flip_x_button.toggled.connect(self._set_flip_x)
        self.flip_y_button.toggled.connect(self._set_flip_y)
        table.currentCellChanged.connect(self._selection_changed)
        if hasattr(table, "valuesEdited"):
            table.valuesEdited.connect(self._queue_table_sync)
        if hasattr(table, "valuesSynchronized"):
            table.valuesSynchronized.connect(self._queue_table_sync)
        self._live_updates_connected = True
        self.finished.connect(self._disconnect_live_updates)
        app = QApplication.instance()
        manager = app.property("ecueditor_theme_manager") if app is not None else None
        if manager is not None and hasattr(manager, "changed"):
            manager.changed.connect(self._theme_changed)
        self._draw_surface()
        self._selection_changed(
            max(0, table.currentRow()),
            max(0, table.currentColumn()),
            -1,
            -1,
        )

    @Slot(int)
    def _disconnect_live_updates(self, _result: int) -> None:
        """Stop hidden Matplotlib work as soon as the review is closed."""
        if not self._live_updates_connected:
            return
        self._live_updates_connected = False
        self._redraw_timer.stop()
        self._orbit_controller.end()
        try:
            self._table.currentCellChanged.disconnect(self._selection_changed)
        except (RuntimeError, TypeError):
            pass
        if hasattr(self._table, "valuesEdited"):
            try:
                self._table.valuesEdited.disconnect(self._queue_table_sync)
            except (RuntimeError, TypeError):
                pass
        if hasattr(self._table, "valuesSynchronized"):
            try:
                self._table.valuesSynchronized.disconnect(self._queue_table_sync)
            except (RuntimeError, TypeError):
                pass

    def _slice_help_text(self) -> str:
        description = (
            "X slice follows the selected row across X. "
            "Y slice follows the selected column across Y. "
            "The amber point is the selected cell."
        )
        if self._padding_conflict:
            return (
                "Repeated axis bins contain different values; showing the full physical grid. "
                + description
            )
        return description

    def _collapse_mask(self) -> np.ndarray | None:
        if self._source_mask is None:
            return None
        if self._source_mask.shape == self._source_map.z.shape:
            return self._collapsed.collapse_mask(self._source_mask)
        if self._source_mask.shape == self._map.z.shape:
            return self._source_mask.copy()
        return None

    def _redraw_current(self) -> None:
        self._draw_surface()
        self._selection_changed(self._current_row, self._current_column, -1, -1)

    def _redraw_surface_projection(self) -> None:
        self._surface_flips = surface_display_flips(
            flip_x=self._flip_x,
            flip_y=self._flip_y,
        )
        self._draw_surface()
        display_row = int(self._collapsed.y_inverse[self._current_row])
        display_column = int(self._collapsed.x_inverse[self._current_column])
        self._draw_surface_selection(display_row, display_column)
        self.canvas.draw_idle()

    def reset_view(self) -> None:
        self._orbit_controller.end()
        self.surface_axes.view_init(
            elev=SURFACE_DEFAULT_CAMERA[0],
            azim=SURFACE_DEFAULT_CAMERA[1],
            roll=SURFACE_DEFAULT_CAMERA[2],
        )
        if hasattr(self, "_default_limits"):
            self.surface_axes.set_xlim3d(*self._default_limits[0])
            self.surface_axes.set_ylim3d(*self._default_limits[1])
            self.surface_axes.set_zlim3d(*self._default_limits[2])
        if hasattr(self, "_default_axes_position"):
            self.surface_axes.set_position(
                self._default_axes_position,
                which="both",
            )
        self.canvas.draw_idle()

    @Slot(bool)
    def _set_flip_x(self, enabled: bool) -> None:
        self._flip_x = enabled
        self._redraw_surface_projection()

    @Slot(bool)
    def _set_flip_y(self, enabled: bool) -> None:
        self._flip_y = enabled
        self._redraw_surface_projection()

    @Slot(object)
    def _theme_changed(self, _theme) -> None:
        self._redraw_current()

    @Slot()
    def _queue_table_sync(self) -> None:
        if self._live_updates_connected and not self._redraw_timer.isActive():
            self._redraw_timer.start()

    @Slot()
    def _synchronize_from_table(self) -> None:
        if not self._live_updates_connected:
            return
        previous_z_range = self._map.value_range
        try:
            values = np.asarray(self._table.values(), dtype=float)
            if values.shape != self._source_map.z.shape:
                return
            x = self._table.x_values()
            y = self._table.y_values()
            if x is None:
                x = self._source_map.x
            if y is None:
                y = self._source_map.y
            updated = MapData(
                x,
                y,
                values,
                self._source_map.name,
            )
            collapsed, padding_conflict = _review_projection(updated)
        except (MapValidationError, RuntimeError, ValueError):
            return
        axes_unchanged = np.array_equal(updated.x, self._source_map.x) and np.array_equal(
            updated.y, self._source_map.y
        )
        self._source_map = updated
        self._collapsed = collapsed
        self._map = collapsed.map_data
        self._padding_conflict = padding_conflict
        self.slices_help.setText(self._slice_help_text())
        self._source_mask = self._table.mask_values()
        self._mask = self._collapse_mask()
        self._draw_surface(
            preserve_limits=axes_unchanged,
            previous_z_range=previous_z_range,
        )
        self._selection_changed(self._current_row, self._current_column, -1, -1)

    def _selection_changed(self, row: int, column: int, *_args) -> None:
        source_row = max(0, min(self._source_map.rows - 1, row))
        source_column = max(0, min(self._source_map.columns - 1, column))
        self._current_row = source_row
        self._current_column = source_column
        display_row = int(self._collapsed.y_inverse[source_row])
        display_column = int(self._collapsed.x_inverse[source_column])
        self._draw_slices(display_row, display_column)
        self._draw_surface_selection(display_row, display_column)
        unit_suffix = f" {self._value_units}" if self._value_units else ""
        selection_text = (
            f"{self._x_label} {self._source_map.x[source_column]:.8g}   ·   "
            f"{self._y_label} {self._source_map.y[source_row]:.8g}   ·   "
            f"Value {self._source_map.z[source_row, source_column]:.8g}{unit_suffix}"
        )
        self.selection_label.setText(selection_text)
        self.slice_selection_label.setText(
            f"Cell {source_column + 1}, {source_row + 1}   ·   {selection_text}"
        )
        self.slices_canvas.draw_idle()
        self.canvas.draw_idle()

    def _draw_slices(self, row: int, column: int) -> None:
        theme = current_theme()
        self.slices_figure.set_facecolor(theme.bg)

        self.x_axes.clear()
        _style_2d_axis(self.x_axes)
        self.x_axes.plot(
            self._map.x,
            self._map.z[row, :],
            marker="o",
            markersize=4,
            color=theme.chart_pens[0],
            linewidth=1.7,
        )
        if self._mask is not None and np.any(self._mask[row, :]):
            outside = self._mask[row, :]
            self.x_axes.scatter(
                self._map.x[outside],
                self._map.z[row, outside],
                color=theme.warn,
                edgecolors=theme.bg,
                linewidths=0.6,
                zorder=4,
            )
        self.x_axes.scatter(
            [self._map.x[column]],
            [self._map.z[row, column]],
            color=theme.warn,
            edgecolors=theme.bg,
            zorder=5,
        )
        self.x_axes.set_title(
            f"{self._x_label} slice at {self._y_label} = {self._map.y[row]:.8g}",
            loc="left",
            color=theme.text,
        )
        self.x_axes.set_xlabel(self._x_label)
        self.x_axes.set_ylabel(self._value_units or "Calibration value")

        self.y_axes.clear()
        _style_2d_axis(self.y_axes)
        self.y_axes.plot(
            self._map.y,
            self._map.z[:, column],
            marker="o",
            markersize=4,
            color=theme.chart_pens[1],
            linewidth=1.7,
        )
        if self._mask is not None and np.any(self._mask[:, column]):
            outside = self._mask[:, column]
            self.y_axes.scatter(
                self._map.y[outside],
                self._map.z[outside, column],
                color=theme.warn,
                edgecolors=theme.bg,
                linewidths=0.6,
                zorder=4,
            )
        self.y_axes.scatter(
            [self._map.y[row]],
            [self._map.z[row, column]],
            color=theme.warn,
            edgecolors=theme.bg,
            zorder=5,
        )
        self.y_axes.set_title(
            f"{self._y_label} slice at {self._x_label} = {self._map.x[column]:.8g}",
            loc="left",
            color=theme.text,
        )
        self.y_axes.set_xlabel(self._y_label)
        self.y_axes.set_ylabel(self._value_units or "Calibration value")

    def _draw_surface(
        self,
        *,
        preserve_limits: bool = True,
        previous_z_range: tuple[float, float] | None = None,
    ) -> None:
        from matplotlib.colors import Normalize, to_rgba

        had_surface = self.surface_item is not None
        self._orbit_controller.end()
        camera = (self.surface_axes.elev, self.surface_axes.azim)
        limits = None
        axes_position = None
        if had_surface and preserve_limits:
            limits = (
                tuple(self.surface_axes.get_xlim3d()),
                tuple(self.surface_axes.get_ylim3d()),
                tuple(self.surface_axes.get_zlim3d()),
            )
            axes_position = self.surface_axes.get_position(original=True).frozen()
        self.surface_axes.clear()
        self.surface_axes.set_position(self._base_axes_position, which="both")
        self._surface_selection = None
        self._surface_mask = None
        _style_3d_axis(self.surface_axes)
        theme = current_theme()
        self.figure.set_facecolor(theme.bg)
        x, y, z, nx, ny = surface_projection_arrays(
            self._map.x,
            self._map.y,
            self._map.z.T,
            flip_x=self._surface_flips[0],
            flip_y=self._surface_flips[1],
        )
        self._surface_real = (x, y, z)
        self._surface_positions = (nx, ny)
        xx, yy = np.meshgrid(nx, ny, indexing="ij")
        low, high, pad = surface_value_bounds(z)
        if high == low:
            norm = Normalize(low - pad, high + pad)
        else:
            norm = Normalize(low, high)
        self.surface_item = self.surface_axes.plot_surface(
            xx,
            yy,
            z,
            cmap=matplotlib_colormap(self._colormap),
            norm=norm,
            edgecolor=to_rgba(theme.grid_line, 0.92),
            linewidth=0.48,
            antialiased=True,
            shade=False,
        )
        if self._mask is not None and np.any(self._mask):
            surface_mask = surface_projection_arrays(
                self._map.x,
                self._map.y,
                self._mask.T,
                flip_x=self._surface_flips[0],
                flip_y=self._surface_flips[1],
            )
            surface_mask = surface_mask[2]
            outside = surface_mask.astype(bool)
            self._surface_mask = self.surface_axes.scatter(
                xx[outside],
                yy[outside],
                z[outside],
                color=theme.warn,
                edgecolors=theme.bg,
                linewidths=0.5,
                depthshade=False,
            )
        x_tick_positions, x_tick_labels = coarse_axis_ticks(x)
        y_tick_positions, y_tick_labels = coarse_axis_ticks(y)
        self.surface_axes.set_xticks(x_tick_positions, x_tick_labels)
        self.surface_axes.set_yticks(y_tick_positions, y_tick_labels)
        self.surface_axes.set_xlabel(self._x_label, labelpad=6)
        self.surface_axes.set_ylabel(self._y_label, labelpad=6)
        self.surface_axes.set_zlabel(
            self._value_units or "Calibration value",
            labelpad=5,
        )
        for axis in (
            self.surface_axes.xaxis,
            self.surface_axes.yaxis,
            self.surface_axes.zaxis,
        ):
            axis.label.set_fontsize(9)
        xy_limits = scaled_limits(0.0, 10.0, SURFACE_DEFAULT_VIEW_SCALE)
        z_limits = scaled_limits(
            low - pad,
            high + pad,
            SURFACE_DEFAULT_VIEW_SCALE,
        )
        self.surface_axes.set_xlim3d(*xy_limits)
        self.surface_axes.set_ylim3d(*xy_limits)
        self.surface_axes.set_zlim3d(*z_limits)
        self.surface_axes.set_box_aspect(SURFACE_DEFAULT_BOX_ASPECT)
        self.surface_axes.set_proj_type(
            "persp",
            focal_length=SURFACE_DEFAULT_FOCAL_LENGTH,
        )
        self.surface_axes.view_init(elev=camera[0], azim=camera[1], roll=0.0)
        self._default_limits = (
            tuple(self.surface_axes.get_xlim3d()),
            tuple(self.surface_axes.get_ylim3d()),
            tuple(self.surface_axes.get_zlim3d()),
        )
        self._default_axes_position = self._base_axes_position.frozen()
        if limits is not None:
            self.surface_axes.set_xlim3d(*limits[0])
            self.surface_axes.set_ylim3d(*limits[1])
            expand_z_view = False
            if previous_z_range is not None:
                old_z_minimum, old_z_maximum = sorted(limits[2])
                data_minimum, data_maximum = self._map.value_range
                previous_minimum, previous_maximum = previous_z_range
                scale = max(
                    1.0,
                    abs(data_minimum),
                    abs(data_maximum),
                    abs(previous_minimum),
                    abs(previous_maximum),
                )
                tolerance = np.finfo(float).eps * scale * 16.0
                expand_z_view = (
                    data_minimum < previous_minimum - tolerance
                    and data_minimum < old_z_minimum - tolerance
                ) or (
                    data_maximum > previous_maximum + tolerance
                    and data_maximum > old_z_maximum + tolerance
                )
            if not expand_z_view:
                self.surface_axes.set_zlim3d(*limits[2])
            else:
                auto_z_limits = tuple(self.surface_axes.get_zlim3d())
                old_inverted = limits[2][0] > limits[2][1]
                auto_inverted = auto_z_limits[0] > auto_z_limits[1]
                if old_inverted != auto_inverted:
                    auto_z_limits = auto_z_limits[::-1]
                self.surface_axes.set_zlim3d(*auto_z_limits)
            if axes_position is not None:
                self.surface_axes.set_position(axes_position, which="both")

    def _draw_surface_selection(self, row: int, column: int) -> None:
        if self._surface_selection is not None:
            self._surface_selection.remove()
        if self._surface_real is None or self._surface_positions is None:
            return
        display_column, display_row = display_index(
            column,
            row,
            self._map.columns,
            self._map.rows,
            flip_x=self._surface_flips[0],
            flip_y=self._surface_flips[1],
        )
        nx, ny = self._surface_positions
        _x, _y, z = self._surface_real
        theme = current_theme()
        self._surface_selection = self.surface_axes.scatter(
            [nx[display_column]],
            [ny[display_row]],
            [z[display_column, display_row]],
            color=theme.warn,
            edgecolors=theme.bg,
            linewidths=0.8,
            s=36,
            depthshade=False,
        )
