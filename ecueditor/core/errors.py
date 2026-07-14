from __future__ import annotations


class ECUEditorError(Exception):
    ...


class DefinitionError(ECUEditorError):
    ...


class NoMatchingRomError(ECUEditorError):
    ...


class TableError(ECUEditorError):
    ...


class ScalingError(ECUEditorError):
    ...


class ChecksumError(ECUEditorError):
    ...


class CommsError(ECUEditorError):
    ...


class CommsTimeout(CommsError):
    ...
