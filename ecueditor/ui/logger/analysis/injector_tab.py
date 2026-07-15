from __future__ import annotations
from PySide6 import QtWidgets
import pyqtgraph as pg
from ecueditor.core.logger.analysis.injector import InjectorAnalysis, InjectorParams, InjectorFilters
from ecueditor.core.logger.analysis.channels import ChannelMap
from ecueditor.core.errors import ECUEditorError
from ecueditor.ui.logger.analysis.common import confirm_reapply_dialog

class InjectorTab(QtWidgets.QWidget):
    title = "Injector"

    def __init__(self, channel_map: ChannelMap, parent=None) -> None:
        super().__init__(parent)
        self.engine = InjectorAnalysis(channel_map=channel_map, params=InjectorParams(),
                                       filters=InjectorFilters())
        self._recording = False
        self._rom = None                       # set by the main window via set_rom(); gates "Update Injector"
        self._definition = None                # set by the window via set_definition(); gates roles
        self._applied = False                  # apply-once guard (H1): armed by a non-raising apply
        self.confirm_reapply = lambda text: confirm_reapply_dialog(self, text)
        p = self.engine.params
        f = self.engine.filters

        # --- plot: scatter of (pulse width, fuel cc) + degree-1 regression line ---
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Injector Pulse Width (ms)")
        self.plot.setLabel("left", "Fuel (cc)")
        self.scatter = pg.ScatterPlotItem(size=6)
        self.fit_line = pg.PlotCurveItem()
        self.plot.addItem(self.scatter)
        self.plot.addItem(self.fit_line)

        def _dspin(value: float, lo: float, hi: float, decimals: int) -> QtWidgets.QDoubleSpinBox:
            box = QtWidgets.QDoubleSpinBox()
            box.setDecimals(decimals); box.setRange(lo, hi); box.setValue(value)
            return box

        # --- fuel params (drive fuelcc; must be current before samples are accepted) ---
        self.stoich_field  = _dspin(p.stoich_afr,   1.0,  30.0,   2)
        self.density_field = _dspin(p.fuel_density, 1.0,  2000.0, 1)

        # --- MAF-style validity filter fields the injector gate uses, seeded from engine defaults ---
        self.afr_min_field      = _dspin(f.afr_min,        0.0,  100.0,   2)
        self.afr_max_field      = _dspin(f.afr_max,        0.0,  100.0,   2)
        self.rpm_min_field      = _dspin(f.rpm_min,        0.0,  20000.0, 0)
        self.rpm_max_field      = _dspin(f.rpm_max,        0.0,  20000.0, 0)
        self.maf_min_field      = _dspin(f.maf_min,        0.0,  2000.0,  2)
        self.maf_max_field      = _dspin(f.maf_max,        0.0,  2000.0,  2)
        self.ect_min_field      = _dspin(f.ect_min,      -50.0,  300.0,   1)
        self.iat_max_field      = _dspin(f.iat_max,      -50.0,  300.0,   1)
        self.dmafv_dt_max_field = _dspin(f.dmafv_dt_max,   0.0,  1e6,     3)
        self.tip_in_max_field   = _dspin(f.tip_in_max,     0.0,  1e6,     2)

        # --- buttons; each wired to an explicit slot ---
        self.record_btn = QtWidgets.QPushButton("Record"); self.record_btn.setCheckable(True)
        self.update_btn = QtWidgets.QPushButton("Update Injector")
        self.reset_btn  = QtWidgets.QPushButton("Reset")
        self.record_btn.toggled.connect(self.set_recording)     # toggled(bool) -> set_recording(bool)
        # clicked emits a bool; do_update needs a RomImage -> route through _request_update, NOT
        # .connect(self.do_update), which would pass the bool as `rom`.
        self.update_btn.clicked.connect(lambda: self._request_update())
        self.reset_btn.clicked.connect(self.reset)
        self.update_btn.setEnabled(False)                       # enabled once set_rom() attaches a ROM

        # keep engine params/filters live as the user edits fields (params feed fuelcc at accept time).
        # These fire only on a real edit, never during the seeding setValue() above (connected after).
        self.stoich_field.valueChanged.connect(lambda _=None: self.apply_params())
        self.density_field.valueChanged.connect(lambda _=None: self.apply_params())
        for box in (self.afr_min_field, self.afr_max_field, self.rpm_min_field, self.rpm_max_field,
                    self.maf_min_field, self.maf_max_field, self.ect_min_field, self.iat_max_field,
                    self.dmafv_dt_max_field, self.tip_in_max_field):
            box.valueChanged.connect(lambda _=None: self.apply_filters())

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
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.editingFinished.connect(lambda r=role: self._rebind_role(r))
            self._role_combos[role] = combo
            chan_form.addRow(role, combo)

        # --- status sink: engine notes / caught errors land here (no message boxes, no math) ---
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)

        # --- layout ---
        form = QtWidgets.QFormLayout()
        for label, box in (("Stoich AFR", self.stoich_field), ("Fuel density", self.density_field),
                           ("AFR min", self.afr_min_field), ("AFR max", self.afr_max_field),
                           ("RPM min", self.rpm_min_field), ("RPM max", self.rpm_max_field),
                           ("MAF min", self.maf_min_field), ("MAF max", self.maf_max_field),
                           ("ECT min", self.ect_min_field), ("IAT max", self.iat_max_field),
                           ("dMAFv/dt max", self.dmafv_dt_max_field), ("Tip-in max", self.tip_in_max_field)):
            form.addRow(label, box)
        btns = QtWidgets.QHBoxLayout()
        for b in (self.record_btn, self.update_btn, self.reset_btn):
            btns.addWidget(b)
        controls = QtWidgets.QVBoxLayout()
        controls.addLayout(form); controls.addWidget(chan_box); controls.addLayout(btns)
        controls.addWidget(self.status_label)
        root = QtWidgets.QHBoxLayout(self)
        root.addLayout(controls); root.addWidget(self.plot, stretch=1)

    def set_recording(self, on: bool) -> None:
        self._recording = on

    def on_sample(self, sample) -> None:
        if self._recording:
            self.engine.accept(sample)

    def apply_params(self) -> None:
        self.engine.params = InjectorParams(stoich_afr=self.stoich_field.value(),
                                            fuel_density=self.density_field.value())

    def apply_filters(self) -> None:
        f = self.engine.filters
        f.afr_min      = self.afr_min_field.value()
        f.afr_max      = self.afr_max_field.value()
        f.rpm_min      = self.rpm_min_field.value()
        f.rpm_max      = self.rpm_max_field.value()
        f.maf_min      = self.maf_min_field.value()
        f.maf_max      = self.maf_max_field.value()
        f.ect_min      = self.ect_min_field.value()
        f.iat_max      = self.iat_max_field.value()
        f.dmafv_dt_max = self.dmafv_dt_max_field.value()
        f.tip_in_max   = self.tip_in_max_field.value()

    def set_rom(self, rom) -> None:
        if rom is not self._rom:
            self._applied = False              # a DIFFERENT ROM re-arms; same-rom re-fires from the
                                               # window's MDI activation hook must not (H1)
        self._rom = rom
        self._refresh_gate()

    def _request_update(self) -> list[str]:
        if self._rom is None:
            notes = ["Update Injector: no ROM loaded"]
        elif self._applied and not self.confirm_reapply(
                "Injector corrections were already applied to this ROM.\n"
                "Applying again ADDS the latency offset on top of the last write (RomRaider "
                "apply-once semantics). Apply again anyway?"):
            notes = ["Update Injector: already applied — skipped (Reset or load another ROM to re-arm)"]
        else:
            try:
                notes = self.do_update(self._rom)
                self._applied = True
            except (ValueError, ECUEditorError) as exc:   # H5
                notes = [str(exc)]
        self._show_notes(notes)                 # every outcome lands in the status label
        return notes

    def do_update(self, rom) -> list[str]:
        return self.engine.apply_to_rom(rom)      # delegates entirely to core (flow + latency)

    def refresh_plot(self) -> None:
        xs = [pt[0] for pt in self.engine.points]; ys = [pt[1] for pt in self.engine.points]
        self.scatter.setData(xs, ys)
        res = self.engine.result()
        if res.fit_x:
            self.fit_line.setData(list(res.fit_x), list(res.fit_y))
        else:
            self.fit_line.setData([], [])

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
        self.engine.reset()                    # clears points and rate-gate baselines
        self._applied = False                  # fresh data cycle: guard re-arms (H1)
        self.scatter.setData([], []); self.fit_line.setData([], [])
        self._show_notes([])
