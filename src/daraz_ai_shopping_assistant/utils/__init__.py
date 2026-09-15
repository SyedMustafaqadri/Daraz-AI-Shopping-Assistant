"""Shared helpers used across layers.

Keep this package dependency-free: nothing here should import from
``services``, ``scrapers``, or ``parsers``. Utilities are leaves.
"""

from __future__ import annotations

from daraz_ai_shopping_assistant.utils.datetime import PKT, pkt_now

__all__ = ["PKT", "pkt_now"]
