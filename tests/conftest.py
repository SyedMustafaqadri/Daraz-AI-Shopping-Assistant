"""Session-wide pytest configuration.

Ensures ``FIRECRAWL_API_KEY`` is set before any module that reads
``core.config.settings`` is imported. Without this, importing the FastAPI
app in tests would fail at module load time.

Individual tests that need to exercise the "missing key" path still work
-- they use ``monkeypatch.delenv`` and pass ``_env_file=None`` to bypass
this default.
"""

from __future__ import annotations

import os

# Set a dummy key before pytest imports any test module. Real requests are
# never made in the unit suite (the scraper is always mocked), so the
# value is never used against Firecrawl.
os.environ.setdefault("FIRECRAWL_API_KEY", "fc-test-key")
