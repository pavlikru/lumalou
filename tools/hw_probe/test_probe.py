"""Offline tests for the hardware probe: no Bluetooth is ever used."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import ClassVar

import pytest
from lumalou import commands as C
from lumalou.client import (
    _REQUEST_RESPONSES,
    FreshSessionRequiredError,
    RequestTimeoutError,
    ResponseEnvelope,
)
from lumalou.schedules import DAY_ROUTINE_RESPONSES

import probe

ADDRESS = "12345678-ABCD-4000-8000-00000000BEEF"
FINGERPRINT = "ab" * 32


@pytest.fixture(autouse=True)
def no_bluetooth(monkeypatch):
    async def forbidden(*_a, **_k):
        raise AssertionError("Bluetooth must not be used")

    monkeypatch.setattr(probe, "find_device", forbidden)
    monkeypatch.setattr(probe, "scan_candidates", forbidden)
    monkeypatch.setattr(probe, "RECONNECT_GAP", 0)
    monkeypatch.setattr(probe, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(probe, "RESTORE_SPACING", 0)


def run_cli(tmp_path, *argv):
    return probe.main([*argv, "--out", str(tmp_path / "out")])


def log_text(tmp_path) -> str:
    return "".join(p.read_text() for p in (tmp_path / "out").glob("*.jsonl"))


# ---- allowlist ----------------------------------------------------------


def test_read_names_match_library():
    assert set(probe.READ_NAMES) == set(_REQUEST_RESPONSES)


def test_allowlist_covers_every_write_builder_and_nothing_unsafe():
    builders = {
        name
        for name, fn in vars(C).items()
        if inspect.isfunction(fn)
        and fn.__module__ == C.__name__
        and not name.startswith("_")
    }
    allowlisted = {f.builder for f in probe.FEATURES.values()}
    assert allowlisted <= builders
    assert builders - allowlisted == set(probe.EXCLUDED_BUILDERS)
    banned = ("prescaler", "pairing", "dfu", "ota", "firmware", "raw", "reset")
    for feature in probe.FEATURES.values():
        assert not any(b in feature.name or b in feature.builder for b in banned)


def test_dangerous_features():
    assert {n for n, f in probe.FEATURES.items() if f.dangerous} == {
        "soother",
        "global-state",
        "nap-start",
        "routine-start",
        "routine-control",
    }


@pytest.mark.parametrize(
    ("feature", "args", "payload"),
    [
        ("light-color", ["night_light"], "3c 07"),
        ("light-brightness", ["2"], "3a 02"),
        ("light-off", [], "3e"),
        ("volume", ["3"], "37 03"),
        ("play", ["ocean"], "3f 03"),
        ("clock-settings", ["on", "2", "24"], "79 01 21"),
        ("r2r-status", ["off"], "44 00"),
        ("nap-alarm", ["inactive"], "4f 09"),
        ("global-state", ["lights_on=1"], "01 1f ff ff ff"),
    ],
)
def test_plans(feature, args, payload):
    plan = probe.build_plan(feature, args, probe.Options())
    assert plan.payload.hex(" ") == payload


def test_caps_and_guards():
    opts = probe.Options()
    for feature, args in (
        ("light-brightness", ["4"]),
        ("volume", ["4"]),
        ("routine-volume", ["9"]),
        ("clock-settings", ["on", "9", "24"]),
        ("global-state", ["volume=5"]),
        ("global-state", ["volume=15"]),  # 0x0F "no modify" sentinel
        ("nap-alarm", ["active"]),
        ("play", ["8"]),
    ):
        with pytest.raises(probe.ProbeError):
            probe.build_plan(feature, args, opts)
    assert probe.build_plan("volume", ["9"], probe.Options(allow_high=True))
    assert probe.build_plan("nap-alarm", ["0"], probe.Options(allow_alarm=True))

    plan = probe.build_plan("play", ["rain"], opts)
    probe.check_volume_guard(plan, {"currentVolume": 3}, opts)
    with pytest.raises(probe.ProbeError):
        probe.check_volume_guard(plan, {"currentVolume": 7}, opts)
    with pytest.raises(probe.ProbeError):
        probe.check_volume_guard(plan, None, opts)


def test_json_blocks(tmp_path):
    alarms = tmp_path / "alarms.json"
    alarms.write_text(json.dumps({"days": [9, 9, 9, 9, 9, 9, 0], "sound": 1}))
    with pytest.raises(probe.ProbeError, match="allow-alarm"):
        probe.build_plan("r2r-alarms", [str(alarms)], probe.Options())
    routine = tmp_path / "routine.json"
    routine.write_text(json.dumps({"time": "19:30", "steps": [[3], [7, 1]]}))
    plan = probe.build_plan("day-routine", ["friday", str(routine)], probe.Options())
    assert plan.payload.hex(" ") == "64 19 30 13 27 21 00 00 00 00 00 00 00 00 00"
    assert plan.readback == ("day:friday",)


# ---- CLI safety ---------------------------------------------------------


def test_dry_run_prints_payload_without_bluetooth(tmp_path, capsys):
    assert run_cli(tmp_path, "send", "light-brightness", "2", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "3a 02" in out and "dry run" in out


def test_write_requires_yes(tmp_path, capsys):
    assert run_cli(tmp_path, "send", "light-brightness", "2") == 2
    assert "--yes" in capsys.readouterr().out


def test_dangerous_requires_flag_even_with_yes(tmp_path, capsys):
    assert run_cli(tmp_path, "send", "nap-start", "min_15", "--yes") == 2
    assert "--dangerous" in capsys.readouterr().out


def test_cap_refused_on_cli(tmp_path):
    assert run_cli(tmp_path, "send", "volume", "7", "--dry-run") == 2


# ---- sessions with a fake client ----------------------------------------


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []
    script: ClassVar[dict[str, list]] = {}

    def __init__(
        self, device, *, on_response, disconnected_callback, expected_device_fingerprint
    ):
        self.device = device
        self.on_response = on_response
        self.expected = expected_device_fingerprint
        self.connected = False
        self.sent: list[bytes] = []
        self.last_error = None
        FakeClient.instances.append(self)

    @property
    def device_fingerprint(self):
        return FINGERPRINT if self.connected else None

    async def connect(self, timeout=None):
        self.connected = True
        return self

    async def disconnect(self):
        self.connected = False

    async def send(self, payload):
        self.sent.append(payload)

    async def _answer(self, key, opcode):
        outcome = FakeClient.script.get(key, [bytes([1])])
        item = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        if isinstance(item, BaseException):
            if isinstance(item, RequestTimeoutError):
                self.connected = False
            raise item
        envelope = ResponseEnvelope(opcode, item, 0, 0.0, 1)
        self.on_response(envelope)
        return envelope

    async def request_named(self, name, timeout=3.0):
        return await self._answer(name, _REQUEST_RESPONSES[name])

    async def request_day_routine(self, day, timeout=3.0):
        return await self._answer(f"day:{day}", DAY_ROUTINE_RESPONSES[day])


@pytest.fixture
def fake(monkeypatch):
    FakeClient.instances = []
    FakeClient.script = {
        "global_state": [bytes(13)],
        "r2r_times": [bytes.fromhex("0700ffff" * 3 + "0700")],
    }
    monkeypatch.setattr(probe, "CLIENT_FACTORY", FakeClient)

    async def device(log, name, timeout):
        log.add_secret(ADDRESS)
        return probe.Candidate(object(), ADDRESS, "1234", -50, "digits-name", {})

    monkeypatch.setattr(probe, "find_device", device)
    return FakeClient


def test_read_all_records_timeout_and_reconnects(tmp_path, fake, capsys):
    fake.script["volume"] = [RequestTimeoutError("timed out")]
    fake.script["light_color"] = [FreshSessionRequiredError("seen"), bytes([7])]
    assert run_cli(tmp_path, "read-all") == 1  # one timeout
    out = capsys.readouterr().out
    assert "volume" in out and "timeout" in out
    records = [
        json.loads(line)
        for line in log_text(tmp_path).splitlines()
        if '"kind": "read"' in line
    ]
    by_name = {r["name"]: r for r in records if r["status"] != "fresh-session-required"}
    assert by_name["light_color"]["status"] == "ok"
    assert by_name["r2r_times"]["decoded"]["days"][0] == "07:00"
    assert by_name["led_brightness"]["undecodable"].startswith(
        "UnsupportedResponseError"
    )
    assert len(by_name) == len(probe.READ_NAMES) + 7
    # Reconnected after the timeout and the fresh-session refusal, pinned to identity.
    assert len(fake.instances) == 3
    assert all(c.expected == FINGERPRINT for c in fake.instances[1:])
    text = out + log_text(tmp_path)
    assert (
        FINGERPRINT not in text and ADDRESS not in text and ADDRESS.lower() not in text
    )


def test_send_writes_once_and_diffs(tmp_path, fake, capsys):
    fake.script["led_brightness"] = [bytes([5]), bytes([2])]
    code = run_cli(
        tmp_path, "send", "light-brightness", "2", "--yes", "--repeat-state-read"
    )
    assert code == 0
    sent = [p for c in fake.instances for p in c.sent]
    assert sent == [bytes.fromhex("3a02")]
    out = capsys.readouterr().out
    assert "led_brightness" in out and "changed" in out
    assert "repeat-state-read" in out


def test_baseline_roundtrip(tmp_path, fake, capsys):
    base = tmp_path / "base.json"
    run_cli(tmp_path, "baseline", "save", str(base))
    saved = json.loads(base.read_text())
    assert saved["format"] == probe.BASELINE_FORMAT
    ops, skipped = probe.plan_restore(
        saved, include_inferred=False, opts=probe.Options()
    )
    assert "r2r_times" in {op.block for op in ops}
    assert "music_playlist" not in {op.block for op in ops}  # 1-byte fake: undecodable
    assert any(name == "r2r_status" for name, _ in skipped)
    inferred, _ = probe.plan_restore(saved, include_inferred=True, opts=probe.Options())
    assert {"r2r_status", "routine_mode_status"} <= {op.block for op in inferred}

    assert run_cli(tmp_path, "baseline", "restore", str(base), "--dry-run") == 0
    assert not any(c.sent for c in fake.instances)
    assert run_cli(tmp_path, "baseline", "restore", str(base), "--yes") == 0
    sent = [p for c in fake.instances for p in c.sent]
    assert (
        bytes([C.COMMANDS["SET_R2R_TIMES"]]) + bytes.fromhex("0700ffff" * 3 + "0700")
        in sent
    )


def test_ctrl_c_disconnects(tmp_path, fake, monkeypatch):
    async def interrupted(args, log, probe_):
        await probe_.connect()
        raise asyncio.CancelledError

    monkeypatch.setattr(probe, "cmd_watch", interrupted)
    assert run_cli(tmp_path, "watch", "--seconds", "1") == 130
    assert fake.instances and not fake.instances[-1].connected
