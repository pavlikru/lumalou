"""Live command builders: exact bytes and range checks before any I/O."""

import pytest

from lumalou import Audio
from lumalou import commands as C
from lumalou.cli import _raw_command


def test_light_commands_match_the_web_client():
    # Colour is the web client's light-on action; off is its own opcode.
    assert C.set_light_color(0) == bytes.fromhex("3c00")
    assert C.set_light_color(9) == bytes.fromhex("3c09")
    assert C.set_led_brightness(1) == bytes.fromhex("3a01")
    assert C.turn_off_backlight() == bytes.fromhex("3e")
    assert C.set_light_duration(4) == bytes.fromhex("6c04")


def test_soother_is_a_strict_boolean():
    assert C.set_global_on(True) == bytes.fromhex("0301")
    assert C.set_global_on(False) == bytes.fromhex("0300")
    for invalid in (1, 0, None, "on"):
        with pytest.raises(ValueError):
            C.set_global_on(invalid)


@pytest.mark.parametrize(
    "builder,low,high",
    [
        (C.play_audio, 0, 7),
        (C.start_nap, 0, 11),
        (C.routine_control, 0, 4),
    ],
)
def test_enum_commands_reject_values_outside_their_enum(builder, low, high):
    assert builder(low)[1] == low
    assert builder(high)[1] == high
    for invalid in (low - 1, high + 1, 256, True, 1.0, "1", None):
        with pytest.raises(ValueError):
            builder(invalid)


def test_play_audio_accepts_the_enum():
    assert C.play_audio(Audio.RAIN) == bytes.fromhex("3f04")


@pytest.mark.parametrize(
    "fields",
    [
        (24, 0, 0, 0),
        (0, 60, 0, 0),
        (0, 0, 60, 0),
        (0, 0, 0, 7),
        (None, 0, 0, 0),
        (-1, 0, 0, 0),
        (True, 0, 0, 0),
    ],
)
def test_current_date_rejects_invalid_fields(fields):
    with pytest.raises(ValueError):
        C.set_current_date(*fields)


def test_current_date_bytes():
    assert C.set_current_date(23, 59, 7, 6) == bytes.fromhex("3023590706")


@pytest.mark.parametrize("text", ["5201", "52", "3401", "34"])
def test_cli_refuses_unsafe_raw_opcodes(text):
    with pytest.raises(SystemExit):
        _raw_command(text)


def test_cli_raw_command_passes_other_opcodes_through():
    assert _raw_command("3c05") == bytes.fromhex("3c05")
    with pytest.raises(SystemExit):
        _raw_command("")
