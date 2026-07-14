"""Example third-party analysis tab (drop-in plugin) — proves extension point #5.

Plots wideband AFR against AFR target and reports the mean AFR error. Registered purely through the public
plugin API; adding it edits no existing file. Channel ids are ChannelMap-style and overridable.
"""
from __future__ import annotations
from ecueditor.core.plugins.registry import register
from ecueditor.core.logger.analysis.base import AnalysisResult

@register("analyses", "afr_target")
class AfrTargetAnalysis:
    id = "afr_target"
    title = "AFR Target"

    def __init__(self, afr_channel: str = "P58", target_channel: str = "P60") -> None:
        self.afr_channel = afr_channel
        self.target_channel = target_channel
        self.required_channels = (afr_channel, target_channel)
        self._points: list[tuple[float, float]] = []

    def accept(self, sample) -> None:
        v = sample.values
        if self.afr_channel in v and self.target_channel in v:
            self._points.append((v[self.target_channel], v[self.afr_channel]))

    def result(self) -> AnalysisResult:
        errs = [afr - tgt for tgt, afr in self._points]
        corrections = {"mean_afr_error": sum(errs) / len(errs)} if errs else {}
        return AnalysisResult(kind="afr_target", x_label="AFR Target", y_label="Wideband AFR",
                              points=tuple(self._points), corrections=corrections,
                              sample_count=len(self._points))

    def apply_to_rom(self, rom) -> list[str]:
        return ["AFR Target: read-only analysis, nothing written to ROM"]
