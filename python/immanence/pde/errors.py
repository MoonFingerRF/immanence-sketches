"""What can go wrong in a sketch, as one exception type the caller can report without a traceback."""
from __future__ import annotations


class PdeError(Exception):
    """A sketch that cannot be read, compiled or run to the end of a frame.

    `kind`: syntax | compile | runtime | budget (steps) | deadline (wall clock) | memory | depth
    (recursion or nesting) | size (not 240x128). `line` is 1-based in the sketch, 0 when unknown."""

    def __init__(self, kind: str, message: str, line: int = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.line = line

    def __str__(self) -> str:
        return f"{self.kind}: {self.message}" + (f" (line {self.line})" if self.line else "")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message[:200], "line": self.line}
