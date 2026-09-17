"""JSON-file-backed persistence for scrape payloads.

A single JSON document on disk holds validated scrape payloads keyed by a
canonical string. The file is loaded once at startup and written back
atomically after every change. There is no external database -- this is
the smallest persistence layer that satisfies the "fetch once, serve many"
requirement without adding a runtime dependency.

Design:

    - One JSON object at the top level, mapping cache key -> entry.
    - Each entry has: kind, payload, stored_at, expires_at.
    - Expiry is per-entry and enforced on read.
    - Writes are atomic: temp file + os.replace.
    - All writes are serialised through an asyncio.Lock.
    - A corrupt or missing file is treated as an empty store. The app
      does not crash on startup if the file is malformed.

Concurrency:

    FastAPI under uvicorn runs a single event loop. Concurrent requests
    can interleave at await points, so the lock protects the (rare) write
    path. The read path only performs a dict lookup and an expiry check,
    which is safe without the lock.

Non-goals:

    - No eviction policy. The store grows until the file is deleted. A
      single warning is logged once the file crosses a size threshold.
    - No migrations. The schema is stable; if it changes, delete the file.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from daraz_ai_shopping_assistant.core.logging import get_logger
from daraz_ai_shopping_assistant.utils.datetime import pkt_now

logger = get_logger(__name__)

#: Emit a one-shot warning once the store file grows past this size.
_DEFAULT_WARN_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB

class ScrapeStore:
    """In-memory dict of scrape payloads, persisted to a JSON file.

    Attributes:
        _path: Filesystem path of the backing JSON file.
        _entries: The full key -> entry mapping currently in memory.
        _write_lock: Serialises every write so a concurrent set() cannot
            interleave with a persist.
        _warn_size_bytes: Threshold above which a size warning is logged.
        _warned_large: Whether the size warning has already been emitted.
    """

    def __init__(
        self,
        path: Path,
        *,
        entries: dict[str, dict[str, Any]] | None = None,
        warn_size_bytes: int = _DEFAULT_WARN_SIZE_BYTES,
    ) -> None:
        """Initialise the store. Prefer :meth:`load` over direct construction.

        Args:
            path: Filesystem path to write to on the next persist.
            entries: Optional initial entries. Defaults to empty.
            warn_size_bytes: Threshold for the one-shot size warning.
        """
        self._path: Path = path
        self._entries: dict[str, dict[str, Any]] = entries or {}
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._warn_size_bytes: int = warn_size_bytes
        self._warned_large: bool = False

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        warn_size_bytes: int = _DEFAULT_WARN_SIZE_BYTES,
    ) -> ScrapeStore:
        """Load the store from disk, tolerating a missing or corrupt file.

        A missing file bootstraps an empty store and creates the parent
        directory. A corrupt file logs a warning and bootstraps empty --
        the app does not crash on bad persisted state.

        Expired entries are pruned at load time so a restart does not
        resurrect them.

        Args:
            path: Filesystem path to the JSON file.
            warn_size_bytes: Threshold for the one-shot size warning.

        Returns:
            A ready-to-use ``ScrapeStore``.
        """
        resolved = Path(path)

        if not resolved.exists():
            resolved.parent.mkdir(parents=True, exist_ok=True)
            logger.info(
                "SCRAPE_STORE_CREATED",
                extra={"ctx": {"path": str(resolved)}},
            )
            return cls(resolved, warn_size_bytes=warn_size_bytes)

        try:
            raw = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "SCRAPE_STORE_LOAD_FAILED",
                extra={"ctx": {"path": str(resolved), "error": type(exc).__name__}},
            )
            return cls(resolved, warn_size_bytes=warn_size_bytes)

        if not raw.strip():
            logger.info(
                "SCRAPE_STORE_LOADED",
                extra={"ctx": {"path": str(resolved), "entries": 0, "dropped_expired": 0}},
            )
            return cls(resolved, warn_size_bytes=warn_size_bytes)

        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "SCRAPE_STORE_LOAD_FAILED",
                extra={
                    "ctx": {
                        "path": str(resolved),
                        "error": "json_decode",
                        "detail": str(exc)[:120],
                    }
                },
            )
            return cls(resolved, warn_size_bytes=warn_size_bytes)

        if not isinstance(parsed, dict):
            logger.warning(
                "SCRAPE_STORE_LOAD_FAILED",
                extra={"ctx": {"path": str(resolved), "error": "not_an_object"}},
            )
            return cls(resolved, warn_size_bytes=warn_size_bytes)

        pruned: dict[str, dict[str, Any]] = {}
        for key, entry in parsed.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            if cls._entry_expired(entry):
                continue
            pruned[key] = entry

        logger.info(
            "SCRAPE_STORE_LOADED",
            extra={
                "ctx": {
                    "path": str(resolved),
                    "entries": len(pruned),
                    "dropped_expired": len(parsed) - len(pruned),
                }
            },
        )
        return cls(resolved, entries=pruned, warn_size_bytes=warn_size_bytes)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get(self, key: str) -> dict[str, Any] | None:
        """Return the payload stored under ``key``.

        Expired entries are dropped lazily from memory. The file is not
        rewritten on a read-driven expiry -- the next :meth:`set` call
        persists the cleaned state.

        Args:
            key: Canonical cache key.

        Returns:
            The stored payload dict, or ``None`` when the key is missing,
            expired, or holds a non-dict payload.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._entry_expired(entry):
            del self._entries[key]
            return None
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            return None
        return payload

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    async def set(
        self,
        key: str,
        *,
        kind: str,
        payload: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        """Insert or replace the entry for ``key`` and persist to disk.

        Args:
            key: Canonical cache key.
            kind: A short tag identifying the payload type ("search" or
                "product"). Stored for diagnostics only.
            payload: The JSON-serialisable payload to store.
            ttl_seconds: Seconds until the entry expires.
        """
        async with self._write_lock:
            now = pkt_now()
            self._entries[key] = {
                "kind": kind,
                "payload": payload,
                "stored_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            }
            self._persist_locked()

    async def prune(self) -> int:
        """Drop every expired entry and persist.

        Returns:
            The number of entries removed.
        """
        async with self._write_lock:
            expired_keys = [
                key
                for key, entry in self._entries.items()
                if self._entry_expired(entry)
            ]
            for key in expired_keys:
                del self._entries[key]
            if expired_keys:
                self._persist_locked()
            return len(expired_keys)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _persist_locked(self) -> None:
        """Write the entries dict to disk atomically.

        Caller must hold ``self._write_lock``. Writes to a temp file in
        the same directory, then ``os.replace`` swaps it into place. On
        POSIX and Windows this is atomic within the same filesystem.
        """
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(self._entries, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning(
                "SCRAPE_STORE_WRITE_FAILED",
                extra={"ctx": {"path": str(self._path), "error": type(exc).__name__}},
            )
            return

        size = self._path.stat().st_size
        if size > self._warn_size_bytes and not self._warned_large:
            logger.warning(
                "SCRAPE_STORE_LARGE",
                extra={
                    "ctx": {
                        "path": str(self._path),
                        "size_bytes": size,
                        "entries": len(self._entries),
                    }
                },
            )
            self._warned_large = True

    @staticmethod
    def _entry_expired(entry: dict[str, Any]) -> bool:
        """Return True when ``entry`` is malformed or past its expiry.

        A malformed entry is treated as expired so a bad write cannot
        keep stale data alive indefinitely.

        Args:
            entry: A single entry dict as stored on disk.

        Returns:
            Whether the entry should be dropped.
        """
        expires_raw = entry.get("expires_at")
        if not isinstance(expires_raw, str):
            return True
        try:
            expires_at = datetime.fromisoformat(expires_raw)
        except ValueError:
            return True
        if expires_at.tzinfo is None:
            return True
        return pkt_now() >= expires_at

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    @property
    def size(self) -> int:
        """Number of entries currently held in memory."""
        return len(self._entries)

    @property
    def path(self) -> Path:
        """Filesystem path of the backing file."""
        return self._path

__all__ = ["ScrapeStore"]
