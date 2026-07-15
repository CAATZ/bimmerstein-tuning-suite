from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar


T = TypeVar("T")


class UndoHistory(Generic[T]):
    def __init__(self, equal: Callable[[T, T], bool], limit: int = 100) -> None:
        self._equal = equal
        self._limit = max(2, int(limit))
        self._items: list[T] = []
        self._index = -1

    def reset(self, value: T) -> None:
        self._items, self._index = [value], 0

    def clear(self) -> None:
        self._items, self._index = [], -1

    @property
    def current(self) -> T | None:
        return self._items[self._index] if 0 <= self._index < len(self._items) else None

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return 0 <= self._index < len(self._items) - 1

    def record(self, value: T) -> bool:
        if self.current is not None and self._equal(self.current, value):
            return False
        del self._items[self._index + 1 :]
        self._items.append(value)
        if len(self._items) > self._limit:
            del self._items[: len(self._items) - self._limit]
        self._index = len(self._items) - 1
        return True

    def undo(self) -> T | None:
        if not self.can_undo:
            return None
        self._index -= 1
        return self._items[self._index]

    def redo(self) -> T | None:
        if not self.can_redo:
            return None
        self._index += 1
        return self._items[self._index]
