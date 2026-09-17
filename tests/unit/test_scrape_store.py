"""Unit tests for the JSON-backed ScrapeStore.

Every test uses ``tmp_path`` so nothing touches the real ``data/``
directory. No network, no LLM, no Firecrawl.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from daraz_ai_shopping_assistant.storage.json_store import ScrapeStore
from daraz_ai_shopping_assistant.utils.datetime import pkt_now


# ---------------------------------------------------------------------- #
# Construction
# ---------------------------------------------------------------------- #
def test_load_creates_parent_directory(tmp_path: Path) -> None:
    """Loading from a missing nested path creates the parent directory."""
    target = tmp_path / "deeply" / "nested" / "store.json"
    store = ScrapeStore.load(target)
    assert target.parent.exists()
    assert store.size == 0

def test_load_from_missing_file_is_empty(tmp_path: Path) -> None:
    """A missing file produces an empty store, not an error."""
    store = ScrapeStore.load(tmp_path / "missing.json")
    assert store.size == 0
    assert store.get("anything") is None

def test_load_from_empty_file_is_empty(tmp_path: Path) -> None:
    """A blank file produces an empty store."""
    target = tmp_path / "empty.json"
    target.write_text("", encoding="utf-8")
    store = ScrapeStore.load(target)
    assert store.size == 0

def test_load_from_corrupt_file_is_empty(tmp_path: Path) -> None:
    """A corrupt JSON file produces an empty store, not a crash."""
    target = tmp_path / "corrupt.json"
    target.write_text("{not valid json", encoding="utf-8")
    store = ScrapeStore.load(target)
    assert store.size == 0

def test_load_prunes_expired_entries(tmp_path: Path) -> None:
    """Expired entries are dropped at load time."""
    target = tmp_path / "store.json"
    now = pkt_now()
    target.write_text(
        json.dumps(
            {
                "fresh": {
                    "kind": "search",
                    "payload": {"search_query": "x"},
                    "stored_at": now.isoformat(),
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                },
                "stale": {
                    "kind": "search",
                    "payload": {"search_query": "y"},
                    "stored_at": (now - timedelta(days=2)).isoformat(),
                    "expires_at": (now - timedelta(days=1)).isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )
    store = ScrapeStore.load(target)
    assert store.size == 1
    assert store.get("fresh") is not None
    assert store.get("stale") is None

def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    """Entries missing required fields are dropped."""
    target = tmp_path / "store.json"
    target.write_text(
        json.dumps(
            {
                "no_expires": {"kind": "search", "payload": {}},
                "bad_expires": {
                    "kind": "search",
                    "payload": {},
                    "stored_at": "x",
                    "expires_at": "not-a-datetime",
                },
                "good": {
                    "kind": "search",
                    "payload": {"ok": True},
                    "stored_at": pkt_now().isoformat(),
                    "expires_at": (pkt_now() + timedelta(hours=1)).isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )
    store = ScrapeStore.load(target)
    assert store.size == 1
    assert store.get("good") == {"ok": True}

# ---------------------------------------------------------------------- #
# get / set
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_set_then_get_returns_payload(tmp_path: Path) -> None:
    """A stored payload is returned verbatim by get."""
    store = ScrapeStore.load(tmp_path / "store.json")
    payload = {"search_query": "mouse", "products": []}
    await store.set("search:mouse", kind="search", payload=payload, ttl_seconds=60)
    assert store.get("search:mouse") == payload

@pytest.mark.asyncio()
async def test_set_writes_file(tmp_path: Path) -> None:
    """A successful set persists the entry to disk."""
    target = tmp_path / "store.json"
    store = ScrapeStore.load(target)
    await store.set(
        "product:i1",
        kind="product",
        payload={"id": "i1"},
        ttl_seconds=60,
    )
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert "product:i1" in raw
    assert raw["product:i1"]["kind"] == "product"
    assert raw["product:i1"]["payload"] == {"id": "i1"}

@pytest.mark.asyncio()
async def test_set_overwrites_existing_key(tmp_path: Path) -> None:
    """A second set for the same key replaces the payload."""
    store = ScrapeStore.load(tmp_path / "store.json")
    await store.set("k", kind="search", payload={"v": 1}, ttl_seconds=60)
    await store.set("k", kind="search", payload={"v": 2}, ttl_seconds=60)
    assert store.get("k") == {"v": 2}

def test_get_returns_none_for_missing_key(tmp_path: Path) -> None:
    """An absent key returns None."""
    store = ScrapeStore.load(tmp_path / "store.json")
    assert store.get("nope") is None

# ---------------------------------------------------------------------- #
# Expiry
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_expired_entry_returns_none(tmp_path: Path) -> None:
    """An entry past its expiry is not returned by get."""
    store = ScrapeStore.load(tmp_path / "store.json")
    # TTL of 0 would trip validation on Settings but is fine here because
    # we are calling the store directly. Use a tiny value instead.
    await store.set("k", kind="search", payload={"v": 1}, ttl_seconds=1)
    # Manually age the entry so the test is deterministic.
    entry = store._entries["k"]
    entry["expires_at"] = (pkt_now() - timedelta(seconds=1)).isoformat()
    assert store.get("k") is None

@pytest.mark.asyncio()
async def test_prune_removes_expired(tmp_path: Path) -> None:
    """prune drops expired entries and reports the count."""
    store = ScrapeStore.load(tmp_path / "store.json")
    await store.set("keep", kind="search", payload={"v": 1}, ttl_seconds=3600)
    await store.set("drop", kind="search", payload={"v": 2}, ttl_seconds=3600)
    store._entries["drop"]["expires_at"] = (
        pkt_now() - timedelta(seconds=1)
    ).isoformat()
    removed = await store.prune()
    assert removed == 1
    assert store.get("keep") is not None
    assert store.get("drop") is None

@pytest.mark.asyncio()
async def test_prune_no_op_when_nothing_expired(tmp_path: Path) -> None:
    """prune returns 0 when everything is fresh."""
    store = ScrapeStore.load(tmp_path / "store.json")
    await store.set("k", kind="search", payload={"v": 1}, ttl_seconds=3600)
    assert await store.prune() == 0

# ---------------------------------------------------------------------- #
# Atomicity and malformed payloads
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_get_rejects_non_dict_payload(tmp_path: Path) -> None:
    """A payload that is not a dict is treated as missing."""
    store = ScrapeStore.load(tmp_path / "store.json")
    await store.set("k", kind="search", payload={"ok": True}, ttl_seconds=60)
    store._entries["k"]["payload"] = "not-a-dict"
    assert store.get("k") is None

@pytest.mark.asyncio()
async def test_concurrent_sets_do_not_corrupt(tmp_path: Path) -> None:
    """Concurrent writes serialise through the lock without losing entries."""
    store = ScrapeStore.load(tmp_path / "store.json")

    async def write(i: int) -> None:
        await store.set(f"k{i}", kind="search", payload={"v": i}, ttl_seconds=60)

    await asyncio.gather(*(write(i) for i in range(20)))
    assert store.size == 20
    raw = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))
    assert len(raw) == 20

def test_path_property(tmp_path: Path) -> None:
    """path returns the resolved backing file path."""
    target = tmp_path / "store.json"
    store = ScrapeStore.load(target)
    assert store.path == target
