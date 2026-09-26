"""`lumalou send`: vetting and confirmation happen before any Bluetooth I/O."""

from typing import ClassVar

import pytest

from lumalou import cli


class _NoBluetooth:
    """Fails the test if the CLI tries to scan or connect."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("the CLI must not connect")

    @staticmethod
    async def scan(*args, **kwargs):
        raise AssertionError("the CLI must not scan")


class _RecordingClient:
    sent: ClassVar[list] = []

    def __init__(self, address, *args, **kwargs):
        self.address = address

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        _RecordingClient.sent.append((self.address, data))


@pytest.fixture
def no_bluetooth(monkeypatch):
    monkeypatch.setattr(cli, "LumalouClient", _NoBluetooth)


@pytest.fixture
def recording_client(monkeypatch):
    _RecordingClient.sent = []
    monkeypatch.setattr(cli, "LumalouClient", _RecordingClient)
    return _RecordingClient.sent


def test_send_without_yes_prints_the_opcode_and_does_not_connect(no_bluetooth, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["-a", "AA", "send", "3c05"])
    assert "--yes" in str(exc.value.code)
    assert "opcode 0x3c SET_LIGHT_COLOR, args 05" in capsys.readouterr().out


def test_send_with_yes_sends_the_bytes(recording_client, capsys):
    cli.main(["-a", "AA", "send", "--yes", "3c05"])
    assert recording_client == [("AA", bytes.fromhex("3c05"))]
    assert "SET_LIGHT_COLOR" in capsys.readouterr().out


@pytest.mark.parametrize("text", ["5201", "3401"])
def test_send_never_sends_unsafe_opcodes_even_when_dangerous(no_bluetooth, text):
    with pytest.raises(SystemExit):
        cli.main(["-a", "AA", "send", "--yes", "--dangerous", text])


@pytest.mark.parametrize("text", ["0301", "01" + "00" * 8])
def test_whole_device_writes_need_dangerous(text):
    with pytest.raises(SystemExit) as exc:
        cli._raw_command(text)
    assert "--dangerous" in str(exc.value.code)
    assert cli._raw_command(text, dangerous=True) == bytes.fromhex(text)


def test_opcodes_outside_the_spec_need_dangerous():
    with pytest.raises(SystemExit) as exc:
        cli._raw_command("ff00")
    assert "not in the protocol spec" in str(exc.value.code)
    assert cli._raw_command("ff00", dangerous=True) == bytes.fromhex("ff00")


def test_day_routine_opcodes_are_named():
    assert cli._opcode_name(0x5A) == "SET_SUNDAY_ROUTINE"
    assert cli._opcode_name(0x67) == "REQUEST_SATURDAY_ROUTINE"
    assert cli._raw_command("5b") == bytes.fromhex("5b")


@pytest.mark.parametrize("text", ["", "3c0", "zz"])
def test_send_rejects_empty_or_invalid_hex(text):
    with pytest.raises(SystemExit):
        cli._raw_command(text)
