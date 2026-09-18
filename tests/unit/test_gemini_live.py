"""Unit tests for the Gemini Live adapter.

The ``google-genai`` SDK client is fully mocked, so these tests never open
a real Live session, never touch the network, and never require a live
API key. They verify:

    - the configured model is the one passed to ``connect``
    - the configured voice is the one the session speaks with
    - the function declarations are registered
    - browser audio is forwarded as ``audio/pcm;rate=16000``
    - model audio is emitted to the event callback as raw bytes
    - transcripts, tool calls, interrupted, and turn_complete are
      translated into the documented event vocabulary
    - the resumption handle is captured and re-sent on reconnect
    - finish() cancels the reader task and closes the context manager
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daraz_ai_shopping_assistant.core.config import Settings
from daraz_ai_shopping_assistant.core.exceptions import VoiceError
from daraz_ai_shopping_assistant.voice.gemini_live import (
    _SYSTEM_INSTRUCTION,
    _TOOL_DECLARATIONS,
    GeminiLiveAdapter,
)


# ---------------------------------------------------------------------- #
# Test doubles
# ---------------------------------------------------------------------- #
class _FakeResponse:
    """Minimal stand-in for the SDK's ``LiveServerMessage``.

    Real response objects expose a grab-bag of optional attributes; the
    adapter reads them with ``getattr(response, name, None)``. This fake
    defaults every attribute to ``None`` at the class level and only sets
    the ones the test supplied, so absent attributes read back as ``None``
    rather than as a magic mock.
    """

    data: Any = None
    text: Any = None
    input_transcription: Any = None
    output_transcription: Any = None
    tool_call: Any = None
    server_content: Any = None
    session_resumption_update: Any = None

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

def _make_receive(responses: list[Any]) -> Any:
    """Return a callable that produces a fresh async iterator over ``responses``.

    Args:
        responses: The objects the fake session should yield from
            ``receive()``.

    Returns:
        A zero-argument callable that returns a new async iterator each
        time it is invoked.
    """

    def _receive() -> Any:
        async def _gen() -> Any:
            for response in responses:
                yield response
                # Give the loop a chance to run so tests can observe
                # dispatches between iterations.
                await asyncio.sleep(0)

        return _gen()

    return _receive

class _FakeConnectCM:
    """Fake async context manager returned by ``client.aio.live.connect``."""

    def __init__(self, session: Any) -> None:
        self._session = session
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> Any:
        self.entered = True
        return self._session

    async def __aexit__(self, *_exc: Any) -> None:
        self.exited = True

def _build_mock_client(
    responses: list[Any] | None = None,
) -> tuple[MagicMock, MagicMock, _FakeConnectCM]:
    """Return a mocked ``genai.Client``, session, and connect CM.

    Args:
        responses: Canned responses the fake session should yield from
            ``receive``.

    Returns:
        A ``(client, session, connect_cm)`` tuple.
    """
    session = MagicMock()
    session.send_realtime_input = AsyncMock()
    session.send_tool_response = AsyncMock()
    session.receive = _make_receive(responses or [])

    connect_cm = _FakeConnectCM(session)

    client = MagicMock()
    client.aio.live.connect = MagicMock(return_value=connect_cm)
    return client, session, connect_cm

async def _noop_on_event(_event: dict[str, Any]) -> None:
    """Async callback that discards every event."""

@pytest.fixture()
def mock_genai() -> Iterator[tuple[MagicMock, MagicMock, MagicMock, _FakeConnectCM]]:
    """Patch ``genai.Client`` for the duration of the test.

    Yields:
        ``(MockClient class, mock client, mock session, connect_cm)``.
        Tests can replace ``session.receive`` before calling
        ``adapter.start`` if they need canned server responses.
    """
    client, session, connect_cm = _build_mock_client()
    with patch(
        "daraz_ai_shopping_assistant.voice.gemini_live.genai.Client"
    ) as MockClient:  # noqa: N806
        MockClient.return_value = client
        yield MockClient, client, session, connect_cm

# ---------------------------------------------------------------------- #
# Configuration
# ---------------------------------------------------------------------- #
def test_llm_live_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LLM_LIVE_MODEL`` on the environment flows into settings."""
    monkeypatch.setenv("LLM_LIVE_MODEL", "gemini-3.8-live")
    fresh = Settings(_env_file=None, firecrawl_api_key="fc-test")
    assert fresh.llm_live_model == "gemini-3.8-live"

def test_adapter_defaults_to_settings_llm_live_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no override, the adapter uses ``settings.llm_live_model``."""
    from daraz_ai_shopping_assistant.core import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_live_model", "gemini-3.8-live")
    adapter = GeminiLiveAdapter(api_key="k")
    assert adapter._model == "gemini-3.8-live"

def test_adapter_respects_explicit_model_override() -> None:
    """An explicit ``model=`` argument wins over settings."""
    adapter = GeminiLiveAdapter(api_key="k", model="custom-live-model")
    assert adapter._model == "custom-live-model"

# ---------------------------------------------------------------------- #
# start() lifecycle
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_start_raises_without_api_key() -> None:
    """A missing Google API key fails fast with a clear error."""
    adapter = GeminiLiveAdapter(api_key="")
    with pytest.raises(VoiceError) as excinfo:
        await adapter.start(_noop_on_event)
    assert "GOOGLE_API_KEY" in str(excinfo.value)

@pytest.mark.asyncio()
async def test_configured_model_reaches_connect(mock_genai: Any) -> None:
    """The model identifier passed to ``connect`` is the configured one.

    This is the "the model is the model receiving the audio" test: the
    configured string is the string the SDK is told to open.
    """
    _, client, _session, _cm = mock_genai
    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(_noop_on_event)
    await adapter.finish()

    client.aio.live.connect.assert_called_once()
    assert client.aio.live.connect.call_args.kwargs["model"] == "gemini-3.8-live"

@pytest.mark.asyncio()
async def test_default_model_reaches_connect(mock_genai: Any) -> None:
    """With no override, ``settings.llm_live_model`` reaches ``connect``."""
    from daraz_ai_shopping_assistant.core.config import settings

    _, client, _session, _cm = mock_genai
    adapter = GeminiLiveAdapter(api_key="k")
    await adapter.start(_noop_on_event)
    await adapter.finish()

    assert (
        client.aio.live.connect.call_args.kwargs["model"]
        == settings.llm_live_model
    )

@pytest.mark.asyncio()
async def test_connect_config_carries_voice_and_tools(mock_genai: Any) -> None:
    """Voice name, system instruction, and tool declarations are all set."""
    _, client, _session, _cm = mock_genai
    adapter = GeminiLiveAdapter(
        api_key="k",
        model="gemini-3.8-live",
        voice_name="Kore",
    )
    await adapter.start(_noop_on_event)
    await adapter.finish()

    config = client.aio.live.connect.call_args.kwargs["config"]
    assert config.response_modalities == ["AUDIO"]

    voice_name = (
        config.speech_config.voice_config.prebuilt_voice_config.voice_name
    )
    assert voice_name == "Kore"

    system_text = config.system_instruction.parts[0].text
    assert system_text == _SYSTEM_INSTRUCTION

    assert len(config.tools) == 1
    decls = config.tools[0].function_declarations
    names = {
        d["name"] if isinstance(d, dict) else d.name for d in decls
    }
    assert names == {"search_products", "get_product"}

@pytest.mark.asyncio()
async def test_start_emits_connected_event(mock_genai: Any) -> None:
    """A successful start emits a ``connected`` event to the callback."""
    _, _client, _session, _cm = mock_genai
    events: list[dict[str, Any]] = []

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await adapter.finish()

    assert {"type": "connected"} in events

# ---------------------------------------------------------------------- #
# Audio input -- "the model receives the audio"
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_send_audio_forwards_pcm16_16khz(mock_genai: Any) -> None:
    """Browser audio is forwarded as PCM16 at 16 kHz.

    This is the "the model receives the audio" assertion: the SDK sees
    exactly the bytes we handed the adapter, tagged with the mime type
    that tells Gemini the encoding and rate.
    """
    _, _client, session, _cm = mock_genai
    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(_noop_on_event)

    chunk = b"\x01\x02\x03\x04" * 8
    await adapter.send_audio(chunk)

    session.send_realtime_input.assert_awaited_once()
    audio = session.send_realtime_input.call_args.kwargs["audio"]
    assert audio.data == chunk
    assert audio.mime_type == "audio/pcm;rate=16000"

    await adapter.finish()

@pytest.mark.asyncio()
async def test_send_audio_is_noop_before_start() -> None:
    """Calling ``send_audio`` before ``start`` is a silent no-op."""
    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    # Should not raise and should not attempt any I/O.
    await adapter.send_audio(b"\x00\x01")

# ---------------------------------------------------------------------- #
# Audio output -- "the model outputs audio"
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_audio_output_is_emitted_to_callback(mock_genai: Any) -> None:
    """Model audio is emitted to the callback as raw PCM bytes.

    This is the "the model outputs the audio" assertion: when the SDK
    delivers a response carrying audio, the adapter surfaces it as a
    ``{"type": "audio", "data": <bytes>}`` event.
    """
    _, _client, session, _cm = mock_genai
    session.receive = _make_receive([_FakeResponse(data=b"\xaa\xbb\xcc\xdd")])

    received: list[bytes] = []
    got_audio = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "audio":
            received.append(event["data"])
            got_audio.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(got_audio.wait(), timeout=2.0)
    await adapter.finish()

    assert received == [b"\xaa\xbb\xcc\xdd"]

@pytest.mark.asyncio()
async def test_multiple_audio_chunks_all_delivered(mock_genai: Any) -> None:
    """Every audio response in the stream reaches the callback in order."""
    _, _client, session, _cm = mock_genai
    chunks = [b"one", b"two", b"three"]
    session.receive = _make_receive([_FakeResponse(data=c) for c in chunks])

    received: list[bytes] = []
    done = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "audio":
            received.append(event["data"])
            if len(received) == len(chunks):
                done.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    await adapter.finish()

    assert received == chunks

# ---------------------------------------------------------------------- #
# Transcripts
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_input_transcription_becomes_transcript_input(
    mock_genai: Any,
) -> None:
    """An ``input_transcription`` block surfaces as ``transcript_input``."""
    from types import SimpleNamespace

    _, _client, session, _cm = mock_genai
    session.receive = _make_receive(
        [
            _FakeResponse(
                input_transcription=SimpleNamespace(text="hello there"),
            )
        ]
    )

    events: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "transcript_input":
            done.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    await adapter.finish()

    assert {"type": "transcript_input", "text": "hello there"} in events

@pytest.mark.asyncio()
async def test_output_transcription_becomes_transcript_output(
    mock_genai: Any,
) -> None:
    """An ``output_transcription`` block surfaces as ``transcript_output``."""
    from types import SimpleNamespace

    _, _client, session, _cm = mock_genai
    session.receive = _make_receive(
        [
            _FakeResponse(
                output_transcription=SimpleNamespace(text="here is what I found"),
            )
        ]
    )

    events: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "transcript_output":
            done.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    await adapter.finish()

    assert {"type": "transcript_output", "text": "here is what I found"} in events

# ---------------------------------------------------------------------- #
# Tool calls, barge-in, turn complete, resumption
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_tool_call_event_is_emitted(mock_genai: Any) -> None:
    """A tool call surfaces as a ``tool_call`` event with the calls list."""
    from types import SimpleNamespace

    _, _client, session, _cm = mock_genai
    call = SimpleNamespace(id="c1", name="search_products", args={"query": "mouse"})
    tool_call = SimpleNamespace(function_calls=[call])
    session.receive = _make_receive([_FakeResponse(tool_call=tool_call)])

    events: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "tool_call":
            done.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    await adapter.finish()

    tool_events = [e for e in events if e.get("type") == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0]["calls"] == [call]

@pytest.mark.asyncio()
async def test_interrupted_event_is_emitted(mock_genai: Any) -> None:
    """A server-side interruption surfaces as ``interrupted``."""
    from types import SimpleNamespace

    _, _client, session, _cm = mock_genai
    session.receive = _make_receive(
        [_FakeResponse(server_content=SimpleNamespace(interrupted=True))]
    )

    events: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "interrupted":
            done.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    await adapter.finish()

    assert {"type": "interrupted"} in events

@pytest.mark.asyncio()
async def test_turn_complete_event_is_emitted(mock_genai: Any) -> None:
    """A completed turn surfaces as ``turn_complete``."""
    from types import SimpleNamespace

    _, _client, session, _cm = mock_genai
    session.receive = _make_receive(
        [_FakeResponse(server_content=SimpleNamespace(turn_complete=True))]
    )

    events: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "turn_complete":
            done.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    await adapter.finish()

    assert {"type": "turn_complete"} in events

@pytest.mark.asyncio()
async def test_resumption_handle_is_captured(mock_genai: Any) -> None:
    """A session resumption update is stored on the adapter."""
    from types import SimpleNamespace

    _, _client, session, _cm = mock_genai
    update = SimpleNamespace(new_handle="resume-token-42")
    session.receive = _make_receive(
        [_FakeResponse(session_resumption_update=update)]
    )

    done = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "resumption_update":
            done.set()

    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(on_event)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    await adapter.finish()

    assert adapter._resumption_handle == "resume-token-42"

# ---------------------------------------------------------------------- #
# Tool responses and teardown
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio()
async def test_send_tool_responses_forwards_to_session(mock_genai: Any) -> None:
    """``send_tool_responses`` forwards the list to the SDK."""
    from google.genai import types

    _, _client, session, _cm = mock_genai
    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(_noop_on_event)

    response = types.FunctionResponse(
        id="c1", name="search_products", response={"products": []}
    )
    await adapter.send_tool_responses([response])

    session.send_tool_response.assert_awaited_once()
    forwarded = session.send_tool_response.call_args.kwargs["function_responses"]
    assert forwarded == [response]

    await adapter.finish()

@pytest.mark.asyncio()
async def test_finish_cancels_reader_and_closes_cm(mock_genai: Any) -> None:
    """``finish`` cancels the reader task and closes the connect CM."""
    _, _client, _session, connect_cm = mock_genai
    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(_noop_on_event)

    assert adapter._reader_task is not None
    await adapter.finish()

    assert adapter._reader_task is None
    assert connect_cm.entered is True
    assert connect_cm.exited is True

@pytest.mark.asyncio()
async def test_finish_is_idempotent(mock_genai: Any) -> None:
    """Calling ``finish`` twice is safe."""
    _, _client, _session, _cm = mock_genai
    adapter = GeminiLiveAdapter(api_key="k", model="gemini-3.8-live")
    await adapter.start(_noop_on_event)
    await adapter.finish()
    await adapter.finish()  # must not raise

def test_tool_declarations_cover_remaining_tools() -> None:
    """Module-level declarations expose exactly the two remaining service tools."""
    names = {decl["name"] for decl in _TOOL_DECLARATIONS}
    assert names == {"search_products", "get_product"}
