"""Unit tests for :class:`SentenceChunker`.

Pure, fast, no I/O. Exercises the sentence-boundary detection, the
minimum-length guard against abbreviations, and the max-length force
flush.
"""

from __future__ import annotations

import pytest

from daraz_ai_shopping_assistant.voice.sentence_chunker import SentenceChunker


def _feed_all(chunker: SentenceChunker, tokens: list[str]) -> list[str]:
    """Feed a token stream and collect all emitted sentences.

    Args:
        chunker: The chunker to feed.
        tokens: Tokens in order.

    Returns:
        The concatenated list of every sentence the chunker emitted.
    """
    out: list[str] = []
    for token in tokens:
        out.extend(chunker.feed(token))
    return out

def test_empty_feed_returns_empty() -> None:
    """An empty token produces no sentences."""
    assert SentenceChunker().feed("") == []

def test_short_fragment_does_not_flush() -> None:
    """Text below the min-chars threshold stays buffered."""
    chunker = SentenceChunker(min_chars=40)
    assert chunker.feed("Hello.") == []

def test_period_at_end_of_buffer_flushes() -> None:
    """A terminator at end-of-buffer is treated as a boundary."""
    chunker = SentenceChunker(min_chars=5)
    sentences = chunker.feed("Hello, this is a full sentence.")
    assert sentences == ["Hello, this is a full sentence."]

def test_terminator_followed_by_whitespace_flushes() -> None:
    """A terminator followed by whitespace is a boundary."""
    chunker = SentenceChunker(min_chars=5)
    sentences = chunker.feed("First sentence. Second sentence.")
    assert len(sentences) == 2
    assert sentences[0] == "First sentence."
    assert sentences[1] == "Second sentence."

def test_question_mark_terminates() -> None:
    """Question marks are sentence boundaries."""
    chunker = SentenceChunker(min_chars=5)
    sentences = chunker.feed("Want more options? Sure.")
    assert sentences[0] == "Want more options?"

def test_exclamation_terminates() -> None:
    """Exclamation marks are sentence boundaries."""
    chunker = SentenceChunker(min_chars=5)
    sentences = chunker.feed("Great deal! Let me check.")
    assert sentences[0] == "Great deal!"

def test_abbreviation_inside_min_chars_not_split() -> None:
    """A short prefix like 'Rs.' does not trigger an early boundary."""
    chunker = SentenceChunker(min_chars=40)
    sentences = chunker.feed("The price is Rs. 1,234 for this one.")
    assert sentences == ["The price is Rs. 1,234 for this one."]

def test_tokens_accumulate_across_calls() -> None:
    """Boundary detection is stable across multiple feed calls."""
    chunker = SentenceChunker(min_chars=5)
    out: list[str] = []
    out.extend(chunker.feed("Hello"))
    out.extend(chunker.feed(", "))
    out.extend(chunker.feed("world"))
    out.extend(chunker.feed("."))
    assert out == ["Hello, world."]

def test_flush_returns_remainder() -> None:
    """flush() emits whatever is buffered, with whitespace stripped."""
    chunker = SentenceChunker(min_chars=100)
    chunker.feed("incomplete tail")
    assert chunker.flush() == "incomplete tail"
    assert chunker.flush() == ""  # buffer now empty

def test_max_chars_forces_flush() -> None:
    """A buffer longer than max_chars is force-flushed even without a terminator."""
    chunker = SentenceChunker(min_chars=5, max_chars=30)
    sentences = chunker.feed("this is a very long string without any terminator at all")
    assert len(sentences) == 1
    assert sentences[0].startswith("this is a very long")
    assert chunker.flush() == ""

def test_invalid_bounds_raise() -> None:
    """max_chars must be >= min_chars."""
    with pytest.raises(ValueError):
        SentenceChunker(min_chars=100, max_chars=10)

def test_multiple_sentences_in_one_token() -> None:
    """A token containing several sentences emits them all."""
    chunker = SentenceChunker(min_chars=5)
    sentences = _feed_all(
        chunker, ["First item. Second item. Third item."]
    )
    assert sentences == ["First item.", "Second item.", "Third item."]
