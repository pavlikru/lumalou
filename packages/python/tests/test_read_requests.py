"""Literal source-backed query vectors; no guessed response layouts."""

import json
from pathlib import Path

import pytest
from lumalou import client, commands
from lumalou._generated import COMMANDS, RESPONSES
from lumalou.client import (
    MalformedResponseError,
    ResponseEnvelope,
    UnsupportedResponseError,
)
from lumalou.profile import RoutineMusicSettings
from lumalou.responses import SINGLE_VALUE_RESPONSES

VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "spec" / "read-requests.json").read_text()
)["requests"]


def test_all_declared_queries_are_covered_without_write_opcodes():
    names = {v["name"] for v in VECTORS}
    assert len(names) == len(VECTORS) == 28
    assert names == set(commands._REQUESTS) == set(client._REQUEST_RESPONSES)
    assert {v["command"] for v in VECTORS} == {
        name for name in COMMANDS if name.startswith("REQUEST_")
    }
    assert len({v["response"] for v in VECTORS}) == len(VECTORS)


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_exact_query_and_response_ids(vector):
    name = vector["name"]
    assert commands.request(name) == bytes.fromhex(vector["request"])
    assert COMMANDS[vector["command"]] == int(vector["request"], 16)
    assert client._REQUEST_RESPONSES[name] == int(vector["response"], 16)
    assert int(vector["response"], 16) in RESPONSES


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_only_proven_response_layouts_have_decoders(vector):
    opcode = int(vector["response"], 16)
    if opcode in {0x02, 0x13, 0x19, 0x22, 0x23, 0x27, 0x93, 0x94, 0x99}:
        return
    if opcode in SINGLE_VALUE_RESPONSES:
        return
    # Deliberately arbitrary bytes: preserving raw is not validating semantics.
    raw = b"\x00\xff\x01\x89"
    envelope = ResponseEnvelope(opcode, raw, 1, 1.0, 1)
    with pytest.raises(UnsupportedResponseError):
        envelope.decode()
    assert envelope.args == raw


@pytest.mark.parametrize(
    "name",
    [
        "SET_TIME_PRESCALER",
        "set_time_prescaler",
        "SEND_PAIRING_COMPLETE",
        "unknown",
        "",
    ],
)
def test_query_builder_rejects_writes_and_unknown_names(name):
    with pytest.raises(KeyError):
        commands.request(name)


def test_single_value_responses_match_their_request_names():
    for opcode, name in SINGLE_VALUE_RESPONSES.items():
        assert client._REQUEST_RESPONSES[name] == opcode
    assert set(SINGLE_VALUE_RESPONSES.values()) == {
        "led_brightness",
        "light_color",
        "light_duration",
        "volume",
        "routine_volume",
        "song_playing",
        "playlist_duration",
        "operation_mode",
        "activity_state",
        "current_stage",
        "r2r_status",
        "routine_mode_status",
        "r2r_alarm_status",
        "nap_current_status",
        "transmission_mode",
    }


@pytest.mark.parametrize("opcode", sorted(SINGLE_VALUE_RESPONSES))
def test_single_value_responses_decode_exactly_one_byte(opcode):
    # Hardware (firmware 0.3.7) values, e.g. song 13 = pink noise.
    for value in (0, 5, 13, 255):
        assert ResponseEnvelope(opcode, bytes((value,)), 1, 1.0, 1).decode() == value
    for raw in (b"", b"\x00\x00", b"\x00\xff\x01\x89"):
        with pytest.raises(MalformedResponseError):
            ResponseEnvelope(opcode, raw, 1, 1.0, 1).decode()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"\x01\x11", RoutineMusicSettings(1, 1, 1)),  # factory default
        (b"\x02\x34", RoutineMusicSettings(2, 3, 4)),  # read back after 69 02 34
        (b"\x00\x00", RoutineMusicSettings(0, 0, 0)),
    ],
)
def test_routine_music_status_uses_the_set_layout(raw, expected):
    assert ResponseEnvelope(0x93, raw, 1, 1.0, 1).decode() == expected


@pytest.mark.parametrize("raw", [b"", b"\x01", b"\x01\x11\x00"])
def test_routine_music_status_rejects_other_lengths(raw):
    with pytest.raises(MalformedResponseError):
        ResponseEnvelope(0x93, raw, 1, 1.0, 1).decode()


def test_unanswered_requests_are_named_requests():
    assert commands.UNANSWERED_REQUESTS == {"nap_alarm_status", "nap_alarm"}
    assert commands.UNANSWERED_REQUESTS <= set(client._REQUEST_RESPONSES)
