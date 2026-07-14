from __future__ import annotations
import json
from pathlib import Path
from typing import Mapping, Sequence, Union
from ecueditor.core.comms.transport.base import SerialParams
from ecueditor.core.errors import CommsTimeout
from ecueditor.core.plugins.registry import register

_Resp = Union[bytes, bytearray, Sequence[bytes]]

@register("transports", "replay")
class ReplayTransport:
    """No-hardware transport: maps a written request to a scripted response (or response queue).

    Values are normalized to a list; each write pops the next until one remains (which repeats).
    This makes flaky/retry scenarios scriptable, e.g. {req: [bad_frame, good_frame]}.
    """
    name = "replay"

    def __init__(self, script: Mapping[bytes, _Resp] | None = None) -> None:
        self._script: dict[bytes, list[bytes]] = {}
        for key, val in (script or {}).items():
            self._script[bytes(key)] = self._as_list(val)
        self._rx = bytearray()
        self._open = False
        self.written: list[bytes] = []

    @staticmethod
    def _as_list(val: _Resp) -> list[bytes]:
        if isinstance(val, (bytes, bytearray)):
            return [bytes(val)]
        return [bytes(v) for v in val]

    def load_script(self, script: Mapping[bytes, _Resp]) -> None:
        for key, val in script.items():
            self._script[bytes(key)] = self._as_list(val)

    @classmethod
    def from_file(cls, path: str | Path) -> "ReplayTransport":
        """JSON of hex strings: {"120400": "12..." | ["12..","12.."]}."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        script: dict[bytes, _Resp] = {}
        for k, v in raw.items():
            resp: _Resp = bytes.fromhex(v) if isinstance(v, str) else [bytes.fromhex(x) for x in v]
            script[bytes.fromhex(k)] = resp
        return cls(script)

    def open(self, port: str, params: SerialParams) -> None:
        self._open = True

    def write(self, data: bytes) -> None:
        data = bytes(data)
        self.written.append(data)
        queue = self._script.get(data)
        if not queue:
            return                      # unknown request -> silence (caller times out)
        resp = queue.pop(0) if len(queue) > 1 else queue[0]
        self._rx += resp

    def read(self, n: int, timeout_ms: int) -> bytes:
        if len(self._rx) < n:
            self._rx.clear()
            raise CommsTimeout(f"replay short read: wanted {n}, had fewer")
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def flush_input(self) -> None:
        self._rx.clear()

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open
