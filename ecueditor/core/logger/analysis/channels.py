from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

ROLES: tuple[str, ...] = (
    "maf_voltage", "maf", "rpm", "iat", "ect", "afr", "load",
    "pulse_width", "learning", "correction", "closed_loop", "throttle",
)

# MS41 defaults — verified ids where known; inferred ids flagged in the plan's role table (fact base 7.4).
_MS41_DEFAULTS: dict[str, str] = {
    "maf_voltage": "P18", "maf": "P12", "rpm": "P8", "iat": "P11", "ect": "P2",
    "afr": "P58", "load": "E2", "pulse_width": "P21",
    "learning": "E19", "correction": "E13", "closed_loop": "E218", "throttle": "E23",
}

@dataclass(frozen=True)
class ChannelMap:
    roles: Mapping[str, str]

    @classmethod
    def ms41_defaults(cls) -> "ChannelMap":
        return cls(MappingProxyType(dict(_MS41_DEFAULTS)))

    def with_overrides(self, overrides: Mapping[str, str]) -> "ChannelMap":
        merged = dict(self.roles)
        for role, cid in overrides.items():
            if role not in ROLES:
                raise KeyError(f"unknown analysis role {role!r}")
            merged[role] = cid
        return ChannelMap(MappingProxyType(merged))

    def resolve(self, roles: Sequence[str]) -> tuple[str, ...]:
        out: list[str] = []
        for role in roles:
            if role not in self.roles:
                raise KeyError(f"role {role!r} not bound in this ChannelMap")
            out.append(self.roles[role])
        return tuple(out)

    def missing(self, roles: Sequence[str], definition) -> list[str]:
        """Roles whose bound channel id is absent from the loaded logger definition."""
        def _present(cid: str) -> bool:
            has = getattr(definition, "has", None)
            if callable(has):
                return bool(has(cid))
            try:
                definition.by_id(cid); return True
            except KeyError:
                return False
        return [r for r in roles if r in self.roles and not _present(self.roles[r])]
