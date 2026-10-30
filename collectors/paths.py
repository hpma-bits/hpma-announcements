"""Repository locations used by the collector modules.

Tests build throwaway roots, so every collector function takes ``root`` and
falls back to :data:`REPO_ROOT`.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
