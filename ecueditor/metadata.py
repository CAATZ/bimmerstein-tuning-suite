from __future__ import annotations

import re


PRODUCT_NAME = "BimmerStein Tuning Suite"
PRODUCT_TAGLINE = "ECU Calibration and Data Logging"
PRODUCT_SLUG = "bimmerstein-tuning-suite"
WINDOWS_APP_STEM = "BimmerStein-Tuning-Suite"
PUBLISHER = "CAATZ"


_RELEASE_VERSION = re.compile(
    r"^(?P<release>\d+\.\d+\.\d+)(?:(?P<channel>a|b|rc)(?P<number>\d+))?$"
)
_CHANNEL_LABELS = {"a": "Alpha", "b": "Beta", "rc": "RC"}


def display_version(version: str) -> str:
    """Return a compact PEP 440 release version as a user-facing label."""
    match = _RELEASE_VERSION.fullmatch(version)
    if match is None or match.group("channel") is None:
        return version
    return (
        f"{match.group('release')} "
        f"{_CHANNEL_LABELS[match.group('channel')]} {match.group('number')}"
    )


def windows_numeric_version(version: str) -> str:
    """Convert a supported release version to Windows' four-part numeric form."""
    match = _RELEASE_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"Unsupported release version for Windows metadata: {version!r}")
    build = match.group("number") or "0"
    return f"{match.group('release')}.{build}"
