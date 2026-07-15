from __future__ import annotations
import contextlib
import logging
import xml.etree.ElementTree as ET
from typing import Callable, Sequence

from PySide6.QtCore import Qt, QSignalBlocker, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel,
                               QMainWindow, QMenuBar, QMessageBox, QPushButton, QStatusBar,
                               QVBoxLayout, QWidget)

from ecueditor.core.comms.transport.base import list_ports
from ecueditor.core.dyno.profile import CarProfile
from ecueditor.core.dyno.run import VEHICLE_SPEED
from ecueditor.core.errors import ECUEditorError
from ecueditor.core.settings import EditorSettings, save_settings
from ecueditor.metadata import PRODUCT_NAME
from ecueditor.ui.logger.controller import LoggerController
from ecueditor.ui.logger.profile import apply_profile, load_profile, profile_from_panel, save_profile
from ecueditor.ui.logger.selection_panel import ParameterSelectionPanel
from ecueditor.ui.logger.switch_trigger import SwitchTriggeredLogger
from ecueditor.ui.workspace.status_chips import Chip

ControllerFactory = Callable[[str], LoggerController]

_log = logging.getLogger(__name__)


def _speed_units_kmh(definition) -> bool:
    """Return whether the dyno's vehicle-speed channel reports km/h."""
    try:
        chan = definition.by_id(VEHICLE_SPEED)
    except (KeyError, AttributeError):
        return False
    units = getattr(getattr(chan, "conversion", None), "units", "") or ""
    return "km" in units.lower()


class LoggerWindow(QMainWindow):
    connected = Signal(str)        # ECU-ID
    disconnected = Signal()

    def __init__(self, definition, *, controller_factory: ControllerFactory,
                 port_lister: Callable[[], list[str]] = list_ports,
                 profiles: Sequence[CarProfile] = (),
                 settings: EditorSettings | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"{PRODUCT_NAME} — Logger")
        self._definition = definition
        self._controller_factory = controller_factory
        self._port_lister = port_lister
        self._controller: LoggerController | None = None
        self._controller_error = False
        self._disconnect_in_progress = False
        self._settings = settings

        # --- connection bar ---
        bar = QWidget()
        bar.setObjectName("loggerConnectionBar")
        row = QHBoxLayout(bar)

        # File menu (Phase 8c): `bar` occupies the QMainWindow menu-bar slot via setMenuWidget()
        # below, so a plain self.menuBar() would be hidden behind it -- embed a real QMenuBar at
        # the left of this same row instead of relocating the connection bar (decision #1).
        menubar = QMenuBar(bar)
        file_menu = menubar.addMenu("&File")
        self.action_load_profile = QAction("Load Profile…", self)
        self.action_save_profile = QAction("Save Profile…", self)
        self.action_set_log_dir = QAction("Set Log Directory…", self)
        file_menu.addAction(self.action_load_profile)
        file_menu.addAction(self.action_save_profile)
        file_menu.addSeparator()
        file_menu.addAction(self.action_set_log_dir)
        self.action_load_profile.triggered.connect(self._load_profile)
        self.action_save_profile.triggered.connect(self._save_profile)
        self.action_set_log_dir.triggered.connect(self._set_log_directory)
        row.addWidget(menubar)

        self.port_combo = QComboBox()
        self.poll_mode_combo = QComboBox()
        self.poll_mode_combo.addItem("Auto (fast batch)", "auto")
        self.poll_mode_combo.addItem("Compatible", "memory")
        self.poll_mode_combo.setToolTip(
            "Fast batch uses one DS2 0x0B telegram for eligible definition channels and grouped reads "
            "for the rest. Compatible uses individual DS2 memory reads."
        )
        self.refresh_button = QPushButton("Refresh")
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)
        row.addWidget(QLabel("COM port:"))
        row.addWidget(self.port_combo)
        row.addWidget(QLabel("Polling:"))
        row.addWidget(self.poll_mode_combo)
        row.addWidget(self.refresh_button)
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        row.addStretch(1)
        self.setMenuWidget(bar)

        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_clicked)
        self.disconnect_button.clicked.connect(self.disconnect_clicked)
        self.poll_mode_combo.currentIndexChanged.connect(lambda _i: self._on_selection_changed())

        # --- selection dock (split channels into Parameters vs Switches per the
        #     LoggerDefinition.parameters()/switches() contract) ---
        self.selection_panel = ParameterSelectionPanel()
        params, switches = self._split_channels(definition)
        self._switch_channels = list(switches)
        self.selection_panel.set_channels(params)
        self.selection_panel.set_switches(switches)
        dock = QDockWidget("Selection")
        dock.setWidget(self.selection_panel)
        self.selection_dock = dock
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        # --- status bar: 8a chips for connection / target / ECU-ID / CAL-ID / query stats
        #     (Phase 8c, C13/D4) -- kind carries the color semantics, no inline setStyleSheet.
        sb = QStatusBar()
        self._chips: dict[str, Chip] = {
            "conn": Chip("● Disconnected", "neutral"),
            "target": Chip(f"Target 0x{getattr(definition, 'module_address', 0):02X}", "neutral"),
            "ecu": Chip("ECU —", "neutral"),
            "cal": Chip("CAL —", "neutral"),
            "mode": Chip("Compatible", "neutral"),
            "rate": Chip("— Hz", "neutral"),
            "polls": Chip("0 polls · 0 err", "neutral"),
            "rec": Chip("", "danger"),
        }
        self._chips["rec"].hide()
        for c in self._chips.values():
            sb.addWidget(c)
        self.setStatusBar(sb)

        self.refresh_ports()

        from ecueditor.ui.logger.data_tab import DataTab
        from ecueditor.ui.logger.graph_tab import GraphTab
        from ecueditor.ui.logger.dashboard_tab import DashboardTab
        from ecueditor.ui.logger.log_controls import LogControlsBar
        from PySide6.QtWidgets import QTabWidget

        self.data_tab = DataTab()
        self.graph_tab = GraphTab()
        self.dashboard_tab = DashboardTab()
        self.dashboard_tab.set_settings(settings)
        self.log_controls = LogControlsBar()
        self.log_controls.set_switch_channels(self._switch_channels)   # fill the switch-trigger combo
        self.tabs = QTabWidget()
        self.tabs.addTab(self.data_tab, "Data")
        self.tabs.addTab(self.graph_tab, "Graph")
        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        center = QWidget(); center.setObjectName("loggerCenter")
        center_lay = QVBoxLayout(center)
        center_lay.addWidget(self.tabs); center_lay.addWidget(self.log_controls)
        self.setCentralWidget(center)

        # Dyno + analysis tabs (Phase 6b): dyno is core RomRaider logger functionality; the
        # analysis set is driven by the ANALYSES registry (extension point #5).
        from ecueditor.core.logger.analysis.channels import ChannelMap
        from ecueditor.ui.dyno.tab import DynoTab
        from ecueditor.ui.logger.analysis.mount import build_analysis_tabs

        self.dyno_tab = DynoTab()
        self.dyno_tab.set_profiles(list(profiles))
        self.dyno_tab.set_speed_units_kmh(_speed_units_kmh(definition))
        self.tabs.addTab(self.dyno_tab, "Dyno")
        self.analysis_tabs = build_analysis_tabs(ChannelMap.ms41_defaults(), definition)
        for tab in self.analysis_tabs:
            self.tabs.addTab(tab, tab.title)
        self._analysis_rom = None
        self._failed_analysis_tabs: set[int] = set()   # id(tab) already logged (once per tab, H10)

        from ecueditor.ui.logger.overlay import LiveOverlayBridge
        from ecueditor.ui.logger.log_controls import CsvLogSession
        from pathlib import Path

        self.overlay = LiveOverlayBridge()
        csv_dir = (Path(settings.logger_csv_dir) if settings and settings.logger_csv_dir
                  else Path.home())
        self._csv = CsvLogSession(out_dir=csv_dir)
        self._csv.subscribe(self._on_csv_state_changed)
        self._switch_logger = SwitchTriggeredLogger(self._csv,
                                                    channels_provider=self._selected_channels)
        self._controller_sample_hook = None

        self.log_controls.startRequested.connect(self._start_csv)
        self.log_controls.stopRequested.connect(self._stop_csv)
        self.log_controls.switchTriggerChanged.connect(self._arm_switch)

        def _act(text, key, slot):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        self.action_toggle_log = _act("Start/Stop File Logging", "F1",
                                      self.log_controls.toggle_logging)
        self.action_unselect_all = _act("Unselect All", "F9",
                                        self.selection_panel.unselect_all)
        self.action_toggle_panel = _act("Toggle Selection Panel", "F11",
                                        lambda: self.selection_dock.setVisible(
                                            not self.selection_dock.isVisible()))
        self.action_cycle_style = _act("Cycle Gauge/Graph Style", "F12",
                                       self._cycle_style)

        self.selection_panel.selectionChanged.connect(self._on_selection_changed)
        if settings is not None and settings.logger_selections:
            # Last-session restore (C3): mirrors _on_started's debounce (review #1) -- block
            # every pane's table signals across both restore loops so the single
            # _on_selection_changed() call below does the one real rebuild, instead of a
            # signal storm from each check()/set_view_checked() call.
            checked = settings.logger_selections.get("poll", [])
            view_snapshot = {v: set(settings.logger_selections.get(v, []))
                             for v in ("livedata", "graph", "dash")}
            with contextlib.ExitStack() as stack:
                for pane in self.selection_panel._panes:
                    stack.enter_context(QSignalBlocker(pane))
                    stack.enter_context(QSignalBlocker(pane._table))
                for cid in checked:
                    try:
                        self.selection_panel.check(cid)
                    except KeyError:
                        pass   # persisted id not resolvable for this definition
                survivors = set(self.selection_panel.selected_ids())
                for view, ids in view_snapshot.items():
                    for cid in survivors:
                        self.selection_panel.set_view_checked(cid, view, cid in ids)
        self._on_selection_changed()

    def chip(self, name: str) -> Chip:
        return self._chips[name]

    def _set_chip(self, name: str, text: str, kind: str | None = None) -> None:
        chip = self._chips[name]
        chip.setText(text)
        if kind is not None:
            chip.set_kind(kind)

    def _cycle_style(self) -> None:
        # F12 cycles gauge styles only (C9): combine/split for the Graph tab stays on its own
        # button, uncoupled from this shortcut.
        self.dashboard_tab.cycle_gauge_style()

    @staticmethod
    def _split_channels(definition):
        """(parameters, switches) via the LoggerDefinition.parameters()/switches() contract;
        falls back to LoggerChannel.is_switch for stub/duck-typed definitions."""
        if hasattr(definition, "parameters") and hasattr(definition, "switches"):
            return list(definition.parameters()), list(definition.switches())
        channels = list(getattr(definition, "channels", []))
        params = [c for c in channels if not getattr(c, "is_switch", False)]
        switches = [c for c in channels if getattr(c, "is_switch", False)]
        return params, switches

    def _channels_for(self, ids: set[str]):
        params, switches = self._split_channels(self._definition)
        return [
            c.with_units(self.selection_panel.units_for(c.id))
            for c in (*params, *switches) if c.id in ids
        ]

    def _on_selection_changed(self) -> None:
        # Route per-view flags immediately and atomically replace the live engine poll set.
        panel = self.selection_panel
        self.data_tab.set_channels(self._channels_for(set(panel.view_ids("livedata"))))
        self.graph_tab.set_channels(self._channels_for(set(panel.view_ids("graph"))))
        self.dashboard_tab.set_channels(self._channels_for(set(panel.view_ids("dash"))))
        if self._controller is not None and self._controller.is_running:
            self._controller.update_selection(
                panel.selected_ids(),
                poll_mode=self.poll_mode_combo.currentData() or "auto",
                units=panel.units_map(),
            )
            self._show_selection_report()

    # --- ports ---
    def refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItems(self._port_lister())
        if current:
            idx = self.port_combo.findText(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)

    # --- connection ---
    @property
    def is_connected(self) -> bool:
        return self._controller is not None and self._controller.is_running

    def connect_clicked(self) -> None:
        if self.is_connected:
            return
        port = self.port_combo.currentText()
        try:
            self._controller = self._controller_factory(port)
        except ECUEditorError as exc:      # real transport/protocol factories raise on open/init
            self.statusBar().showMessage(f"Connect failed: {exc}", 5000)
            self._set_chip("conn", "● Error", "warn")
            return
        self._controller.started.connect(self._on_started)
        self._controller.statsUpdated.connect(self._on_stats)
        self._controller.modeUpdated.connect(self._on_mode)
        self._controller.errorOccurred.connect(self._on_error)
        self._controller.sampleReady.connect(self._dispatch_sample)
        self._controller.stopped.connect(self._on_controller_finished)
        self._controller_error = False
        try:
            self._controller.start(
                self.selection_panel.selected_ids(),
                poll_mode=self.poll_mode_combo.currentData() or "auto",
                units=self.selection_panel.units_map(),
            )
        except ECUEditorError as exc:
            self._controller.stop()
            self._controller = None
            self.statusBar().showMessage(f"Connect failed: {exc}", 5000)
            self._on_error(str(exc))
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
            return
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self._set_chip("conn", "● Connected", "ok")

    def _on_started(self, ecu_id: str) -> None:
        self._set_chip("ecu", f"ECU {ecu_id}", "info")   # blue (mockup); M Red reserved for alarm/REC
        self._show_selection_report()
        # Refine the panes to channels resolvable for the live ECU-ID, PRESERVING the user's
        # current selection across the repopulate. A bare set_channels() would rebuild every row
        # UNCHECKED and wipe the picks that _start_csv/_arm_switch depend on (see Task 13).
        if hasattr(self._definition, "for_ecu") and ecu_id:
            checked = set(self.selection_panel.selected_ids())
            units_snapshot = self.selection_panel.units_map(checked)
            view_snapshot = {view: set(self.selection_panel.view_ids(view))
                             for view in ("livedata", "graph", "dash")}
            resolved = self._definition.for_ecu(ecu_id)
            params = [c for c in resolved if not getattr(c, "is_switch", False)]
            switches = [c for c in resolved if getattr(c, "is_switch", False)]
            self._switch_channels = list(switches)
            self.selection_panel.set_channels(params)
            self.selection_panel.set_switches(switches)
            log_controls = getattr(self, "log_controls", None)   # exists once Task 12 lands
            if log_controls is not None:
                log_controls.set_switch_channels(switches)
            # Debounce (review #1): check() + set_view_checked() below fire ~4N+1
            # selectionChanged signals for N survivors, each triggering a full three-tab
            # rebuild via _on_selection_changed(). Block every pane's table signals across
            # both restore loops so the single _on_selection_changed() call at the end of
            # this method does the one real rebuild. Blocking also suppresses the col-0
            # auto-check-all-views cascade in _CheckboxList._on_item_changed, but that's
            # fine here: every view flag (on AND off) is explicitly re-applied below, so
            # the suppressed cascade can't leave a stale flag.
            with contextlib.ExitStack() as stack:
                for pane in self.selection_panel._panes:
                    stack.enter_context(QSignalBlocker(pane))
                    stack.enter_context(QSignalBlocker(pane._table))
                for cid in checked:
                    try:
                        self.selection_panel.check(cid)      # restore survivors; drop the rest
                    except KeyError:
                        pass   # channel not resolvable for this ECU-ID
                for cid, units in units_snapshot.items():
                    try:
                        self.selection_panel.set_units(cid, units)
                    except KeyError:
                        pass
                # check() above defaults all three view flags back on (RomRaider default) --
                # re-apply the pre-repopulate per-view flags so a channel the user toggled OFF
                # for one view (e.g. graph) doesn't silently reappear there after an ECU-ID
                # repopulate. selected_ids() is already a subset of `checked` (only ids in
                # `checked` can have been checked above), so no need to re-intersect.
                survivors = set(self.selection_panel.selected_ids())
                for view, ids in view_snapshot.items():
                    for cid in survivors:
                        self.selection_panel.set_view_checked(cid, view, cid in ids)
        self._on_selection_changed()
        # (spec §9.2/C4) feed the CAL chip. getattr-guarded: production always has a controller
        # here (started fires only after connect_clicked builds it), but tests drive _on_started
        # directly with _controller still None -> "" -> "—".
        self.set_cal_id(getattr(self._controller, "cal_id", "") or "—")
        self.connected.emit(ecu_id)

    def _show_selection_report(self) -> None:
        report = getattr(self._controller, "selection_report", None)
        if report is None or not report.unavailable:
            return
        unavailable = ", ".join(
            f"{channel_id} ({reason})" for channel_id, reason in report.unavailable.items()
        )
        self.statusBar().showMessage(f"Unavailable channels: {unavailable}", 10000)

    def _on_mode(self, status: str) -> None:
        self._set_chip("mode", status, "info" if status == "Fast batch" else "neutral")
        self._show_selection_report()

    def set_cal_id(self, cal_id: str) -> None:
        has_cal = bool(cal_id) and cal_id != "—"
        self._set_chip("cal", f"CAL {cal_id}", "info" if has_cal else "neutral")

    def _on_stats(self, stats) -> None:
        # stats is a LoggerStats (INTERFACES.md ui/logger): polls / errors / rate_hz
        self._set_chip("rate", f"{stats.rate_hz:.1f} Hz")
        self._set_chip("polls", f"{stats.polls} polls · {stats.errors} err")

    def _on_error(self, msg: str) -> None:
        self._controller_error = True
        self.statusBar().showMessage(f"Error: {msg}", 5000)
        self._set_chip("conn", "● Error", "warn")

    def register_editor_table(self, grid) -> None:
        self.overlay.register(grid)

    def unregister_editor_table(self, grid) -> None:
        self.overlay.unregister(grid)

    def set_active_rom(self, rom) -> None:
        """Set the active ROM used by analysis-tab apply actions; ``None`` disables writing."""
        self._analysis_rom = rom
        for tab in self.analysis_tabs:
            tab.set_rom(rom)

    def _selected_channels(self):
        ids = set(self.selection_panel.selected_ids())
        return [
            c.with_units(self.selection_panel.units_for(c.id))
            for c in getattr(self._definition, "channels", []) if c.id in ids
        ]

    def _start_csv(self, infix: str, absolute: bool) -> None:
        self._csv.start(self._selected_channels(), absolute_time=absolute, name_infix=infix)
        self._set_chip("rec", f"● REC {self._csv.current_filename()}", "danger")
        self._chips["rec"].show()

    def _stop_csv(self) -> None:
        self._csv.stop()
        self._chips["rec"].hide()
        self._chips["rec"].setText("")

    def _on_csv_state_changed(self, active: bool, filename: str) -> None:
        self.log_controls.set_logging(active)
        if active:
            self._set_chip("rec", f"REC {filename}", "danger")
            self._chips["rec"].show()
        else:
            self._chips["rec"].hide()
            self._chips["rec"].setText("")

    def _arm_switch(self, enabled: bool, switch_id: str) -> None:
        self._switch_logger = SwitchTriggeredLogger(self._csv,
                                                    channels_provider=self._selected_channels)
        if enabled and switch_id:
            self._switch_logger.arm(switch_id=switch_id,
                                    absolute_time=self.log_controls.absolute_time_check.isChecked(),
                                    name_infix=self.log_controls.name_infix_edit.text())

    # --- File menu: profiles + CSV directory (Phase 8c) ---
    def _load_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Profile", "", "Profile XML (*.xml)")
        if not path:
            return
        try:
            profile = load_profile(path)
        except (ET.ParseError, OSError) as exc:     # malformed/non-profile XML (review #3)
            QMessageBox.warning(self, "Load Profile", f"Could not load profile: {exc}")
            return
        # Debounce (review #1, same class as _on_started's repopulate and the construction-time
        # restore): apply_profile's per-entry check() + set_view_checked() calls each fire
        # selectionChanged -> _on_selection_changed (a full three-tab rebuild). Block every
        # pane's table signals across the apply so the single _on_selection_changed() call
        # below does the one real rebuild instead of ~4N redundant ones.
        with contextlib.ExitStack() as stack:
            for pane in self.selection_panel._panes:
                stack.enter_context(QSignalBlocker(pane))
                stack.enter_context(QSignalBlocker(pane._table))
            apply_profile(self.selection_panel, profile)
        if profile.port:
            idx = self.port_combo.findText(profile.port)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        if profile.logfile_dir:                    # profile wins over settings (C10)
            self._csv.out_dir = profile.logfile_dir
        self._on_selection_changed()

    def _save_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Profile", "", "Profile XML (*.xml)")
        if not path:
            return
        profile = profile_from_panel(self.selection_panel,
                                     port=self.port_combo.currentText() or None,
                                     logfile_dir=str(self._csv.out_dir))
        save_profile(path, profile)

    def _set_log_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Set Log Directory")
        if not directory:
            return
        self._csv.out_dir = directory
        if self._settings is not None:
            self._settings.logger_csv_dir = directory
            save_settings(self._settings)

    def _dispatch_sample(self, sample) -> None:
        # runs on the UI thread (queued from the controller) — safe to touch widgets
        self.data_tab.update_sample(sample)
        self.graph_tab.update_sample(sample)
        self.dashboard_tab.update_sample(sample)
        self.dyno_tab.on_sample(sample)          # dyno capture (Phase 6b)
        for tab in self.analysis_tabs:           # analysis engines gate/skip internally (Phase 6b)
            # Third-party engine isolation (final-review Important #1): a plugin's accept()/
            # result() raising here must not starve the CSV writer/overlay below (H10). Rich
            # first-party tabs (data/graph/dashboard/dyno) above stay unguarded -- this mirrors
            # the build-time per-entry isolation in mount.py (commit d98e8be).
            try:
                tab.on_sample(sample)
            except (Exception, SystemExit) as exc:
                key = id(tab)
                if key not in self._failed_analysis_tabs:
                    self._failed_analysis_tabs.add(key)
                    _log.warning("analysis tab %r failed on sample: %s",
                                getattr(tab, "title", tab), exc, exc_info=exc)
        # CSV logging: the switch logger is the single CSV writer. Disarmed, it forwards each
        # sample to the active manual _csv session (its trailing write); armed, it start/stops
        # _csv on the switch crossing and writes. Calling self._csv.on_sample here too would
        # double every row (final-review C1).
        self._switch_logger.on_sample(sample)
        self.overlay.on_sample(sample)           # live overlay onto editor tables
        if self._controller_sample_hook is not None:
            self._controller_sample_hook(sample)

    def disconnect_clicked(self) -> None:
        self._controller_error = False
        if self._controller is not None:
            self._disconnect_in_progress = True
            try:
                self._controller.stop()
            finally:
                self._disconnect_in_progress = False
            self._controller = None
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self._set_chip("conn", "● Disconnected", "neutral")
        self.overlay.clear()
        self.disconnected.emit()

    def _on_controller_finished(self) -> None:
        if self._disconnect_in_progress:
            return
        had_error = self._controller_error
        self.disconnect_clicked()
        if had_error:
            self._controller_error = True
            self._set_chip("conn", "● Error", "warn")

    def closeEvent(self, event) -> None:          # T11-m1: [X] must equal Disconnect (C2)
        if self._settings is not None:            # persist last-session selection (C3)
            self._settings.logger_selections = {
                "poll": self.selection_panel.selected_ids(),
                **{v: self.selection_panel.view_ids(v) for v in ("livedata", "graph", "dash")},
            }
            save_settings(self._settings)
        self._stop_csv()
        self._switch_logger.disarm()
        if self._controller is not None:
            self.disconnect_clicked()
        event.accept()
        super().closeEvent(event)


def launch_logger_window(definition, *, controller_factory: ControllerFactory,
                         port_lister: Callable[[], list[str]] = list_ports,
                         profiles: Sequence[CarProfile] = (),
                         settings: EditorSettings | None = None,
                         parent: QWidget | None = None) -> LoggerWindow:
    """Entry point wired to the editor's Logger menu/toolbar action (fact base §1.5 openLogger)."""
    win = LoggerWindow(definition, controller_factory=controller_factory,
                       port_lister=port_lister, profiles=profiles, settings=settings, parent=parent)
    win.show()
    return win
