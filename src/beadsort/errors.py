"""Error types. Every error carries a stable code for the JSON envelope and an exit code."""

from __future__ import annotations

from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_BD = 3
EXIT_API = 4
EXIT_PARTIAL = 5


class BeadsortError(Exception):
    """Base error. `code` is a stable snake_case identifier for machine consumers."""

    exit_code = EXIT_ERROR

    def __init__(self, message: str, *, code: str = "error", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


class UsageError(BeadsortError):
    exit_code = EXIT_USAGE

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message, code="usage", detail=detail)


class ApiError(BeadsortError):
    """The model API refused or failed, or no key was available."""

    exit_code = EXIT_API


class PartialApplyError(BeadsortError):
    exit_code = EXIT_PARTIAL

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message, code="partial_apply", detail=detail)
