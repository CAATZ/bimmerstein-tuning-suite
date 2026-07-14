from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from ecueditor.ui.design.colormaps import COLORMAPS
from ecueditor.ui.design.fonts import numeric_font
from ecueditor.ui.design.icons import icon
from ecueditor.ui.editor.frames.header import FrameHeader

_DEFAULT_VIEW_SCALE = 1.0 / 0.88  # one wheel notch out: keeps corner tick labels separated


def _scaled_limits(low: float, high: float, scale: float) -> tuple[float, float]:
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

    Repeated RomRaider padding bins receive tiny visual-only gaps.  Their displayed labels and
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


def axis_tick_indices(count: int, target: int = 7) -> tuple[int, ...]:
    """Return adaptive index ticks while keeping both ends."""
    if count <= 0:
        return ()
    step = max(1, round(count / max(1, target)))
    ticks = list(range(0, count, step))
    if ticks[-1] != count - 1:
        ticks.append(count - 1)
    return tuple(ticks)


def axis_value_tick_indices(values, target: int = 7) -> tuple[int, ...]:
    """Adaptive tick indexes with repeated RomRaider padding labelled once."""
    axis = np.asarray(values, dtype=float)
    unique: list[int] = []
    for index, value in enumerate(axis):
        if any(math.isclose(float(value), float(axis[prior]), rel_tol=1e-9, abs_tol=1e-9)
               for prior in unique):
            continue
        unique.append(index)
    return tuple(unique[index] for index in axis_tick_indices(len(unique), target))


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


class _CoalescedOrbitController:
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
        self._callback_ids = (
            canvas.mpl_connect("button_press_event", self._on_press),
            canvas.mpl_connect("motion_notify_event", self._on_motion),
            canvas.mpl_connect("button_release_event", self._on_release),
            canvas.mpl_connect("scroll_event", self._on_scroll),
        )

    def bind_axes(self, axes) -> None:
        self.end()
        self.axes = axes
        self._disconnect_default_navigation(axes)

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


class Surface3DView(QWidget):
    """Themed Matplotlib calibration surface with native camera-aware axes."""

    def __init__(self, table, parent=None, *, colormap: str = "viridis") -> None:
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        from ecueditor.ui.design.theme_manager import current_theme

        self._table = table
        self._colormap = colormap
        self._source_model = None
        self._marker = None
        self._highlighted: tuple[int, int] | None = None
        self._flip_x = False
        self._flip_y = True
        self._height_scale = 1.0
        self.surface_item = None
        self.colorbar = None

        self.setObjectName("surface3dView")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = FrameHeader(table.definition)
        layout.addWidget(self.header)

        controls = QWidget()
        controls.setObjectName("surfaceControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(12, 6, 12, 6)
        controls_layout.setSpacing(8)

        self.reset_button = QToolButton()
        self.reset_button.setText("Reset view")
        self.reset_button.setIcon(icon("cube"))
        self.reset_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        controls_layout.addWidget(self.reset_button)

        self.flip_x_button = QToolButton()
        self.flip_x_button.setText("Flip X")
        self.flip_x_button.setCheckable(True)
        controls_layout.addWidget(self.flip_x_button)
        self.flip_y_button = QToolButton()
        self.flip_y_button.setText("Flip Y")
        self.flip_y_button.setCheckable(True)
        self.flip_y_button.setChecked(True)
        controls_layout.addWidget(self.flip_y_button)

        controls_layout.addSpacing(6)
        controls_layout.addWidget(QLabel("HEIGHT"))
        self.height_slider = QSlider(Qt.Orientation.Horizontal)
        self.height_slider.setObjectName("surfaceHeight")
        self.height_slider.setRange(50, 200)
        self.height_slider.setValue(100)
        self.height_slider.setSingleStep(10)
        self.height_slider.setPageStep(25)
        self.height_slider.setFixedWidth(120)
        controls_layout.addWidget(self.height_slider)
        self.height_label = QLabel("1.0×")
        self.height_label.setObjectName("surfaceHeightValue")
        controls_layout.addWidget(self.height_label)
        controls_layout.addStretch(1)
        controls_layout.addWidget(QLabel(
            "Left drag: orbit  ·  Middle drag: pan  ·  Wheel: zoom"
        ))
        layout.addWidget(controls)

        theme = current_theme()
        self.figure = Figure(figsize=(9.2, 6.1), dpi=100, facecolor=theme.bg)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setObjectName("surfaceCanvas")
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(280)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._orbit_controller = _CoalescedOrbitController(self.canvas)
        layout.addWidget(self.canvas, 1)

        footer = QWidget()
        footer.setObjectName("surfaceFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 6, 12, 8)
        self.value_label = QLabel("Select a cell in the table to inspect the surface")
        self.value_label.setObjectName("surfaceValue")
        self.value_label.setFont(numeric_font(9))
        footer_layout.addWidget(self.value_label)
        footer_layout.addStretch(1)
        layout.addWidget(footer)

        self.reset_button.clicked.connect(self.reset_view)
        self.flip_x_button.toggled.connect(self._set_flip_x)
        self.flip_y_button.toggled.connect(self._set_flip_y)
        self.height_slider.valueChanged.connect(self._set_height)
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
        old_camera = None
        if hasattr(self, "axes") and not reset_camera:
            old_camera = (self.axes.elev, self.axes.azim, getattr(self.axes, "roll", 0.0))

        real_x, real_y, real_z = surface_arrays(self._table)
        x, y, z = oriented_arrays(
            real_x, real_y, real_z, flip_x=self._flip_x, flip_y=self._flip_y,
        )
        nx, ny = axis_positions(x), axis_positions(y)
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
        low, high = float(np.min(z)), float(np.max(z))
        if high == low:
            pad = max(1.0, abs(low) * 0.05)
            norm = Normalize(low - pad, high + pad)
        else:
            pad = (high - low) * 0.04
            norm = Normalize(low, high)
        cmap = matplotlib_colormap(self._colormap)
        self.surface_item = self.axes.plot_surface(
            xx, yy, z, rstride=1, cstride=1, cmap=cmap, norm=norm,
            edgecolor=to_rgba(theme.grid_line, 0.92), linewidth=0.48,
            antialiased=True, shade=False,
        )

        x_ticks = axis_value_tick_indices(x)
        y_ticks = axis_value_tick_indices(y)
        self.axes.set_xticks([float(nx[index]) for index in x_ticks],
                             [f"{x[index]:g}" for index in x_ticks])
        self.axes.set_yticks([float(ny[index]) for index in y_ticks],
                             [f"{y[index]:g}" for index in y_ticks])
        xy_limits = _scaled_limits(0.0, 10.0, _DEFAULT_VIEW_SCALE)
        z_limits = _scaled_limits(low - pad, high + pad, _DEFAULT_VIEW_SCALE)
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
        self.axes.set_box_aspect((1.28, 1.0, 0.82 * self._height_scale))
        self.axes.set_proj_type("persp", focal_length=0.92)
        camera = old_camera or (25.0, -125.0, 0.0)
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

        self._marker = None
        self.canvas.draw_idle()
        if highlighted is not None:
            self.highlight_cell(*highlighted)

    def bind_source_model(self, model) -> None:
        self._source_model = model

    def _on_source_data_changed(self, *_args) -> None:
        self.refresh()

    def _on_source_selection_changed(self, current, _previous=None) -> None:
        if current is not None and current.isValid() and self._source_model is not None:
            self.highlight_cell(*self._source_model.cell_xy(current))
        else:
            self.clear_highlight()

    def refresh(self) -> None:
        self._rebuild()

    def set_colormap(self, name: str) -> None:
        self._colormap = name
        self._rebuild()

    def reset_view(self) -> None:
        self.axes.view_init(elev=25.0, azim=-125.0, roll=0.0)
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

    def _set_height(self, value: int) -> None:
        self._height_scale = value / 100.0
        self.height_label.setText(f"{self._height_scale:.1f}×")
        self.axes.set_box_aspect((1.28, 1.0, 0.82 * self._height_scale))
        self.canvas.draw_idle()

    def highlight_cell(self, x: int, y: int) -> None:
        self._remove_marker(draw=False)
        nx, ny = self._positions
        real_x, real_y, real_z = self._real
        size_x, size_y = len(nx), len(ny)
        if not (0 <= x < size_x and 0 <= y < size_y):
            self._highlighted = None
            return
        self._highlighted = (x, y)
        display_x, display_y = display_index(
            x, y, size_x, size_y, flip_x=self._flip_x, flip_y=self._flip_y,
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
