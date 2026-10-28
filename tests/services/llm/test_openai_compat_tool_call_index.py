"""Streamed tool-call deltas from servers that send no ``index``."""

from __future__ import annotations

from types import SimpleNamespace

from deeptutor.services.llm.provider_core.openai_compat_provider import (
    OpenAICompatProvider,
    _accumulate_streamed_tool_call,
)


def _delta(call_id, name, arguments, index=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _chunk(tool_call):
    delta = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=None)],
        usage=None,
    )


def test_parse_chunks_keeps_parallel_tool_calls_without_index_apart() -> None:
    response = OpenAICompatProvider._parse_chunks(
        [
            _chunk(_delta("call_1", "get_weather", '{"city":')),
            _chunk(_delta(None, None, ' "Paris"}')),
            _chunk(_delta("call_2", "get_time", '{"zone": "UTC"}')),
        ]
    )

    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("call_1", "get_weather", {"city": "Paris"}),
        ("call_2", "get_time", {"zone": "UTC"}),
    ]


def test_parse_chunks_keeps_indexed_tool_calls_in_their_slots() -> None:
    response = OpenAICompatProvider._parse_chunks(
        [
            _chunk(_delta("call_1", "get_weather", "{", index=0)),
            _chunk(_delta("call_2", "get_time", "{", index=1)),
            _chunk(_delta(None, None, "}", index=0)),
            _chunk(_delta(None, None, "}", index=1)),
        ]
    )

    assert [(c.id, c.name) for c in response.tool_calls] == [
        ("call_1", "get_weather"),
        ("call_2", "get_time"),
    ]


def test_streamed_tool_call_preview_keeps_calls_without_index_apart() -> None:
    buffers: dict[int, dict[str, str]] = {}

    for delta in (
        _delta("call_1", "get_weather", '{"city":'),
        _delta(None, None, ' "Paris"}'),
        _delta("call_2", "get_time", "{}"),
    ):
        _accumulate_streamed_tool_call(buffers, delta)

    assert [(b["id"], b["name"], b["arguments"]) for b in buffers.values()] == [
        ("call_1", "get_weather", '{"city": "Paris"}'),
        ("call_2", "get_time", "{}"),
    ]
