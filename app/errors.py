from __future__ import annotations


class LabError(Exception):
    """An operation the page can explain to a visitor."""

    def __init__(self, status_code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.hint = hint