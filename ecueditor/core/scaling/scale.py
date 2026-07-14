from __future__ import annotations
from dataclasses import dataclass
from ecueditor.core.scaling.expression import compile_expression
from ecueditor.core.errors import ScalingError

@dataclass(frozen=True)
class Scale:
    units: str = "raw value"
    expression: str = "x"
    to_byte: str = ""
    format: str = "0.00"
    fine_increment: float = 1.0
    coarse_increment: float = 2.0
    category: str = "Raw Value"

    def to_real(self, raw: int) -> float:
        return compile_expression(self.expression).evaluate(raw)

    def to_raw(self, real: float) -> float:
        if self.to_byte.strip():
            return compile_expression(self.to_byte).evaluate(real)
        f = compile_expression(self.expression)
        if f.is_identity:
            return real
        f0, f1, f2 = f.evaluate(0), f.evaluate(1), f.evaluate(2)
        slope = f1 - f0
        if slope == 0 or abs((f2 - f1) - slope) > 1e-4:
            raise ScalingError(f"no analytic inverse for non-linear expression {self.expression!r}; "
                               f"supply to_byte")
        return (real - f0) / slope

    def decimals(self) -> int:
        return len(self.format.split(".", 1)[1]) if "." in self.format else 0

    def format_value(self, real: float) -> str:
        return f"{real:.{self.decimals()}f}"
