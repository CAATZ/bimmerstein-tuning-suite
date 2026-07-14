from __future__ import annotations
import functools
import operator
import re
from typing import Callable
from ecueditor.core.errors import ScalingError

_TOKEN = re.compile(r"<=|>=|==|!=|&&|\|\||[A-Za-z_]\w*|\d*\.\d+|\d+|[-+*/%(),<>!]")

class _Parser:
    def __init__(self, source: str) -> None:
        e = source.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
        self.toks = _TOKEN.findall(e)
        self.i = 0
        self.x = 0.0
    def peek(self):  return self.toks[self.i] if self.i < len(self.toks) else None
    def eat(self, t=None):
        c = self.peek()
        if t is not None and c != t: raise ValueError(f"expected {t!r} got {c!r}")
        self.i += 1; return c
    # or -> and -> cmp -> add -> mul -> unary -> primary
    def parse(self, x: float) -> float:              # <-- ms41log.py _P.parse: bind x, then descend
        self.x = x
        self.i = 0
        v = self._or()
        if self.peek() is not None:
            raise ValueError(f"trailing token {self.peek()!r}")
        return v
    def _or(self):
        v = self._and()
        while self.peek() == "||": self.eat(); v = 1.0 if (v != 0 or self._and() != 0) else 0.0
        return v
    def _and(self):
        v = self._cmp()
        while self.peek() == "&&": self.eat(); r = self._cmp(); v = 1.0 if (v != 0 and r != 0) else 0.0
        return v
    def _cmp(self):
        v = self._add()
        while self.peek() in ("<", ">", "<=", ">=", "==", "!="):
            op = self.eat(); r = self._add()
            v = float({"<": v < r, ">": v > r, "<=": v <= r, ">=": v >= r,
                       "==": v == r, "!=": v != r}[op])
        return v
    def _add(self):
        v = self._mul()
        while self.peek() in ("+", "-"):
            op = self.eat(); r = self._mul(); v = v + r if op == "+" else v - r
        return v
    def _mul(self):
        v = self._un()
        while self.peek() in ("*", "/", "%"):
            op = self.eat(); r = self._un()
            v = v * r if op == "*" else (v / r if op == "/" else v % r)
        return v
    def _un(self):
        t = self.peek()
        if t == "!": self.eat(); return 0.0 if self._un() != 0 else 1.0   # JEP logical NOT
        if t == "-": self.eat(); return -self._un()
        if t == "+": self.eat(); return self._un()
        return self._prim()
    def _prim(self):
        t = self.eat()
        if t == "(":
            v = self._or(); self.eat(")"); return v
        if t == "x" or t == "X": return float(self.x)
        if re.fullmatch(r"\d*\.\d+|\d+", t or ""): return float(t)
        if re.fullmatch(r"[A-Za-z_]\w*", t or ""):            # function call
            self.eat("(")
            args = []
            if self.peek() != ")":
                args.append(self._or())
                while self.peek() == ",": self.eat(); args.append(self._or())
            self.eat(")")
            return self._call(t, args)
        raise ValueError(f"unexpected token {t!r}")
    def _call(self, name, a):
        n = name.lower()
        if n == "bitwise":  return float(int(a[1]) & int(a[0]))     # BitWise(mask, x, shift): x & mask
        if n == "if":       return a[1] if a[0] != 0 else a[2]
        if n == "abs":      return abs(a[0])
        if n == "min":      return min(a)
        if n == "max":      return max(a)
        raise ValueError(f"unknown fn {name}()")

class _Compiler:
    """Same grammar as _Parser, but each production returns a closure; parse once, call many."""
    def __init__(self, source: str) -> None:
        e = source.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
        self.toks = _TOKEN.findall(e)
        self.i = 0
    def peek(self):  return self.toks[self.i] if self.i < len(self.toks) else None
    def eat(self, t=None):
        c = self.peek()
        if t is not None and c != t: raise ValueError(f"expected {t!r} got {c!r}")
        self.i += 1; return c
    def compile(self):
        f = self._or()
        if self.peek() is not None:
            raise ValueError(f"trailing token {self.peek()!r}")
        return f
    def _or(self):
        # NOTE: intentionally diverges from _Parser._or. _Parser evaluates operands while
        # parsing, so `a || b` short-circuits token consumption when `a` is truthy and then
        # raises "trailing token" — i.e. the reference parser's success depends on runtime x.
        # The compiler parses once and always consumes both operands, so || is input-independent.
        f = self._and()
        while self.peek() == "||":
            self.eat(); g = self._and()
            f = (lambda a, b: lambda x: 1.0 if (a(x) != 0 or b(x) != 0) else 0.0)(f, g)
        return f
    def _and(self):
        f = self._cmp()
        while self.peek() == "&&":
            self.eat(); g = self._cmp()
            f = (lambda a, b: lambda x: 1.0 if (a(x) != 0 and b(x) != 0) else 0.0)(f, g)
        return f
    def _cmp(self):
        f = self._add()
        while self.peek() in ("<", ">", "<=", ">=", "==", "!="):
            op = self.eat(); g = self._add()
            cmp = {"<": operator.lt, ">": operator.gt, "<=": operator.le,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}[op]
            f = (lambda a, b, c: lambda x: float(c(a(x), b(x))))(f, g, cmp)
        return f
    def _add(self):
        f = self._mul()
        while self.peek() in ("+", "-"):
            op = self.eat(); g = self._mul()
            f = ((lambda a, b: lambda x: a(x) + b(x)) if op == "+"
                 else (lambda a, b: lambda x: a(x) - b(x)))(f, g)
        return f
    def _mul(self):
        f = self._un()
        while self.peek() in ("*", "/", "%"):
            op = self.eat(); g = self._un()
            f = ((lambda a, b: lambda x: a(x) * b(x)) if op == "*" else
                 (lambda a, b: lambda x: a(x) / b(x)) if op == "/" else
                 (lambda a, b: lambda x: a(x) % b(x)))(f, g)
        return f
    def _un(self):
        t = self.peek()
        if t == "!": self.eat(); f = self._un(); return (lambda a: lambda x: 0.0 if a(x) != 0 else 1.0)(f)
        if t == "-": self.eat(); f = self._un(); return (lambda a: lambda x: -a(x))(f)
        if t == "+": self.eat(); return self._un()
        return self._prim()
    def _prim(self):
        t = self.eat()
        if t == "(":
            f = self._or(); self.eat(")"); return f
        if t == "x" or t == "X": return lambda x: float(x)
        if re.fullmatch(r"\d*\.\d+|\d+", t or ""):
            v = float(t); return lambda x: v
        if re.fullmatch(r"[A-Za-z_]\w*", t or ""):
            self.eat("(")
            args = []
            if self.peek() != ")":
                args.append(self._or())
                while self.peek() == ",": self.eat(); args.append(self._or())
            self.eat(")")
            return self._call(t, args)
        raise ValueError(f"unexpected token {t!r}")
    def _call(self, name, a):
        n = name.lower()
        if n == "bitwise":  return (lambda a0, a1: lambda x: float(int(a1(x)) & int(a0(x))))(a[0], a[1])
        if n == "if":       return (lambda c, t, e: lambda x: t(x) if c(x) != 0 else e(x))(a[0], a[1], a[2])
        if n == "abs":      return (lambda a0: lambda x: abs(a0(x)))(a[0])
        if n == "min":      return (lambda fs: lambda x: min(f(x) for f in fs))(tuple(a))
        if n == "max":      return (lambda fs: lambda x: max(f(x) for f in fs))(tuple(a))
        raise ValueError(f"unknown fn {name}()")

class Expression:
    _fn: Callable[[float], float]

    def __init__(self, source: str) -> None:
        self._source = source
        self._identity = source.strip().lower() == "x"
        if self._identity:
            self._fn = lambda x: float(x)
        else:
            # Compile eagerly: constructing an Expression with bad grammar must raise ScalingError.
            try:
                self._fn = _Compiler(source).compile()
            except Exception as exc:  # noqa: BLE001
                raise ScalingError(f"bad expression {source!r}: {exc}") from exc
        self.evaluate(0.0)           # eager runtime validation (division by zero etc. surfaces per-call)

    @property
    def source(self) -> str: return self._source
    @property
    def is_identity(self) -> bool: return self._identity

    def evaluate(self, x: float) -> float:
        if self._identity:
            return float(x)
        try:
            return round(self._fn(x), 5)
        except ScalingError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ScalingError(f"bad expression {self._source!r}: {exc}") from exc

@functools.lru_cache(maxsize=None)
def compile_expression(source: str) -> Expression:
    """Cached Expression factory, keyed on the exact source string.

    Expression compiles its source ONCE into a closure tree at construction and evaluate()
    calls that precompiled closure (no per-call parsing), so sharing one compiled instance
    across repeated evaluations of the same source is sound. maxsize=None is fine: sources
    come from a finite definition corpus (a few thousand distinct expression strings at most),
    not an unbounded input space.
    """
    return Expression(source)

def evaluate(source: str, x: float) -> float:
    return compile_expression(source).evaluate(x)
