from __future__ import annotations
from PySide6 import QtWidgets
import pyqtgraph as pg
from ecueditor.core.logger.analysis.maf import MafAnalysis, MafFilters
from ecueditor.core.logger.analysis.channels import ChannelMap
from ecueditor.core.errors import ECUEditorError
from ecueditor.ui.logger.analysis.common import confirm_reapply_dialog

class MafTab(QtWidgets.QWidget):
    title = "MAF"

    def __init__(self, channel_map: ChannelMap, parent=None) -> None:
        super().__init__(parent)
        self.engine = MafAnalysis(channel_map=channel_map, filters=MafFilters())
        self._recording = False
        self._rom = None                       # set by the main window via set_rom(); gates "Update MAF"
        self._definition = None                # set by the window via set_definition(); gates roles
        self._applied = False                  # apply-once guard (H1): armed by a non-raising apply
        self.confirm_reapply = lambda text: confirm_reapply_dialog(self, text)
        f = self.engine.filters

        # --- plot: scatter of accepted points + fitted polynomial curve ---
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "MAF Sensor Voltage (V)")
        self.plot.setLabel("left", "Total Fuel Trim (%)")
        self.scatter = pg.ScatterPlotItem(size=6)
        self.fit_curve = pg.PlotCurveItem()
        self.plot.addItem(self.scatter)
        self.plot.addItem(self.fit_curve)

        # --- filter fields: one QDoubleSpinBox per MafFilters threshold, named <field>_field, each seeded
        #     from the engine's current default so apply_filters() is a no-op until a field is edited ---
        def _dspin(value: float, lo: float, hi: float, decimals: int) -> QtWidgets.QDoubleSpinBox:
            box = QtWidgets.QDoubleSpinBox()
            box.setDecimals(decimals); box.setRange(lo, hi); box.setValue(value)
            return box
        self.afr_min_field      = _dspin(f.afr_min,        0.0,  100.0,   2)
        self.afr_max_field      = _dspin(f.afr_max,        0.0,  100.0,   2)
        self.rpm_min_field      = _dspin(f.rpm_min,        0.0,  20000.0, 0)
        self.rpm_max_field      = _dspin(f.rpm_max,        0.0,  20000.0, 0)
        self.maf_min_field      = _dspin(f.maf_min,        0.0,  2000.0,  2)
        self.maf_max_field      = _dspin(f.maf_max,        0.0,  2000.0,  2)
        self.mafv_min_field     = _dspin(f.mafv_min,       0.0,  5.0,     2)
        self.mafv_max_field     = _dspin(f.mafv_max,       0.0,  5.0,     2)
        self.ect_min_field      = _dspin(f.ect_min,      -50.0,  300.0,   1)
        self.iat_max_field      = _dspin(f.iat_max,      -50.0,  300.0,   1)
        self.dmafv_dt_max_field = _dspin(f.dmafv_dt_max,   0.0,  1e6,     3)
        self.tip_in_max_field   = _dspin(f.tip_in_max,     0.0,  1e6,     2)

        # --- polynomial order (3..20), seeded from MafFilters.poly_order ---
        self.order_field = QtWidgets.QSpinBox()
        self.order_field.setRange(3, 20); self.order_field.setValue(f.poly_order)

        # --- buttons; each wired to an explicit slot ---
        self.record_btn = QtWidgets.QPushButton("Record"); self.record_btn.setCheckable(True)
        self.interp_btn = QtWidgets.QPushButton("Interpolate")
        self.update_btn = QtWidgets.QPushButton("Update MAF")
        self.reset_btn  = QtWidgets.QPushButton("Reset")
        self.record_btn.toggled.connect(self.set_recording)     # toggled(bool) -> set_recording(bool)
        self.interp_btn.clicked.connect(self.do_interpolate)    # clicked(bool) ignored by the 0-arg slot
        # clicked emits a bool; do_update_maf needs a RomImage, so route through _request_update — NOT
        # .connect(self.do_update_maf), which would pass the bool as `rom`.
        self.update_btn.clicked.connect(lambda: self._request_update())
        self.reset_btn.clicked.connect(self.reset)
        self.update_btn.setEnabled(False)                       # enabled once set_rom() attaches a ROM

        # keep engine filters live as the user edits fields (MAF filters gate accept-time admission,
        # so edits must reach the engine while recording). These fire only on a real edit, never
        # during the seeding setValue() calls above (connected after) -- mirrors injector_tab.py.
        for box in (self.afr_min_field, self.afr_max_field, self.rpm_min_field, self.rpm_max_field,
                    self.maf_min_field, self.maf_max_field, self.mafv_min_field, self.mafv_max_field,
                    self.ect_min_field, self.iat_max_field, self.dmafv_dt_max_field, self.tip_in_max_field):
            box.valueChanged.connect(lambda _=None: self.apply_filters())
        self.order_field.valueChanged.connect(lambda _=None: self.apply_filters())

        # --- channel-config: a QComboBox per role, editable so any logger-def id can be bound ---
        self._role_combos: dict[str, QtWidgets.QComboBox] = {}
        chan_box = QtWidgets.QGroupBox("Channels")
        chan_form = QtWidgets.QFormLayout(chan_box)
        for role, cid in self.engine.channel_map.roles.items():
            combo = QtWidgets.QComboBox(); combo.setEditable(True)
            combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
            combo.setCurrentText(cid)
            # Rebind on activated/editingFinished, NOT per-keystroke currentTextChanged: a
            # half-typed id must never reach the engine while recording (H2/H3, backlog
            # "Phase 6 exit").
            combo.activated.connect(lambda _i, r=role: self._rebind_role(r))
            combo.lineEdit().editingFinished.connect(lambda r=role: self._rebind_role(r))
            self._role_combos[role] = combo
            chan_form.addRow(role, combo)

        # --- status sink: engine notes / caught errors land here (no message boxes, no math) ---
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)

        # --- layout: filters + order + channels + buttons on the left, plot on the right ---
        filt = QtWidgets.QFormLayout()
        for label, box in (("AFR min", self.afr_min_field), ("AFR max", self.afr_max_field),
                           ("RPM min", self.rpm_min_field), ("RPM max", self.rpm_max_field),
                           ("MAF min", self.maf_min_field), ("MAF max", self.maf_max_field),
                           ("MAFv min", self.mafv_min_field), ("MAFv max", self.mafv_max_field),
                           ("ECT min", self.ect_min_field), ("IAT max", self.iat_max_field),
                           ("dMAFv/dt max", self.dmafv_dt_max_field), ("Tip-in max", self.tip_in_max_field),
                           ("Poly order", self.order_field)):
            filt.addRow(label, box)
        btns = QtWidgets.QHBoxLayout()
        for b in (self.record_btn, self.interp_btn, self.update_btn, self.reset_btn):
            btns.addWidget(b)
        controls = QtWidgets.QVBoxLayout()
        controls.addLayout(filt); controls.addWidget(chan_box); controls.addLayout(btns)
        controls.addWidget(self.status_label)
        root = QtWidgets.QHBoxLayout(self)
        root.addLayout(controls); root.addWidget(self.plot, stretch=1)

    def set_recording(self, on: bool) -> None:
        self._recording = on

    def on_sample(self, sample) -> None:
        if self._recording:
            self.engine.accept(sample)

    def apply_filters(self) -> None:
        f = self.engine.filters
        f.afr_min      = self.afr_min_field.value()
        f.afr_max      = self.afr_max_field.value()
        f.rpm_min      = self.rpm_min_field.value()
        f.rpm_max      = self.rpm_max_field.value()
        f.maf_min      = self.maf_min_field.value()
        f.maf_max      = self.maf_max_field.value()
        f.mafv_min     = self.mafv_min_field.value()
        f.mafv_max     = self.mafv_max_field.value()
        f.ect_min      = self.ect_min_field.value()
        f.iat_max      = self.iat_max_field.value()
        f.dmafv_dt_max = self.dmafv_dt_max_field.value()
        f.tip_in_max   = self.tip_in_max_field.value()
        f.poly_order   = self.order_field.value()

    def do_interpolate(self) -> None:
        self.apply_filters()
        try:
            self.engine.interpolate(self.order_field.value())
        except ValueError as exc:               # e.g. too few points for the order
            self._show_notes([str(exc)])
            return
        self.refresh_plot()
        self._show_notes([])                    # success: clear any stale error

    def set_rom(self, rom) -> None:
        if rom is not self._rom:
            self._applied = False              # a DIFFERENT ROM re-arms; same-rom re-fires from the
                                               # window's MDI activation hook must not (H1)
        self._rom = rom
        self._refresh_gate()

    def _request_update(self) -> list[str]:
        if self._rom is None:
            notes = ["Update MAF: no ROM loaded"]
        elif self._applied and not self.confirm_reapply(
                "MAF corrections were already applied to this ROM.\n"
                "Applying again multiplies the correction onto itself (RomRaider apply-once "
                "semantics). Apply again anyway?"):
            notes = ["Update MAF: already applied — skipped (Reset or load another ROM to re-arm)"]
        else:
            self.apply_filters()
            try:
                notes = self.do_update_maf(self._rom)
                self._applied = True           # arm on ANY non-raising apply (conservative: a
                                               # "table not found" note also arms; re-arm via Reset)
            except (ValueError, ECUEditorError) as exc:   # H5: TableError etc. must not escape the slot
                notes = [str(exc)]
        self._show_notes(notes)                # every outcome lands in the status label
        return notes

    def do_update_maf(self, rom) -> list[str]:
        return self.engine.apply_to_rom(rom)      # delegates entirely to core

    def refresh_plot(self) -> None:
        xs = [p[0] for p in self.engine.points]; ys = [p[1] for p in self.engine.points]
        self.scatter.setData(xs, ys)
        res = self.engine.result()
        if res.fit_x:
            self.fit_curve.setData(list(res.fit_x), list(res.fit_y))
        else:
            self.fit_curve.setData([], [])

    def _show_notes(self, notes: list[str]) -> None:
        self.status_label.setText("\n".join(notes))

    def set_channel(self, role: str, channel_id: str) -> None:
        self.engine.channel_map = self.engine.channel_map.with_overrides({role: channel_id})
        self._refresh_gate()

    def _rebind_role(self, role: str) -> None:
        self.set_channel(role, self._role_combos[role].currentText().strip())

    def set_definition(self, definition) -> None:
        """(Phase 6b) Populate the role combos from the live LoggerDefinition and refresh the
        configure-channels affordance (backlog "Phase 6 exit" wiring pre-condition)."""
        self._definition = definition
        ids = [c.id for c in getattr(definition, "channels", [])]
        for combo in self._role_combos.values():
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear(); combo.addItems(ids)
            combo.setCurrentText(current)              # binding survives the repopulate
            combo.blockSignals(False)
        self._refresh_gate()

    def missing_roles(self) -> list[str]:
        if self._definition is None:
            return []
        return self.engine.channel_map.missing(self.engine.required_roles, self._definition)

    def _refresh_gate(self) -> None:
        missing = self.missing_roles()
        self.update_btn.setEnabled(self._rom is not None and not missing)
        if missing:
            bound = self.engine.channel_map.roles
            self._show_notes(["Configure channels — not in the loaded logger definition: "
                              + ", ".join(f"{r} ({bound[r]})" for r in missing)])
        elif self.status_label.text().startswith("Configure channels"):
            self._show_notes([])                       # affordance resolved: clear the message

    def reset(self) -> None:
        self.engine.reset()                    # clears points AND the stale fit -- see MafAnalysis.reset
        self._applied = False                  # fresh data cycle: guard re-arms (H1)
        self.scatter.setData([], []); self.fit_curve.setData([], [])
        self._show_notes([])
