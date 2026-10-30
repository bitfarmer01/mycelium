"""Feature 29: partial-JSON renderer — synthesis text fields surface as they arrive, and a
truncated final chunk is emitted once without ever raising."""
from __future__ import annotations

from core import llm


def test_fields_emit_as_they_arrive():
    s = llm.PartialJSONStream()
    assert s.feed('{"spoken": "hel') == []  # key and value still open
    assert s.feed('lo world", "sc') == [("spoken", "hello world")]  # spoken closed in this chunk
    ev = s.feed('reen": "docs here", "conf')
    assert ev == [("screen", "docs here")]


def test_truncated_final_chunk_emits_partial_without_raising():
    s = llm.PartialJSONStream()
    assert s.feed('{"spoken": "partial ans') == []
    assert s.finish() == [("spoken", "partial ans")]


def test_truncated_dangling_escape_is_dropped():
    s = llm.PartialJSONStream()
    assert s.feed('{"spoken": "abc\\') == []
    assert s.finish() == [("spoken", "abc")]
    s2 = llm.PartialJSONStream()
    s2.feed('{"screen": "ab\\u00')
    assert s2.finish() == [("screen", "ab")]


def test_escapes_and_unicode_unescape():
    s = llm.PartialJSONStream()
    ev = s.feed('{"spoken": "line one\\nline \\"two\\" \\u00e9"}')
    assert ev == [("spoken", 'line one\nline "two" é')]


def test_nested_fields_are_skipped():
    s = llm.PartialJSONStream()
    text = '{"spoken":"x","claims":[{"text":"spoken decoy","chunk_ids":[1,2]}],"gaps":["g1"],"screen":"y"}'
    ev = s.feed(text) + s.finish()
    assert ("spoken", "x") in ev and ("screen", "y") in ev
    assert {k for k, _v in ev} == {"spoken", "screen"}  # nested/array fields never leak


def test_fenced_and_prose_prefixed_output():
    ev = llm.stream_json_fields(['Here you go:\n```json\n{"spoken": "', 'streamed", "screen": "s"}\n```'])
    assert list(ev) == [("spoken", "streamed"), ("screen", "s")]


def test_duplicate_key_keeps_first():
    assert list(llm.stream_json_fields(['{"spoken": "first", "spoken": "second"}'])) == [("spoken", "first")]


def test_completed_fields_not_reemitted_by_finish():
    s = llm.PartialJSONStream()
    assert s.feed('{"spoken": "a"') == [("spoken", "a")]  # closing quote already arrived
    assert s.finish() == []
    assert s.finish() == []  # idempotent


def test_non_string_and_nested_values_never_leak():
    text = '{"gaps": ["a"], "n": 3.5, "ok": true, "spoken": "s", "contradictions": [{"a": "x"}]}'
    assert list(llm.stream_json_fields([text])) == [("spoken", "s")]


def test_garbage_prefix_never_raises():
    s = llm.PartialJSONStream()
    assert s.feed("not json at all") == []
    assert s.feed('{"spoken": "ok"}') == [("spoken", "ok")]
    assert s.feed("trailing junk") == []


def test_full_synthesis_payload_streams_in_order():
    payload = ('{"spoken": "Two words.", "screen": "Doc [1].", '
               '"claims": [{"text": "c", "chunk_ids": [1]}], "contradictions": [], "gaps": [], '
               '"confidence": "high"}')
    chunks = [payload[i:i + 7] for i in range(0, len(payload), 7)]
    assert list(llm.stream_json_fields(chunks)) == [
        ("spoken", "Two words."), ("screen", "Doc [1]."), ("confidence", "high")]
