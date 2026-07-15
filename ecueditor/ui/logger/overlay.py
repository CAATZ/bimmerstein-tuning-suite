from __future__ import annotations
from collections import defaultdict

from ecueditor.core.logger.engine import Sample


class LiveOverlayBridge:
    """Fans a Sample out onto open editor tables, keyed by TableGridWidget.logparam.

    Consumes the Phase 2 TableGridWidget live-overlay contract (see INTERFACES.md):
      - grid.logparam: str | None   (the bound logger channel id, from TableDef.logparam)
      - grid.set_live_value(real)   (highlight the matching cell's live value)
    """
    def __init__(self) -> None:
        self._by_logparam: dict[str, list] = defaultdict(list)

    def register(self, grid) -> None:
        lp = getattr(grid, "logparam", None)
        if lp:
            if grid in self._by_logparam[lp]:     # C11a: idempotent -- re-registering the same
                return                            # grid (e.g. re-activating a doc) must not duplicate it
            self._by_logparam[lp].append(grid)

    def registered_count(self) -> int:
        return sum(len(grids) for grids in self._by_logparam.values())

    def unregister(self, grid) -> None:
        lp = getattr(grid, "logparam", None)
        if lp and grid in self._by_logparam.get(lp, ()):
            self._by_logparam[lp].remove(grid)

    def on_sample(self, sample: Sample) -> None:
        for logparam, grids in self._by_logparam.items():
            if logparam in sample.values:
                value = sample.values[logparam]
                for grid in list(grids):
                    try:
                        grid.set_live_value(value)
                    except RuntimeError:      # C++ widget deleted (subwindow closed via [X]) -- H9
                        grids.remove(grid)

    def clear(self) -> None:
        """Remove the last live highlight from every registered table."""
        for grids in self._by_logparam.values():
            for grid in list(grids):
                try:
                    grid.set_live_value(None)
                except RuntimeError:
                    grids.remove(grid)
