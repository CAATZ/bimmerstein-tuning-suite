from ecueditor.ui.logger.gauges.base import GaugeBase  # noqa: F401
from ecueditor.ui.logger.gauges.digital import DigitalGauge
from ecueditor.ui.logger.gauges.needle import NeedleGauge
from ecueditor.ui.logger.gauges.bar import BarGauge
from ecueditor.ui.logger.gauges.sparkline import SparklineGauge

STYLES = ("digital", "needle", "bar-h", "bar-v", "sparkline")


def make_gauge(style: str, name: str, conversion) -> GaugeBase:
    if style == "needle":    return NeedleGauge(name, conversion)
    if style == "bar-h":     return BarGauge(name, conversion, orientation="h")
    if style == "bar-v":     return BarGauge(name, conversion, orientation="v")
    if style == "sparkline": return SparklineGauge(name, conversion)
    return DigitalGauge(name, conversion)
