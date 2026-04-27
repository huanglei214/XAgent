from __future__ import annotations


class WorkspaceEscapeError(ValueError):
    """Raised when a tool path escapes the current workspace root."""
