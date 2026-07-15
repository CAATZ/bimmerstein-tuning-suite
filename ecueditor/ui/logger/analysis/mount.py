from __future__ import annotations
import logging
from typing import Callable
from PySide6 import QtWidgets
import pyqtgraph as pg
from ecueditor.core.errors import ECUEditorError
from ecueditor.core.logger.analysis.channels import ChannelMap
from ecueditor.core.plugins.registry import ANALYSES
from ecueditor.ui.logger.analysis.common import confirm_reapply_dialog
from ecueditor.ui.logger.analysis.injector_tab import InjectorTab
from ecueditor.ui.logger.analysis.maf_tab import MafTab

_log = logging.getLogger(__name__)

# Rich (hand-built) UI per engine id; every other ANALYSES entry gets GenericAnalysisTab.
# The callable return union preserves the shared rich-tab constructor and ``set_definition``
# contract without erasing them to QWidget's constructor signature.
RichTabFactory = Callable[..., MafTab | InjectorTab]
RICH_TABS: dict[str, RichTabFactory] = {"maf": MafTab, "injector": InjectorTab}


class GenericAnalysisTab(QtWidgets.QWidget):
    """Minimal host for any AnalysisTab-protocol engine (extension point #5): scatter + fit from
    result(), a Record gate, and Apply-to-ROM behind the SAME apply-once guard as the rich tabs."""

    def __init__(self, engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.title = getattr(engine, "title", engine.id)
        self._recording = False
        self._rom = None
        self._applied = False
        self.confirm_reapply: Callable[[str], bool] = lambda text: confirm_reapply_dialog(self, text)

        self.plot = pg.PlotWidget()
        self.scatter = pg.ScatterPlotItem(size=6); self.plot.addItem(self.scatter)
        self.fit_curve = pg.PlotCurveItem(); self.plot.addItem(self.fit_curve)

        self.record_btn = QtWidgets.QPushButton("Record"); self.record_btn.setCheckable(True)
        self.apply_btn = QtWidgets.QPushButton("Apply to ROM"); self.apply_btn.setEnabled(False)
        self.status_label = QtWidgets.QLabel(""); self.status_label.setWordWrap(True)
        self.record_btn.toggled.connect(self._set_recording)
        # clicked emits a bool; route through the 0-arg guard slot (Phase-6 lambda pattern)
        self.apply_btn.clicked.connect(lambda: self._request_apply())

        controls = QtWidgets.QVBoxLayout()
        for w in (self.record_btn, self.apply_btn, self.status_label):
            controls.addWidget(w)
        controls.addStretch(1)
        root = QtWidgets.QHBoxLayout(self)
        root.addLayout(controls); root.addWidget(self.plot, stretch=1)

    def _set_recording(self, on: bool) -> None:
        self._recording = on

    def on_sample(self, sample) -> None:
        if not self._recording:
            return
        self.engine.accept(sample)             # protocol engines gate/guard internally
        self.refresh_plot()

    def refresh_plot(self) -> None:
        res = self.engine.result()
        self.plot.setLabel("bottom", res.x_label); self.plot.setLabel("left", res.y_label)
        self.scatter.setData([p[0] for p in res.points], [p[1] for p in res.points])
        self.fit_curve.setData(list(res.fit_x), list(res.fit_y))

    def set_rom(self, rom) -> None:
        if rom is not self._rom:
            self._applied = False              # same guard contract as the rich tabs (H1)
        self._rom = rom
        self.apply_btn.setEnabled(rom is not None)

    def _request_apply(self) -> list[str]:
        if self._rom is None:
            notes = [f"{self.title}: no ROM loaded"]
        elif self._applied and not self.confirm_reapply(
                f"{self.title} was already applied to this ROM. Apply again anyway?"):
            notes = [f"{self.title}: already applied — skipped"]
        else:
            try:
                candidate_notes = self.engine.apply_to_rom(self._rom)
                if not isinstance(candidate_notes, list) or not all(
                    isinstance(note, str) for note in candidate_notes
                ):
                    raise TypeError("apply_to_rom must return list[str]")
                notes = candidate_notes
                self._applied = True
            except (ValueError, ECUEditorError) as exc:
                notes = [str(exc)]
            except (Exception, SystemExit) as exc:
                detail = str(exc) or type(exc).__name__
                _log.warning(
                    "analysis engine %r failed to apply: %s",
                    self.title,
                    detail,
                    exc_info=exc,
                )
                notes = [f"{self.title}: apply failed: {detail}"]
        self.status_label.setText("\n".join(notes))
        return notes


AnalysisTabWidget = MafTab | InjectorTab | GenericAnalysisTab


def build_analysis_tabs(channel_map: ChannelMap, definition, parent=None) -> list[AnalysisTabWidget]:
    """One widget per ANALYSES registry entry, in registration order (builtins first, then
    load_plugins() discoveries). Engine build convention: factory(channel_map=...) first, fall back
    to factory() on TypeError for engines that bind channels themselves (plugins/afr_target_tab.py).
    A non-rich engine whose construction raises is skipped with a logged warning (per-entry
    isolation: one malformed plugin cannot abort the tab list). Rich ids stay fail-fast — they are
    first-party builtins; a failure there is a real bug that must surface."""
    tabs: list[AnalysisTabWidget] = []
    for key in ANALYSES.keys():
        if key in RICH_TABS:
            rich_tab = RICH_TABS[key](channel_map=channel_map, parent=parent)
            rich_tab.set_definition(definition)
            tab: AnalysisTabWidget = rich_tab
        else:
            factory = ANALYSES.get(key)
            try:
                try:
                    engine = factory(channel_map=channel_map)
                except TypeError:
                    engine = factory()
                tab = GenericAnalysisTab(engine, parent=parent)
            except (Exception, SystemExit) as exc:  # optional extension degrades gracefully
                _log.warning("analysis engine %r failed to build: %s", key, exc, exc_info=exc)
                continue
        tabs.append(tab)
    return tabs
