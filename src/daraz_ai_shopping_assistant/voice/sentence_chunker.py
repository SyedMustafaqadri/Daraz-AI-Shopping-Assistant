"""Token -> sentence splitter for streaming TTS.

The LLM streams tokens one at a time. ElevenLabs produces better audio
when it receives complete sentences rather than fragments, so we buffer
tokens until a sentence boundary appears and emit whole sentences.

A "sentence" is text ending in ``.``, ``!``, or ``?``. Two rules decide
whether a terminator counts as a boundary:

    - **Mid-buffer**: the terminator is followed by whitespace, and at
      least ``min_chars`` characters precede it in the buffer. The
      prefix guard keeps abbreviations like ``Rs.`` or ``Mr.`` from
      splitting a longer sentence mid-stream.

    - **End-of-buffer**: the terminator sits at the very last index of
      the buffer and the buffer is at least
      ``max(min_chars // 2, 5)`` characters long. This lets a short
      complete sentence such as ``"The price is Rs. 999."`` flush
      immediately, while holding back a lone fragment like ``"Rs."``
      that is still waiting for its surrounding context.

The end-of-buffer threshold scales with ``min_chars`` because a caller
that tunes ``min_chars`` down to, say, 5 for lower-latency TTS is also
expecting shorter sentences to flush. A fixed threshold would defeat
that intent.

Sentences longer than ``max_chars`` are force-flushed to keep latency
bounded. The remainder is emitted by :meth:`flush` when the stream ends.

The chunker is stateful but has no side effects and no I/O -- it is a
pure class that can be unit-tested directly.
"""

from __future__ import annotations

from typing import Final

#: Default minimum number of characters before a mid-buffer terminator
#: counts as a sentence boundary. Prevents splits like ``"Rs."``.
_DEFAULT_MIN_CHARS: Final[int] = 40

#: Default force-flush length. Buffers longer than this flush even
#: without a boundary, keeping first-audio latency bounded.
_DEFAULT_MAX_CHARS: Final[int] = 250

#: Absolute floor for the end-of-buffer threshold. Prevents a buffer
#: like ``"Hi."`` from flushing under a small ``min_chars``.
_END_OF_BUFFER_FLOOR: Final[int] = 5

#: Characters that terminate a sentence.
_TERMINATORS: Final[frozenset[str]] = frozenset({".", "!", "?"})

class SentenceChunker:
    """Accumulate streaming tokens and emit whole sentences.

    Usage::

        chunker = SentenceChunker()
        for token in stream:
            for sentence in chunker.feed(token):
                await tts.speak(sentence)
        remainder = chunker.flush()
        if remainder:
            await tts.speak(remainder)

    Attributes:
        _buffer: The accumulated text not yet emitted.
        _min_chars: Minimum length for a mid-buffer terminator to count.
        _max_chars: Hard cap; buffers beyond this are force-flushed.
        _end_threshold: Minimum buffer length for an end-of-buffer
            terminator to count. Derived from ``_min_chars``.
    """

    def __init__(
        self,
        *,
        min_chars: int = _DEFAULT_MIN_CHARS,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> None:
        """Initialise the chunker.

        Args:
            min_chars: Minimum characters before a mid-buffer terminator
                counts as a sentence boundary.
            max_chars: Force-flush threshold. Must be >= ``min_chars``.
        """
        if max_chars < min_chars:
            raise ValueError("max_chars must be >= min_chars")
        self._buffer: str = ""
        self._min_chars: int = min_chars
        self._max_chars: int = max_chars
        self._end_threshold: int = max(min_chars // 2, _END_OF_BUFFER_FLOOR)

    def feed(self, token: str) -> list[str]:
        """Append a token and return any complete sentences it produced.

        Args:
            token: The next piece of text. May be a single character, a
                word, or a whole sentence depending on the LLM.

        Returns:
            A (possibly empty) list of complete sentences with trailing
            whitespace stripped.
        """
        if not token:
            return []
        self._buffer += token

        sentences: list[str] = []
        while True:
            boundary = self._find_boundary()
            if boundary is None:
                break
            sentence = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if sentence:
                sentences.append(sentence)

        if len(self._buffer) >= self._max_chars:
            forced = self._buffer.strip()
            if forced:
                sentences.append(forced)
            self._buffer = ""

        return sentences

    def flush(self) -> str:
        """Return whatever is in the buffer and reset it.

        Called when the LLM stream ends, to emit any trailing fragment
        that has no sentence terminator.

        Returns:
            The remaining text with leading/trailing whitespace stripped,
            or an empty string when the buffer was empty.
        """
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _find_boundary(self) -> int | None:
        """Return the index *after* the first valid sentence boundary.

        Scans left-to-right for ``.``, ``!``, or ``?`` and applies the
        two rules described in the module docstring. The first rule that
        matches wins, so an abbreviation followed by whitespace later in
        the buffer cannot mask an earlier end-of-buffer terminator.

        Returns:
            The slice end index, or ``None`` when no boundary exists.
        """
        n = len(self._buffer)
        for i, ch in enumerate(self._buffer):
            if ch not in _TERMINATORS:
                continue

            # End-of-buffer rule: a trailing terminator counts when the
            # buffer is long enough to look like a real sentence rather
            # than a lone abbreviation.
            if i == n - 1:
                if n >= self._end_threshold:
                    return i + 1
                continue

            # Mid-buffer rule: terminator followed by whitespace, with
            # enough preceding content to exclude short abbreviations.
            if i + 1 >= self._min_chars and self._buffer[i + 1].isspace():
                return i + 1

        return None

__all__ = ["SentenceChunker"]
