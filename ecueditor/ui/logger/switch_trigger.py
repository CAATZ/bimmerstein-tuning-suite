from __future__ import annotations
from typing import Callable, Sequence

from ecueditor.core.loggerdef.channel import LoggerChannel
from ecueditor.core.logger.engine import Sample
from ecueditor.ui.logger.log_controls import CsvLogSession


class SwitchTriggeredLogger:
    """Starts/stops a CsvLogSession from a selected channel's value (fact base §3.1)."""
    def __init__(self, session: CsvLogSession,
                 channels_provider: Callable[[], Sequence[LoggerChannel]]) -> None:
        self._session = session
        # C11b: resolve the channel set at trigger/start time, not at arm/construction time --
        # a frozen list here would ignore selection changes made after arming.
        self._channels_provider = channels_provider
        self._armed = False
        self._switch_id: str | None = None
        self._threshold = 0.5
        self._absolute = False
        self._infix = ""
        self._trigger_active = False

    def channels(self) -> list[LoggerChannel]:
        """Current channel set from the provider (fresh each call -- reflects live selection)."""
        return list(self._channels_provider())

    def arm(self, *, switch_id: str, threshold: float = 0.5,
            absolute_time: bool = False, name_infix: str = "") -> None:
        self._armed = True
        self._switch_id = switch_id
        self._threshold = threshold
        self._absolute = absolute_time
        self._infix = name_infix

    def disarm(self) -> None:
        self._armed = False

    def on_sample(self, sample: Sample) -> None:
        # Evaluate the trigger FIRST, then write exactly once. This makes the crossing sample
        # classify before we decide to record it: a start-crossing sample IS recorded (the
        # session is active by the trailing write); a stop-crossing sample is NOT (the session
        # is stopped before the trailing write). Forwarding before evaluating would append the
        # stop sample as an extra row (the M15 defect: 3 rows written where 2 are expected).
        if self._armed and self._switch_id is not None:
            val = sample.values.get(self._switch_id)
            if val is not None:
                active = val >= self._threshold
                if active and not self._session.is_active:
                    self._session.start(self._channels_provider(), absolute_time=self._absolute,
                                        name_infix=self._infix)
                    self._trigger_active = True
                elif not active and self._session.is_active and self._trigger_active:
                    self._session.stop()
                    self._trigger_active = False
        # single write point: record iff the session is (still / now) active
        if self._session.is_active:
            self._session.on_sample(sample)
