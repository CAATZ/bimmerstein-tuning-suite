from __future__ import annotations
from pathlib import Path
from typing import Sequence
from PySide6 import QtWidgets
import pyqtgraph as pg
from ecueditor.core.dyno.et import ETCapture, ETResult
from ecueditor.core.dyno.offline import load_dyno_samples
from ecueditor.core.dyno.profile import CarProfile
from ecueditor.core.dyno.run import (DynoRun, DynoCapture, DynoEnv, load_run,
                                     ENGINE_SPEED, THROTTLE_ANGLE, VEHICLE_SPEED)
from ecueditor.core.errors import ECUEditorError
from ecueditor.ui.design.theme_manager import current_theme


def _pen(i: int, *, width: int = 2, dashed: bool = False) -> pg.QtGui.QPen:
    """Curve pen from the active theme's chart_pens (C13/D4: no hard-coded pen letters/hex)."""
    color = current_theme().chart_pens[i % len(current_theme().chart_pens)]
    kw = {"width": width}
    if dashed:
        kw["style"] = pg.QtCore.Qt.DashLine
    return pg.mkPen(color, **kw)


class DynoTab(QtWidgets.QWidget):
    """HP/TQ-vs-RPM dyno plot. Consumes core DynoRun/DynoCapture; no business logic here (spec §3).
    Live samples arrive on the UI thread via LoggerWindow._dispatch_sample (INTERFACES §ui/:
    samples cross the logger QThread boundary as queued Qt signals)."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._capture: DynoCapture | None = None
        self._run: DynoRun | None = None
        self._live_curves: list[pg.PlotDataItem] = []
        self._ref_curves: list[pg.PlotDataItem] = []
        self._profiles: list[CarProfile] = []
        self._kmh = False
        self._et_capture: ETCapture | None = None
        self._et_trace: list[tuple[float, float]] = []   # (elapsed_s, speed) live ET trace
        self._t_trace0 = 0.0

        self._plot = pg.PlotWidget()

        self._record = QtWidgets.QPushButton("Record"); self._record.setCheckable(True)
        self._record.toggled.connect(self._on_record_toggled)
        self._smoothing = QtWidgets.QComboBox()
        self._smoothing.addItems([str(n) for n in range(5, 20)])   # 5..19
        self._smoothing.setCurrentText("9")                        # default (fact base §4.3)
        self._recalc = QtWidgets.QPushButton("Recalculate")
        self._recalc.clicked.connect(self.recalculate)

        # pull configuration (fact base §4.2/§4.4/§4.6)
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self.gear_combo = QtWidgets.QComboBox()
        self.tps_min_field = QtWidgets.QDoubleSpinBox()
        self.tps_min_field.setRange(0.0, 100.0)
        self.tps_min_field.setValue(90.0)   # INFERRED default -- the fact base records only the
                                            # tooltip "less than TPS at WOT"; user-editable
        self.rpm_min_field = QtWidgets.QDoubleSpinBox()
        self.rpm_min_field.setRange(0.0, 20000.0); self.rpm_min_field.setDecimals(0)
        self.rpm_min_field.setValue(2000.0)                        # fact base §4.6 default
        self.rpm_max_field = QtWidgets.QDoubleSpinBox()
        self.rpm_max_field.setRange(0.0, 20000.0); self.rpm_max_field.setDecimals(0)
        self.rpm_max_field.setValue(6500.0)                        # fact base §4.6 default
        self.units_combo = QtWidgets.QComboBox()
        self.units_combo.addItems(["Imperial", "Metric"])          # fact base §4.6 toggle
        self.units_combo.currentIndexChanged.connect(lambda _i: self._relabel_axes())
        self.dyno_mode_radio = QtWidgets.QRadioButton("Dyno")
        self.dyno_mode_radio.setChecked(True)
        self.et_mode_radio = QtWidgets.QRadioButton("ET")          # fact base §4.5
        self.et_mode_radio.toggled.connect(self._on_mode_toggled)
        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.clicked.connect(self._save_clicked)
        self.open_ref_btn = QtWidgets.QPushButton("Open Reference")
        self.open_ref_btn.clicked.connect(self._open_ref_clicked)
        self.load_file_btn = QtWidgets.QPushButton("Load From File")
        self.load_file_btn.clicked.connect(self._load_file_clicked)
        self.status_label = QtWidgets.QLabel(""); self.status_label.setWordWrap(True)

        form = QtWidgets.QFormLayout()
        form.addRow("Car", self.profile_combo)
        form.addRow("Gear", self.gear_combo)
        form.addRow("Throttle min %", self.tps_min_field)
        form.addRow("RPM min", self.rpm_min_field)
        form.addRow("RPM max", self.rpm_max_field)
        form.addRow("Units", self.units_combo)

        modes = QtWidgets.QHBoxLayout()
        modes.addWidget(self.dyno_mode_radio); modes.addWidget(self.et_mode_radio)
        controls = QtWidgets.QHBoxLayout()
        for w in (self._record, QtWidgets.QLabel("Smoothing"), self._smoothing, self._recalc,
                  self.save_btn, self.open_ref_btn, self.load_file_btn):
            controls.addWidget(w)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(modes)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        layout.addWidget(self._plot)
        self._relabel_axes()

    # --- Dyno/ET mode switch (review fix): never render a curve against the wrong axes -------
    def _on_mode_toggled(self, _on: bool) -> None:
        """Clear plotted curves on a mode switch -- they were computed for the OTHER mode's
        axes. Presentation only: _run/_capture/_et_capture stay reusable (Recalculate etc.)."""
        if self._record.isChecked():
            self._record.blockSignals(True)
            self._record.setChecked(False)
            self._record.blockSignals(False)
            self._finish_recording_mode(et_mode=not _on)
        for c in self._live_curves + self._ref_curves:
            self._plot.removeItem(c)
        self._live_curves = []
        self._ref_curves = []
        self._et_trace = []
        self._update_record_enabled()
        self._relabel_axes()

    # --- axis labels (fact base §4.6: Metric relabels the power axis) ------------------------
    def _relabel_axes(self) -> None:
        if self.et_mode():
            self._plot.setLabel("bottom", "Time (seconds)")        # fact base §4.6 ET relabel
            self._plot.setLabel("left", "Vehicle Speed")
            return
        self._plot.setLabel("bottom", "Engine Speed (RPM)")
        metric = self.units_metric()
        self._plot.setLabel("left", "Calculated Wheel Power (kW)" if metric
                            else "Calculated Wheel Power (hp)")

    # --- public API used by tests / main window ----------------------------------------------
    def smoothing_order(self) -> int: return int(self._smoothing.currentText())
    def set_smoothing_order(self, order: int) -> None: self._smoothing.setCurrentText(str(order))
    def curve_count(self) -> int: return len(self._live_curves) + len(self._ref_curves)
    def units_metric(self) -> bool: return self.units_combo.currentText() == "Metric"
    def et_mode(self) -> bool: return self.et_mode_radio.isChecked()

    def rpm_range(self) -> tuple[float, float]:
        return (self.rpm_min_field.value(), self.rpm_max_field.value())

    def set_profiles(self, profiles: Sequence[CarProfile]) -> None:
        """Populate the car combo (the composition root loads cars_def.xml). An empty list
        reproduces RomRaider's missing-cars_def affordance: Record disabled + message."""
        self._profiles = list(profiles)
        self.profile_combo.clear()
        self.profile_combo.addItems([p.name for p in self._profiles])
        if not self._profiles:
            self._record.setEnabled(False)
            self.status_label.setText(
                "Missing cars_def.xml — place it next to the logger definition, or set "
                "cars_def_path in settings.json")
        else:
            self._record.setEnabled(True)

        self._update_record_enabled()

    def _update_record_enabled(self) -> None:
        enabled = self.et_mode() or bool(self._profiles)
        self._record.setEnabled(enabled)
        if not self._profiles and not self.et_mode():
            self.status_label.setText(
                "Missing cars_def.xml - place it next to the logger definition, or set "
                "cars_def_path in settings.json")
        elif self.status_label.text().startswith("Missing cars_def.xml"):
            self.status_label.clear()

    def set_speed_units_kmh(self, kmh: bool) -> None:
        """True when the vehicle-speed channel reports km/h (the composition root reads the
        logger definition's P9 units). Consumed by ET mode (Task 9)."""
        self._kmh = bool(kmh)

    def selected_profile(self) -> CarProfile | None:
        i = self.profile_combo.currentIndex()
        return self._profiles[i] if 0 <= i < len(self._profiles) else None

    def gear_ratio(self) -> float | None:
        p = self.selected_profile()
        i = self.gear_combo.currentIndex()
        return p.gear_ratios[i] if p is not None and 0 <= i < len(p.gear_ratios) else None

    def _on_profile_changed(self, index: int) -> None:
        self.gear_combo.clear()
        if 0 <= index < len(self._profiles):
            gears = self._profiles[index].gear_ratios
            self.gear_combo.addItems([str(i + 1) for i in range(len(gears))])
            # default gear = numGears-3: 2nd for 4AT, 3rd for 5MT, 4th for 6MT (fact base §4.4)
            self.gear_combo.setCurrentIndex(max(0, len(gears) - 3))

    def set_run(self, run: DynoRun) -> None:
        self._run = run
        for c in self._live_curves:
            self._plot.removeItem(c)
        self._live_curves = [
            self._plot.plot(run.rpm, run.power_hp, pen=_pen(0, width=2)),      # HP / kW
            self._plot.plot(run.rpm, run.torque_lbft, pen=_pen(1, width=2)),   # TQ / N-m
        ]

    def attach_capture(self, capture: DynoCapture) -> None: self._capture = capture

    # --- live capture (Phase 6b): LoggerWindow._dispatch_sample -> on_sample ------------------
    def on_sample(self, sample) -> None:
        if not self._record.isChecked():
            return
        if self.et_mode():
            cap = self._et_capture
            if cap is None:
                return
            cap.accept(sample)
            vs = sample.values.get(VEHICLE_SPEED)
            if vs is not None:
                if not self._et_trace:
                    self._t_trace0 = sample.timestamp_ms
                self._et_trace.append(((sample.timestamp_ms - self._t_trace0) / 1000.0, vs))
                self._plot_et_trace()
            if cap.is_stopped:
                self._record.setChecked(False)     # past 1330 ft -> finish + splits
            return
        dyno_capture = self._capture
        if dyno_capture is None:
            return
        dyno_capture.accept(sample)             # UI thread; the poll thread never calls in here
        if dyno_capture.is_stopped:
            self._record.setChecked(False)     # throttle lift ended the pull -> finish + plot

    def _on_record_toggled(self, on: bool) -> None:
        if not on:
            self._finish_recording()
            return
        if self.et_mode():
            # ET needs no car data: RomRaider registers only Vehicle Speed (fact base §4.5)
            self._et_capture = ETCapture(kmh=self._kmh)
            self._et_trace = []
            self.status_label.setText("Accelerate for 1/4 mile when ready!!")   # fact base §4.5
            return
        p, g = self.selected_profile(), self.gear_ratio()
        if p is None or g is None:
            # blockSignals: this un-check is an arming FAILURE -- it must not run the finish
            # path against a stale capture kept around for Recalculate.
            self._record.blockSignals(True); self._record.setChecked(False)
            self._record.blockSignals(False)
            self.status_label.setText("Select a car profile before recording")
            return
        self._capture = DynoCapture(p, g, DynoEnv(), tps_min=self.tps_min_field.value())
        self.status_label.setText("Accelerate using WOT when ready!!")   # fact base §4.6 prompt

    def _finish_recording(self) -> None:
        self._finish_recording_mode(et_mode=self.et_mode())

    def _finish_recording_mode(self, *, et_mode: bool) -> None:
        if et_mode:
            if self._et_capture is None:
                return
            self.status_label.setText(_format_et(self._et_capture.finish()))
            self._et_capture = None
            return
        if self._capture is None:
            return
        try:
            self.set_run(self._capture.finish(smoothing_order=self.smoothing_order(),
                                              rpm_range=self.rpm_range(),
                                              metric=self.units_metric()))
            self.status_label.setText("")
        except ECUEditorError as exc:          # e.g. not enough WOT samples
            self.status_label.setText(str(exc))

    def recalculate(self) -> None:
        if self._capture is not None:
            self._finish_recording()           # re-runs finish() WITHOUT recapturing (fact base §4.6)
        elif self._run is not None:
            self.set_run(self._run)            # no capture: just re-plot current run

    def save_run(self, path: str | Path) -> None:
        if self._run is not None:
            # units travel with the run itself (stamped by finish()/load_run -- Task 1)
            self._run.save(Path(path), units=self._run.units,
                           smoothing_order=self.smoothing_order())

    def overlay_reference(self, path: str | Path) -> None:
        ref = load_run(Path(path))
        for c in self._ref_curves:             # a 2nd overlay REPLACES the reference (H8)
            self._plot.removeItem(c)
        # RomRaider's Open restores the units mode recorded in the file (fact base §4.6)
        self.units_combo.setCurrentText(ref.units if ref.units in ("Imperial", "Metric")
                                        else "Imperial")
        self._ref_curves = [
            self._plot.plot(ref.rpm, ref.power_hp,
                            pen=_pen(0, width=1, dashed=True)),
            self._plot.plot(ref.rpm, ref.torque_lbft,
                            pen=_pen(1, width=1, dashed=True)),
        ]

    def _plot_et_trace(self) -> None:
        for c in self._live_curves:
            self._plot.removeItem(c)
        self._live_curves = [self._plot.plot([t for t, _ in self._et_trace],
                                             [v for _, v in self._et_trace],
                                             pen=_pen(2, width=2))]

    def load_from_file(self, path: str | Path) -> None:
        """Offline analysis (fact base §4.6): parse a logger CSV, replay it through a fresh
        DynoCapture, auto-set the RPM range from the WOT window, auto-Recalculate."""
        if self.et_mode():
            self.status_label.setText("Load From File analyzes dyno pulls — switch to Dyno mode")
            return
        p, g = self.selected_profile(), self.gear_ratio()
        if p is None or g is None:
            self.status_label.setText("Select a car profile before loading a file")
            return
        try:
            samples = load_dyno_samples(Path(path))
        except ECUEditorError as exc:              # hostile input -> message, never a crash
            self.status_label.setText(str(exc))
            return
        cap = DynoCapture(p, g, DynoEnv(), tps_min=self.tps_min_field.value())
        wot_rpm = [s.values[ENGINE_SPEED] for s in samples
                   if ENGINE_SPEED in s.values
                   and s.values.get(THROTTLE_ANGLE, 0.0) > self.tps_min_field.value()]
        for s in samples:
            cap.accept(s)
        if wot_rpm:
            self.rpm_min_field.setValue(min(wot_rpm))   # RPM range auto-set (fact base §4.6)
            self.rpm_max_field.setValue(max(wot_rpm))
        self._capture = cap
        self.recalculate()                              # auto-Recalculate (fact base §4.6)

    def _save_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save dyno run", "",
                                                        "Dyno runs (*.dyno);;All files (*)")
        if path:
            self.save_run(path)

    def _open_ref_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open reference run", "",
                                                        "Dyno runs (*.dyno);;All files (*)")
        if not path:
            return
        try:
            self.overlay_reference(path)
        except ECUEditorError as exc:
            self.status_label.setText(str(exc))

    def _load_file_clicked(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load logger CSV", "",
                                                        "Logger CSVs (*.csv);;All files (*)")
        if path:
            self.load_from_file(path)


def _format_et(res: ETResult) -> str:
    parts: list[str] = []
    if res.zero_to_sixty_s is not None:
        parts.append(f"0-60: {res.zero_to_sixty_s:.2f} s")
    for ft in sorted(res.splits):
        t, vs = res.splits[ft]
        parts.append(f"{ft} ft: {t:.2f} s @ {vs:.1f}")
    if res.quarter_mile_s is not None:
        parts.append(f"1/4 mile: {res.quarter_mile_s:.2f} s")
    return " | ".join(parts) if parts else "no ET data captured"
