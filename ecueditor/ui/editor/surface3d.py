from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ecueditor.ui.design.colormaps import COLORMAPS
from ecueditor.ui.design.fonts import numeric_font
from ecueditor.ui.design.icons import icon
from ecueditor.ui.editor.frames.header import FrameHeader

SURFACE_DEFAULT_CAMERA = (25.0, -125.0, 0.0)
SURFACE_DEFAULT_VIEW_SCALE = 1.0 / 0.88
SURFACE_DEFAULT_BOX_ASPECT = (1.28, 1.0, 0.82)
SURFACE_DEFAULT_FOCAL_LENGTH = 0.92
SURFACE_DEFAULT_REVERSE_Y = True


def scaled_limits(low: float, high: float, scale: float) -> tuple[float, float]:
    center = (low + high) / 2.0
    half_span = (high - low) * scale / 2.0
    return center - half_span, center + half_span


def surface_arrays(table) -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    """Return real-value arrays; z[x][y] matches the renderer's indexing."""
    sx, sy = table.shape()
    z = np.empty((sx, sy), dtype=float)
    for xi in range(sx):
        for yi in range(sy):
            z[xi, yi] = table.cell_at(xi, yi).real()
    if table.x_axis is not None:
        x = np.array([table.x_axis.cell_at(i, 0).real() for i in range(sx)], dtype=float)
    else:
        x = np.arange(sx, dtype=float)
    if table.y_axis is not None:
        y = np.array([table.y_axis.cell_at(i, 0).real() for i in range(sy)], dtype=float)
    else:
        y = np.arange(sy, dtype=float)
    return x, y, z


def axis_positions(values) -> "np.ndarray":
    """Map real breakpoints to 0..10 while preserving non-uniform spacing.

    Repeated RomRaider padding bins receive tiny visual-only gaps. Their displayed labels and
    ROM values remain unchanged, but the render grid stays strictly ordered and inspectable.
    """
    axis = np.asarray(values, dtype=float)
    if axis.size <= 1:
        return np.zeros(axis.size, dtype=float)
    low, high = float(np.min(axis)), float(np.max(axis))
    if high == low:
        return np.linspace(0.0, 10.0, axis.size)
    positions = (axis - low) / (high - low) * 10.0
    if positions[-1] < positions[0]:
        positions = 10.0 - positions
    if np.any(np.diff(positions) <= 0.0):
        gap = min(0.08, 1.0 / max(1, axis.size - 1))
        positions = positions.copy()
        for index in range(1, positions.size):
            positions[index] = max(positions[index], positions[index - 1] + gap)
        positions = (positions - positions[0]) / (positions[-1] - positions[0]) * 10.0
    return positions


def coarse_axis_ticks(values, target: int = 7) -> tuple[tuple[float, ...], tuple[str, ...]]:
    """Return regularly spaced whole-number ticks on the normalized real-value axis."""
    from matplotlib.ticker import MaxNLocator

    axis = np.asarray(values, dtype=float)
    if axis.size == 0:
        return (), ()
    start, end = float(axis[0]), float(axis[-1])
    if start == end:
        return (0.0,), (str(int(round(start))),)
    low, high = sorted((start, end))
    locator = MaxNLocator(
        nbins=max(1, target),
        integer=True,
        steps=[1.0, 2.0, 2.5, 5.0, 10.0],
    )
    tolerance = np.finfo(float).eps * max(1.0, abs(low), abs(high)) * 16.0
    tick_values = [
        float(value)
        for value in locator.tick_values(low, high)
        if low - tolerance <= value <= high + tolerance
        and np.isclose(value, round(float(value)), rtol=0.0, atol=tolerance)
    ]
    if not tick_values:
        return (5.0,), (str(int(round((start + end) / 2.0))),)
    if start > end:
        tick_values.reverse()
    positions = tuple((value - start) / (end - start) * 10.0 for value in tick_values)
    labels = tuple(str(int(round(value))) for value in tick_values)
    return positions, labels


def oriented_arrays(x, y, z, *, flip_x: bool, flip_y: bool):
    """Apply display flips without changing the underlying table."""
    ox = np.asarray(x, dtype=float)
    oy = np.asarray(y, dtype=float)
    oz = np.asarray(z, dtype=float)
    if flip_x:
        ox = ox[::-1]
        oz = oz[::-1, :]
    if flip_y:
        oy = oy[::-1]
        oz = oz[:, ::-1]
    return ox.copy(), oy.copy(), oz.copy()


def surface_display_flips(*, flip_x: bool, flip_y: bool) -> tuple[bool, bool]:
    """Resolve user toggles against the suite's neutral surface orientation."""
    return bool(flip_x), SURFACE_DEFAULT_REVERSE_Y ^ bool(flip_y)


def surface_projection_arrays(x, y, z, *, flip_x: bool, flip_y: bool):
    """Return oriented values and normalized render positions for a 3D surface."""
    ox, oy, oz = oriented_arrays(x, y, z, flip_x=flip_x, flip_y=flip_y)
    return ox, oy, oz, axis_positions(ox), axis_positions(oy)


def surface_value_bounds(values) -> tuple[float, float, float]:
    """Return the data range and the suite's visual Z padding."""
    array = np.asarray(values, dtype=float)
    low, high = float(np.min(array)), float(np.max(array))
    if high == low:
        pad = max(1.0, abs(low) * 0.05)
    else:
        pad = (high - low) * 0.04
    return low, high, pad


def display_index(
    x: int, y: int, size_x: int, size_y: int, *, flip_x: bool, flip_y: bool,
) -> tuple[int, int]:
    """Map an original table coordinate to its current displayed coordinate."""
    return (size_x - 1 - x if flip_x else x,
            size_y - 1 - y if flip_y else y)


def matplotlib_colormap(name: str):
    """Build a Matplotlib colormap from the editor's shared 256-color LUT."""
    from matplotlib.colors import ListedColormap

    lut = COLORMAPS.get(name, COLORMAPS["viridis"])
    colors = [(r / 255.0, g / 255.0, b / 255.0, 1.0) for r, g, b in lut]
    return ListedColormap(colors, name=f"ecueditor-{name}")


def _axis_name(axis, fallback: str) -> str:
    if axis is None:
        return fallback
    name = axis.name or fallback
    units = axis.scale.units if axis.scale else ""
    return f"{name} ({units})" if units else name


@dataclass
class OrbitAccumulator:
    """Keep only the newest pointer position between rendered frames."""

    _current: tuple[float, float] | None = None
    _pending: tuple[float, float] | None = None

    def start(self, x: float, y: float) -> None:
        self._current = (x, y)
        self._pending = None

    def queue(self, x: float, y: float) -> None:
        if self._current is not None:
            self._pending = (x, y)

    def take_delta(self) -> tuple[float, float] | None:
        if self._current is None or self._pending is None:
            return None
        x, y = self._pending
        current_x, current_y = self._current
        self._current = self._pending
        self._pending = None
        return x - current_x, y - current_y

    def stop(self) -> None:
        self._current = None
        self._pending = None


class CoalescedOrbitController:
    """Frame-limit 3D navigation so mouse input cannot build a redraw backlog."""

    def __init__(self, canvas) -> None:
        self.canvas = canvas
        self.axes = None
        self.orbit = OrbitAccumulator()
        self._dragging = False
        self._drag_mode = "orbit"
        self._pending_position: tuple[float, float] | None = None
        self._timer = QTimer(canvas)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.flush)
        self._callback_ids: tuple[int, ...] = (
            canvas.mpl_connect("button_press_event", self._on_press),
            canvas.mpl_connect("motion_notify_event", self._on_motion),
            canvas.mpl_connect("button_release_event", self._on_release),
            canvas.mpl_connect("scroll_event", self._on_scroll),
        )

    def bind_axes(self, axes) -> None:
        self.end()
        self.axes = axes
        self._disconnect_default_navigation(axes)

    def shutdown(self) -> None:
        """Stop queued input and release Matplotlib callbacks before canvas teardown."""
        self.end()
        self._timer.stop()
        for callback_id in self._callback_ids:
            self.canvas.mpl_disconnect(callback_id)
        self._callback_ids = ()

    def _disconnect_default_navigation(self, axes) -> None:
        callback_names = {
            "motion_notify_event": "_on_move",
            "button_press_event": "_button_press",
            "button_release_event": "_button_release",
        }
        registry = self.canvas.callbacks.callbacks
        for event_name, method_name in callback_names.items():
            for callback_id, proxy in list(registry.get(event_name, {}).items()):
                callback = proxy()
                if (getattr(callback, "__self__", None) is axes
                        and getattr(callback, "__name__", "") == method_name):
                    self.canvas.mpl_disconnect(callback_id)

    def begin(self, x: float, y: float, *, mode: str = "orbit") -> None:
        if self.axes is None:
            return
        self.orbit.start(x, y)
        self._drag_mode = "pan" if mode == "pan" else "orbit"
        self._pending_position = None
        self._dragging = True
        self._timer.start()

    def queue(self, x: float, y: float) -> None:
        if self._dragging:
            self._pending_position = (x, y)
            self.orbit.queue(x, y)

    def flush(self) -> None:
        if self.axes is None:
            return
        delta = self.orbit.take_delta()
        if delta is None:
            return
        dx, dy = delta
        if dx == 0.0 and dy == 0.0:
            return
        if self._drag_mode == "pan" and self._pending_position is not None:
            from matplotlib.transforms import Bbox

            figure_box = self.axes.figure.bbox
            x_shift = dx / max(float(figure_box.width), 1.0)
            y_shift = dy / max(float(figure_box.height), 1.0)
            position = self.axes.get_position(original=True)
            # Keep a useful slice of the box on-canvas even after an enthusiastic drag.
            visible = 0.14
            x0 = max(-position.width + visible, min(1.0 - visible, position.x0 + x_shift))
            y0 = max(-position.height + visible, min(1.0 - visible, position.y0 + y_shift))
            moved = Bbox.from_bounds(x0, y0, position.width, position.height)
            self.axes.set_position(moved, which="both")
        else:
            width = max(float(self.axes.bbox.width), 1.0)
            height = max(float(self.axes.bbox.height), 1.0)
            self.axes.view_init(
                elev=self.axes.elev - (dy / height) * 180.0,
                azim=self.axes.azim - (dx / width) * 180.0,
                roll=0.0,
            )
        # A synchronous frame prevents Qt from accumulating deferred Matplotlib draws.
        self.canvas.draw()

    def end(self) -> None:
        if not self._dragging:
            self.orbit.stop()
            self._timer.stop()
            return
        self.flush()
        self._dragging = False
        self._pending_position = None
        self.orbit.stop()
        self._timer.stop()

    def _on_press(self, event) -> None:
        from matplotlib.backend_bases import MouseButton

        if event.inaxes is not self.axes:
            return
        if event.button == MouseButton.LEFT:
            self.begin(float(event.x), float(event.y), mode="orbit")
        elif event.button == MouseButton.MIDDLE:
            self.begin(float(event.x), float(event.y), mode="pan")

    def _on_motion(self, event) -> None:
        if self._dragging:
            self.queue(float(event.x), float(event.y))

    def _on_release(self, event) -> None:
        from matplotlib.backend_bases import MouseButton

        if event.button in {MouseButton.LEFT, MouseButton.MIDDLE}:
            self.end()

    def _on_scroll(self, event) -> None:
        if self.axes is None or event.inaxes is not self.axes or not event.step:
            return
        scale = 0.88 ** float(event.step)
        self.axes._scale_axis_limits(scale, scale, scale)
        self.canvas.draw_idle()


class SurfaceControlBar(QWidget):
    """Shared reset/flip controls for suite 3D surface views."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("surfaceControls")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        self.reset_button = QToolButton(self)
        self.reset_button.setText("Reset view")
        self.reset_button.setToolTip("Reset view")
        self.reset_button.setIcon(icon("cube"))
        self.reset_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        layout.addWidget(self.reset_button)

        self.flip_x_button = QToolButton(self)
        self.flip_x_button.setText("Flip X")
        self.flip_x_button.setCheckable(True)
        layout.addWidget(self.flip_x_button)

        self.flip_y_button = QToolButton(self)
        self.flip_y_button.setText("Flip Y")
        self.flip_y_button.setCheckable(True)
        layout.addWidget(self.flip_y_button)

        layout.addStretch(1)
        self.hint_label = QLabel(
            "Left drag: orbit  ·  Middle drag: pan  ·  Wheel: zoom",
            self,
        )
        layout.addWidget(self.hint_label)


class SurfaceSelectionFooter(QWidget):
    """Shared selected-cell readout band for suite 3D surface views."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("surfaceFooter")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 8)
        self.value_label = QLabel(text, self)
        self.value_label.setObjectName("surfaceValue")
        self.value_label.setFont(numeric_font(9))
        layout.addWidget(self.value_label)
        layout.addStretch(1)


class Surface3DView(QWidget):
    """Themed Matplotlib calibration surface with native camera-aware axes."""

    def __init__(self, table, parent=None, *, colormap: str = "viridis") -> None:
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        from ecueditor.ui.design.theme_manager import current_theme

        self._table = table
        self._colormap = colormap
        self._source_grid: Any | None = None
        self._source_model: Any | None = None
        self._source_selection_model: Any | None = None
        self._source_connections: list[Any] = []
        self._marker: Any | None = None
        self._highlighted: tuple[int, int] | None = None
        self._flip_x = False
        self._flip_y = False
        self.surface_item: Any | None = None
        self.colorbar: Any = None
        self._tearing_down = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._flush_queued_refresh)

        self.setObjectName("surface3dView")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = FrameHeader(table.definition)
        layout.addWidget(self.header)

        self.controls = SurfaceControlBar(self)
        self.reset_button = self.controls.reset_button
        self.flip_x_button = self.controls.flip_x_button
        self.flip_y_button = self.controls.flip_y_button
        layout.addWidget(self.controls)

        theme = current_theme()
        self.figure = Figure(figsize=(9.2, 6.1), dpi=100, facecolor=theme.bg)
        self.axes: Any
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setObjectName("surfaceCanvas")
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(280)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._orbit_controller = CoalescedOrbitController(self.canvas)
        layout.addWidget(self.canvas, 1)

        self.footer = SurfaceSelectionFooter(
            "Select a cell in the table to inspect the surface",
            self,
        )
        self.value_label = self.footer.value_label
        layout.addWidget(self.footer)

        self.reset_button.clicked.connect(self.reset_view)
        self.flip_x_button.toggled.connect(self._set_flip_x)
        self.flip_y_button.toggled.connect(self._set_flip_y)
        self._rebuild(reset_camera=True)

    def _style_axes(self) -> None:
        from matplotlib.colors import to_rgba
        from ecueditor.ui.design.theme_manager import current_theme

        theme = current_theme()
        axes = self.axes
        axes.set_facecolor(theme.bg)
        pane = to_rgba(theme.surface2, 0.72)
        pane_edge = to_rgba(theme.border_strong, 0.9)
        grid = to_rgba(theme.border_strong, 0.55)
        for axis in (axes.xaxis, axes.yaxis, axes.zaxis):
            axis.set_pane_color(pane)
            axis.pane.set_edgecolor(pane_edge)
            axis.line.set_color(theme.text_dim)
            axis._axinfo["grid"]["color"] = grid
            axis._axinfo["grid"]["linewidth"] = 0.6
            axis._axinfo["grid"]["linestyle"] = "-"
        axes.tick_params(axis="x", colors=theme.text_dim, labelsize=8, pad=1)
        axes.tick_params(axis="y", colors=theme.text_dim, labelsize=8, pad=1)
        axes.tick_params(axis="z", colors=theme.text_dim, labelsize=8, pad=1)
        axes.xaxis.label.set_color(theme.text)
        axes.yaxis.label.set_color(theme.text)
        axes.zaxis.label.set_color(theme.text)

    def _rebuild(self, *, reset_camera: bool = False) -> None:
        from matplotlib.colors import Normalize, to_rgba
        from matplotlib.cm import ScalarMappable
        from ecueditor.ui.design.theme_manager import current_theme

        highlighted = self._highlighted
        old_view = None
        if hasattr(self, "axes") and not reset_camera:
            old_view = {
                "camera": (
                    self.axes.elev, self.axes.azim, getattr(self.axes, "roll", 0.0),
                ),
                "xlim": tuple(self.axes.get_xlim3d()),
                "ylim": tuple(self.axes.get_ylim3d()),
                "zlim": tuple(self.axes.get_zlim3d()),
                "position": self.axes.get_position(original=True).frozen(),
            }

        real_x, real_y, real_z = surface_arrays(self._table)
        display_flip_x, display_flip_y = surface_display_flips(
            flip_x=self._flip_x,
            flip_y=self._flip_y,
        )
        x, y, z, nx, ny = surface_projection_arrays(
            real_x,
            real_y,
            real_z,
            flip_x=display_flip_x,
            flip_y=display_flip_y,
        )
        self._real = (x, y, z)
        self._positions = (nx, ny)

        self.figure.clear()
        theme = current_theme()
        self.figure.set_facecolor(theme.bg)
        self.axes = self.figure.add_subplot(111, projection="3d", computed_zorder=False)
        self._orbit_controller.bind_axes(self.axes)
        self.figure.subplots_adjust(left=0.045, right=0.89, bottom=0.14, top=0.975)
        self._style_axes()

        xx, yy = np.meshgrid(nx, ny, indexing="ij")
        low, high, pad = surface_value_bounds(z)
        if high == low:
            norm = Normalize(low - pad, high + pad)
        else:
            norm = Normalize(low, high)
        cmap = matplotlib_colormap(self._colormap)
        self.surface_item = self.axes.plot_surface(
            xx, yy, z, rstride=1, cstride=1, cmap=cmap, norm=norm,
            edgecolor=to_rgba(theme.grid_line, 0.92), linewidth=0.48,
            antialiased=True, shade=False,
        )

        x_tick_positions, x_tick_labels = coarse_axis_ticks(x)
        y_tick_positions, y_tick_labels = coarse_axis_ticks(y)
        self.axes.set_xticks(x_tick_positions, x_tick_labels)
        self.axes.set_yticks(y_tick_positions, y_tick_labels)
        xy_limits = scaled_limits(0.0, 10.0, SURFACE_DEFAULT_VIEW_SCALE)
        z_limits = scaled_limits(
            low - pad,
            high + pad,
            SURFACE_DEFAULT_VIEW_SCALE,
        )
        self.axes.set_xlim(*xy_limits)
        self.axes.set_ylim(*xy_limits)
        self.axes.set_zlim(*z_limits)
        self._default_limits = (
            tuple(self.axes.get_xlim3d()),
            tuple(self.axes.get_ylim3d()),
            tuple(self.axes.get_zlim3d()),
        )

        definition = self._table.definition
        self.axes.set_xlabel(_axis_name(definition.x_axis, "X"), labelpad=6)
        self.axes.set_ylabel(_axis_name(definition.y_axis, "Y"), labelpad=6)
        z_units = self._table.cells[0].scale.units
        self.axes.set_zlabel(z_units or "Value", labelpad=5)
        for axis in (self.axes.xaxis, self.axes.yaxis, self.axes.zaxis):
            axis.label.set_fontsize(9)
        self.axes.set_box_aspect(SURFACE_DEFAULT_BOX_ASPECT)
        self.axes.set_proj_type("persp", focal_length=SURFACE_DEFAULT_FOCAL_LENGTH)
        camera = old_view["camera"] if old_view is not None else SURFACE_DEFAULT_CAMERA
        self.axes.view_init(elev=camera[0], azim=camera[1], roll=camera[2])

        mappable = ScalarMappable(norm=norm, cmap=cmap)
        mappable.set_array(z)
        self.colorbar = self.figure.colorbar(
            mappable, ax=self.axes, fraction=0.035, pad=0.075, shrink=0.76, aspect=22,
        )
        self.colorbar.outline.set_edgecolor(theme.border_strong)
        self.colorbar.ax.tick_params(colors=theme.text_dim, labelsize=8, length=3)
        self.colorbar.ax.set_facecolor(theme.bg)
        self._default_axes_position = self.axes.get_position(original=True).frozen()

        if old_view is not None:
            self.axes.set_xlim3d(*old_view["xlim"])
            self.axes.set_ylim3d(*old_view["ylim"])
            old_z = old_view["zlim"]
            ascending = old_z[0] <= old_z[1]
            old_low, old_high = sorted(old_z)
            visible_low = low - pad if low < old_low else old_low
            visible_high = high + pad if high > old_high else old_high
            preserved_z = (visible_low, visible_high)
            if not ascending:
                preserved_z = preserved_z[::-1]
            self.axes.set_zlim3d(*preserved_z)
            self.axes.set_position(old_view["position"], which="both")

        self._marker = None
        self.canvas.draw_idle()
        if highlighted is not None:
            self.highlight_cell(*highlighted)

    def bind_source_model(self, model) -> None:
        """Compatibility binding for callers without a table view or selection model."""
        self.unbind_source_grid()
        self._source_model = model

    def bind_source_grid(self, grid) -> None:
        """Own one source-grid binding and immediately mirror its current selection."""
        if grid is self._source_grid:
            self._sync_source_selection()
            return
        self.unbind_source_grid()
        self._source_grid = grid
        self._source_model = grid.model()
        self._source_selection_model = grid.selectionModel()
        self._source_connections = [
            self._source_selection_model.currentChanged.connect(
                self._on_source_selection_changed
            ),
            grid.destroyed.connect(self._on_source_grid_destroyed),
        ]
        self._sync_source_selection()

    def unbind_source_grid(self, grid=None) -> None:
        """Release a live source without retaining a closing grid or its model."""
        if grid is not None and grid is not self._source_grid:
            return
        for connection in self._source_connections:
            try:
                QObject.disconnect(connection)
            except (RuntimeError, TypeError):
                pass
        self._source_connections = []
        self._source_grid = None
        self._source_model = None
        self._source_selection_model = None
        self._refresh_timer.stop()
        self._highlighted = None
        self._remove_marker(draw=False)
        self.value_label.setText("Select a cell in the table to inspect the surface")

    def _on_source_grid_destroyed(self, *_args) -> None:
        self._source_connections = []
        self._source_grid = None
        self._source_model = None
        self._source_selection_model = None
        self._refresh_timer.stop()
        self._highlighted = None
        self._marker = None

    def _sync_source_selection(self) -> None:
        selection = self._source_selection_model
        if selection is None:
            self.clear_highlight()
            return
        current = selection.currentIndex()
        if not current.isValid():
            indexes = selection.selectedIndexes()
            current = indexes[0] if indexes else current
        self._on_source_selection_changed(current)

    def queue_refresh(self) -> None:
        """Collapse any number of source notifications into one event-loop rebuild."""
        if not self._tearing_down and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _flush_queued_refresh(self) -> None:
        if not self._tearing_down:
            self._rebuild()

    def _on_source_selection_changed(self, current, _previous=None) -> None:
        if current is not None and current.isValid() and self._source_model is not None:
            self.highlight_cell(*self._source_model.cell_xy(current))
        else:
            self.clear_highlight()

    def refresh(self) -> None:
        self._refresh_timer.stop()
        self._rebuild()

    def set_colormap(self, name: str) -> None:
        self._colormap = name
        self._refresh_timer.stop()
        self._rebuild()

    def refresh_theme(self) -> None:
        """Repaint Matplotlib-owned colors after the application theme changes."""
        if self._tearing_down:
            return
        self._refresh_timer.stop()
        self._rebuild(reset_camera=False)

    def teardown(self) -> None:
        """Stop every queued callback before an MDI document detaches this widget."""
        if self._tearing_down:
            return
        self._tearing_down = True
        self._refresh_timer.stop()
        self.unbind_source_grid()
        self._orbit_controller.shutdown()

    def closeEvent(self, event) -> None:
        self.teardown()
        super().closeEvent(event)

    def reset_view(self) -> None:
        self.axes.view_init(
            elev=SURFACE_DEFAULT_CAMERA[0],
            azim=SURFACE_DEFAULT_CAMERA[1],
            roll=SURFACE_DEFAULT_CAMERA[2],
        )
        if hasattr(self, "_default_limits"):
            self.axes.set_xlim3d(*self._default_limits[0])
            self.axes.set_ylim3d(*self._default_limits[1])
            self.axes.set_zlim3d(*self._default_limits[2])
        if hasattr(self, "_default_axes_position"):
            self.axes.set_position(self._default_axes_position, which="both")
        self.canvas.draw_idle()

    def _set_flip_x(self, enabled: bool) -> None:
        self._flip_x = enabled
        self._rebuild()

    def _set_flip_y(self, enabled: bool) -> None:
        self._flip_y = enabled
        self._rebuild()

    def highlight_cell(self, x: int, y: int) -> None:
        self._remove_marker(draw=False)
        nx, ny = self._positions
        real_x, real_y, real_z = self._real
        size_x, size_y = len(nx), len(ny)
        if not (0 <= x < size_x and 0 <= y < size_y):
            self._highlighted = None
            return
        self._highlighted = (x, y)
        display_flip_x, display_flip_y = surface_display_flips(
            flip_x=self._flip_x,
            flip_y=self._flip_y,
        )
        display_x, display_y = display_index(
            x,
            y,
            size_x,
            size_y,
            flip_x=display_flip_x,
            flip_y=display_flip_y,
        )
        from ecueditor.ui.design.theme_manager import current_theme

        theme = current_theme()
        z_span = float(np.ptp(real_z))
        marker_z = float(real_z[display_x, display_y]) + max(z_span * 0.025, 0.02)
        self._marker = self.axes.scatter(
            [nx[display_x]], [ny[display_y]], [marker_z], s=54,
            c=[theme.sel_ring], edgecolors=[theme.sel_ring_inner], linewidths=0.9,
            depthshade=False,
        )
        definition = self._table.definition
        x_name = definition.x_axis.name if definition.x_axis and definition.x_axis.name else "X"
        y_name = definition.y_axis.name if definition.y_axis and definition.y_axis.name else "Y"
        unit = self._table.cells[0].scale.units
        self.value_label.setText(
            f"{x_name} {real_x[display_x]:g}   ·   {y_name} {real_y[display_y]:g}   ·   "
            f"Value {real_z[display_x, display_y]:g} {unit}".rstrip()
        )
        self.canvas.draw_idle()

    def clear_highlight(self) -> None:
        self._highlighted = None
        self._remove_marker(draw=True)
        self.value_label.setText("Select a cell in the table to inspect the surface")

    def _remove_marker(self, *, draw: bool = False) -> None:
        if self._marker is not None:
            self._marker.remove()
            self._marker = None
            if draw:
                self.canvas.draw_idle()
