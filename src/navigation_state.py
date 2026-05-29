from __future__ import annotations


class NavigationHistory:
    def __init__(self) -> None:
        self._entries: list[str] = []
        self._position = -1

    def record(self, path: str) -> None:
        if self._position < len(self._entries) - 1:
            self._entries = self._entries[: self._position + 1]

        if not self._entries or self._entries[-1] != path:
            self._entries.append(path)
            self._position = len(self._entries) - 1

    def go_back(self) -> str | None:
        if not self.can_go_back():
            return None
        self._position -= 1
        return self._entries[self._position]

    def go_forward(self) -> str | None:
        if not self.can_go_forward():
            return None
        self._position += 1
        return self._entries[self._position]

    def can_go_back(self) -> bool:
        return self._position > 0

    def can_go_forward(self) -> bool:
        return self._position < len(self._entries) - 1
