"""Timezone helpers.

The backend standardises every timestamp on Pakistan Standard Time (PKT,
UTC+05:00). Daraz renders all times in PKT, and ``SearchResult.scraped_at``
requires a timezone-aware datetime, so we always produce values in this
zone.

Why a fixed offset instead of ``zoneinfo.ZoneInfo("Asia/Karachi")``:

    - Pakistan has not observed DST since 2009, so a fixed +05:00 offset is
      always correct for the era this application operates in.
    - ``ZoneInfo`` depends on the ``tzdata`` package on Windows. The fixed
      offset has no runtime dependency.
    - Comparisons and serialisation behave identically.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Pakistan Standard Time. Fixed UTC+05:00 offset.
PKT: timezone = timezone(timedelta(hours=5), name="PKT")

def pkt_now() -> datetime:
    """Return the current time in Pakistan Standard Time.

    Returns:
        A timezone-aware ``datetime`` with ``tzinfo=PKT``.

    Example:
        >>> ts = pkt_now()
        >>> ts.tzinfo is PKT
        True
    """
    return datetime.now(tz=PKT)

__all__ = ["PKT", "pkt_now"]
