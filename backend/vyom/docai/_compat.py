"""Temporary compatibility helpers while the P1 platform scaffold is absent."""

from __future__ import annotations

try:  # P1's shared error type takes precedence once it is added.
    from vyom.errors import PipelineError  # type: ignore[import-not-found]
except ImportError:
    class PipelineError(Exception):
        """P1-compatible pipeline exception used until vyom.errors is available."""

        def __init__(self, code: str, message: str, details: dict | None = None):
            super().__init__(message)
            self.code = code
            self.message = message
            self.details = details or {}
