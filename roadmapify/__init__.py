"""roadmapify — a phased build plan with memory that survives context loss."""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("roadmapify")
except Exception:  # not installed (running from a checkout)
    __version__ = "0.1.0"

__all__ = ["__version__"]
