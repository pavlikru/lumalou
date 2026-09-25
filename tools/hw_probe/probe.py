#!/usr/bin/env python3
"""Explicit, opt-in hardware probe for the Fisher-Price Lumalou (gld09).

Talks to a real device through the ``lumalou`` library only (no Home
Assistant). Every write is allowlisted to a named library builder, printed
before it is sent and refused without ``--yes``. There is no raw-opcode path.
Never run this unattended: the owner must be present.

    uv run --project tools/hw_probe python tools/hw_probe/probe.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import hashlib
import json
import logging
import re
import secrets
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import lumalou
from bleak import BleakScanner
from lumalou import commands as C
from lumalou import protocol as P
from lumalou._generated import COMMANDS, DAY_ROUTINE, RESPONSES
from lumalou.advertisement import (
    MANUFACTURER_ID,
    is_lumalou_advertisement,
    parse_advertisement,
)
from lumalou.client import (
    FreshSessionRequiredError,
    LumalouClient,
    LumalouError,
    RequestTimeoutError,
)
from lumalou.profile import ClockSettings, MusicPlaylist, RoutineMusicSettings
from lumalou.responses import label as label_global_state
from lumalou.responses import (
    parse_clock_settings,
    parse_music_playlist,
)
from lumalou.schedules import (
    DAYS,
    ClockTime,
    DailyRoutine,
    RoutineTask,
    WeeklyAlarms,
    WeeklyTimes,
    decode_daily_routine,
    decode_weekly_alarms,
    decode_weekly_times,
)

SCAN_TIMEOUT = 15.0
CONNECT_TIMEOUT = 25.0
REQUEST_TIMEOUT = 3.0
SETTLE_SECONDS = 2.0
RECONNECT_GAP = 1.5  # immediate reconnect after a disconnect was flaky on hardware
RESTORE_SPACING = 0.5
VOLUME_CAP = 3
BRIGHTNESS_CAP = 3

# Every named read the library accepts. READ only: time_prescaler is never set.
READ_NAMES: tuple[str, ...] = (
    "global_state",
    "current_date",
    "toyic_fw_version",
    "led_brightness",
    "light_color",
    "light_duration",
    "volume",
    "routine_volume",
    "song_playing",
    "music_playlist",
    "playlist_duration",
    "operation_mode",
    "activity_state",
    "current_stage",
    "clock_settings",
    "transmission_mode",
    "routine_mode_status",
    "routine_music_status",
    "r2r_status",
    "r2r_times",
    "sleepy_times",
    "r2r_alarm_status",
    "r2r_alarms",
    "routine_task_status",
    "nap_current_status",
    "nap_alarm_status",
    "nap_alarm",
    "time_prescaler",
)
DAY_READS: tuple[str, ...] = tuple(f"day:{day}" for day in DAYS)

_COMMAND_NAMES = {value: name for name, value in COMMANDS.items()}
for _day, _opcode in DAY_ROUTINE["SET"].items():
    _COMMAND_NAMES[_opcode] = f"SET_{_day.upper()}_ROUTINE"

# Injection points for tests.
CLIENT_FACTORY: Callable[..., LumalouClient] = LumalouClient


class ProbeError(Exception):
    """A refused or invalid probe request (exit code 2)."""


# --------------------------------------------------------------------------
# Output: stdout + JSONL, with secrets redacted from both
# --------------------------------------------------------------------------


def jsonable(value):
    """Convert library values to plain JSON (ClockTime -> "HH:MM")."""
    if isinstance(value, ClockTime):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, IntEnum):
        return int(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex(" ")
    if dataclasses.is_dataclass(value):
        return {
            f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return repr(value)


class EventLog:
    """Human lines to stdout, structured records to a JSONL file."""

    def __init__(self, out_dir: Path, command: str, *, stream=None):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = self.out_dir / f"{command}-{stamp}.jsonl"
        self._file = self.path.open("a", encoding="utf-8")
        self._stream = stream or sys.stdout
        self._secrets: list[re.Pattern] = []
        self._t0 = time.monotonic()

    def add_secret(self, text: str | None) -> None:
        if text:
            self._secrets.append(re.compile(re.escape(text), re.IGNORECASE))

    def redact(self, text: str) -> str:
        for pattern in self._secrets:
            text = pattern.sub("<redacted>", text)
        return text

    def say(self, text: str = "") -> None:
        print(self.redact(text), file=self._stream, flush=True)

    def emit(self, kind: str, say: str | None = None, **data) -> None:
        record = {
            "ts": datetime.datetime.now()
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "t": round(time.monotonic() - self._t0, 3),
            "kind": kind,
            **jsonable(data),
        }
        line = json.dumps(record, default=repr, sort_keys=False)
        self._file.write(self.redact(line) + "\n")
        self._file.flush()
        if say is not None:
            self.say(say)

    # Device identity: salted, truncated hash only; salt stays in out_dir.
    def device_hash(self, fingerprint: str) -> str:
        salt_path = self.out_dir / ".salt"
        if not salt_path.exists():
            salt_path.write_text(secrets.token_hex(16), encoding="ascii")
        salt = salt_path.read_text(encoding="ascii").strip()
        return hashlib.sha256(f"{salt}:{fingerprint}".encode()).hexdigest()[:12]

    def check_device(self, device_hash: str) -> str:
        known_path = self.out_dir / "device.json"
        try:
            known = json.loads(known_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        previous = known.get("device_hash")
        if previous is None:
            known_path.write_text(
                json.dumps({"device_hash": device_hash}) + "\n", encoding="utf-8"
            )
            return "first seen (recorded)"
        return (
            "same as previous runs" if previous == device_hash else "DIFFERENT DEVICE"
        )

    def close(self) -> None:
        self._file.close()


class _LibraryLogHandler(logging.Handler):
    """Route library (and bleak warning) log records into the event log."""

    def __init__(self, log: EventLog, echo_debug: bool):
        super().__init__(logging.DEBUG)
        self._log = log
        self.echo_debug = echo_debug

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                message += f" [{type(record.exc_info[1]).__name__}]"
            echo = self.echo_debug or record.levelno >= logging.WARNING
            self._log.emit(
                "lib",
                f"  [lib {record.levelname.lower()}] {message}" if echo else None,
                logger=record.name,
                level=record.levelname,
                message=message,
            )
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)


def install_logging(log: EventLog, echo_debug: bool) -> _LibraryLogHandler:
    handler = _LibraryLogHandler(log, echo_debug)
    lib = logging.getLogger("lumalou")
    lib.setLevel(logging.DEBUG)
    lib.addHandler(handler)
    lib.propagate = False
    # bleak debug output can contain addresses; keep warnings only (redacted).
    ble = logging.getLogger("bleak")
    ble.setLevel(logging.WARNING)
    ble.addHandler(handler)
    ble.propagate = False
    return handler


def describe(error: BaseException | None) -> str:
    if error is None:
        return "none"
    text = str(error)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


# --------------------------------------------------------------------------
# Device selection (never prints the address)
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    device: object  # BLEDevice
    address: str
    name: str | None
    rssi: int | None
    match: str
    advertisement: dict


def match_advertisement(
    name: str | None, manufacturer_data: dict
) -> tuple[str | None, dict]:
    """Same identity as HA's matcher: company 950 + b"MB" prefix, or AP-number name."""
    info: dict = {}
    payload = manufacturer_data.get(MANUFACTURER_ID)
    if payload is not None and is_lumalou_advertisement(bytes(payload)):
        try:
            adv = parse_advertisement(bytes(payload))
            info = {
                "format": adv.format_version,
                "state": adv.connection_state,
                "firmware": adv.firmware_version,
            }
        except ValueError as err:
            info = {"parse_error": str(err)}
        return "manufacturer-950-MB", info
    if name and name.isascii() and name.isdigit():
        return "digits-name", info
    return None, info


async def scan_candidates(timeout: float) -> list[Candidate]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for address, (device, adv) in found.items():
        name = adv.local_name or device.name
        match, info = match_advertisement(name, dict(adv.manufacturer_data))
        if match:
            out.append(Candidate(device, address, name, adv.rssi, match, info))
    out.sort(key=lambda c: -(c.rssi if c.rssi is not None else -999))
    return out


async def find_device(log: EventLog, name: str | None, timeout: float) -> Candidate:
    log.say(f"scanning {timeout:.0f}s for a Lumalou...")
    candidates = await scan_candidates(timeout)
    for c in candidates:
        log.add_secret(c.address)
    for c in candidates:
        log.emit(
            "candidate",
            f"  candidate name={c.name!r} rssi={c.rssi} match={c.match} adv={c.advertisement}",
            name=c.name,
            rssi=c.rssi,
            match=c.match,
            advertisement=c.advertisement,
        )
    if name is not None:
        candidates = [c for c in candidates if c.name == name]
    if not candidates:
        log.emit("scan_empty", "no matching Lumalou found", wanted_name=name)
        raise ProbeError("no matching Lumalou found (is it on? is HA disconnected?)")
    chosen = candidates[0]
    log.emit(
        "selected",
        f"selected name={chosen.name!r} rssi={chosen.rssi} ({chosen.match})",
        name=chosen.name,
        rssi=chosen.rssi,
        match=chosen.match,
        advertisement=chosen.advertisement,
    )
    if chosen.advertisement.get("state") == "connected":
        log.say("  WARNING: advertisement says a central is connected (HA?)")
    return chosen


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


def decode_envelope(envelope) -> dict:
    try:
        decoded = envelope.decode()
    except LumalouError as err:
        out = {"undecodable": describe(err)}
        if len(envelope.args) == 1:
            out["raw_u8_hint"] = envelope.args[0]  # probe hint, not a library decode
        return out
    if envelope.opcode == 0x02 and isinstance(decoded, dict):
        decoded = label_global_state(decoded)
    return {"decoded": jsonable(decoded)}


class Probe:
    """One device, many sessions; the library allows each response type once."""

    def __init__(
        self,
        log: EventLog,
        device,
        *,
        timeout: float = REQUEST_TIMEOUT,
        echo_notifications: bool = False,
    ):
        self.log = log
        self.device = device
        self.timeout = timeout
        self.echo = echo_notifications
        self.client: LumalouClient | None = None
        self.session = 0
        self._fingerprint: str | None = None
        self._closing = False

    async def connect(self) -> None:
        had_session = self.client is not None or self.session > 0
        await self.close()
        if had_session:
            await asyncio.sleep(RECONNECT_GAP)
        self.session += 1
        session = self.session
        client = CLIENT_FACTORY(
            self.device,
            on_response=lambda env: self._on_response(session, env),
            disconnected_callback=lambda c: self._on_disconnected(session, c),
            # Pin later sessions of this run to the first authenticated device.
            expected_device_fingerprint=self._fingerprint,
        )
        self.client = client
        started = time.monotonic()
        self.log.say(f"connecting (session {session})...")
        try:
            await client.connect(timeout=CONNECT_TIMEOUT)
        except (Exception, asyncio.CancelledError) as err:
            self.client = None
            self.log.emit(
                "connect_failed",
                f"connect failed: {describe(err)}",
                session=session,
                error=describe(err),
            )
            raise
        self._remember_identity(client.device_fingerprint)
        self.log.emit(
            "connected",
            f"connected (session {session}, {1000 * (time.monotonic() - started):.0f} ms)",
            session=session,
            ms=round(1000 * (time.monotonic() - started)),
        )

    def _remember_identity(self, fingerprint: str | None) -> None:
        if fingerprint is None:
            self.log.emit(
                "identity", "  identity: library returned no fingerprint", ok=False
            )
            return
        self.log.add_secret(fingerprint)
        if self._fingerprint is None:
            self._fingerprint = fingerprint
            device_hash = self.log.device_hash(fingerprint)
            status = self.log.check_device(device_hash)
            self.log.emit(
                "identity",
                f"  factory token authenticated; device id (salted) {device_hash}: {status}",
                ok=True,
                device_hash=device_hash,
                status=status,
            )

    async def close(self) -> None:
        client, self.client = self.client, None
        if client is None:
            return
        self._closing = True
        try:
            await client.disconnect()
        except Exception as err:
            self.log.emit("disconnect_error", f"disconnect error: {describe(err)}")
        finally:
            self._closing = False

    async def ensure(self) -> None:
        if self.client is None or not self.client.connected:
            await self.connect()

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.connected

    def _on_response(self, session: int, envelope) -> None:
        info = decode_envelope(envelope)
        name = RESPONSES.get(envelope.opcode, f"0x{envelope.opcode:02x}")
        summary = json.dumps(info.get("decoded", info.get("undecodable")))
        self.log.emit(
            "notification",
            f"  <- {name} (0x{envelope.opcode:02x}) seq={envelope.sequence} "
            f"args={envelope.args.hex(' ') or '-'} {summary[:120]}"
            if self.echo
            else None,
            session=session,
            opcode=envelope.opcode,
            response=name,
            sequence=envelope.sequence,
            args_hex=envelope.args.hex(" "),
            **info,
        )

    def _on_disconnected(self, session: int, client) -> None:
        reason = describe(getattr(client, "last_error", None))
        expected = self._closing
        self.log.emit(
            "disconnected",
            None if expected else f"  session {session} ended: {reason}",
            session=session,
            expected=expected,
            reason=reason,
        )

    async def read(self, name: str, *, retry: bool = True) -> dict:
        """One named read (or ``day:<day>``); failures are recorded, not raised."""
        record: dict = {"name": name}
        try:
            await self.ensure()
        except (Exception, asyncio.TimeoutError) as err:
            record.update(status="connect-failed", error=describe(err))
            self.log.emit("read", **record)
            return record
        record["session"] = self.session
        started = time.monotonic()
        try:
            if name.startswith("day:"):
                envelope = await self.client.request_day_routine(
                    name[4:], timeout=self.timeout
                )
            else:
                envelope = await self.client.request_named(name, timeout=self.timeout)
        except FreshSessionRequiredError as err:
            if retry:
                self.log.emit(
                    "fresh_session",
                    f"  {name}: already observed this session; reconnecting",
                    name=name,
                )
                try:
                    await self.connect()
                except Exception as connect_err:
                    record.update(status="connect-failed", error=describe(connect_err))
                    self.log.emit("read", **record)
                    return record
                return await self.read(name, retry=False)
            record.update(status="fresh-session-required", error=describe(err))
        except RequestTimeoutError as err:
            record.update(status="timeout", error=describe(err))
        except LumalouError as err:
            record.update(status="error", error=describe(err))
        else:
            record.update(
                status="ok",
                response_opcode=envelope.opcode,
                response=RESPONSES.get(envelope.opcode),
                args_hex=envelope.args.hex(" "),
                **decode_envelope(envelope),
            )
        record["ms"] = round(1000 * (time.monotonic() - started))
        self.log.emit("read", **record)
        return record

    async def read_many(self, names) -> dict[str, dict]:
        return {name: await self.read(name) for name in names}


# --------------------------------------------------------------------------
# Write allowlist
# --------------------------------------------------------------------------


@dataclass
class Options:
    allow_high: bool = False
    allow_alarm: bool = False


@dataclass
class Plan:
    feature: str
    builder: str
    payload: bytes
    args: dict
    readback: tuple[str, ...]
    dangerous: bool = False
    volume_guard: tuple[str, ...] = ()  # GLOBAL_STATE fields checked before sending

    @property
    def opcode(self) -> int:
        return self.payload[0]

    def describe_lines(self) -> list[str]:
        name = _COMMAND_NAMES.get(self.opcode, "?")
        return [
            f"feature:   {self.feature}{'  [DANGEROUS]' if self.dangerous else ''}",
            f"builder:   lumalou.commands.{self.builder}",
            f"args:      {json.dumps(jsonable(self.args))}",
            f"opcode:    0x{self.opcode:02x} ({name})",
            f"app data:  {self.payload.hex(' ')}",
            f"plaintext: {P.encode_command(self.payload).hex(' ')}  (before encryption)",
            f"readback:  {', '.join(('global_state', *self.readback))}",
        ]


def _norm(text: str) -> str:
    return text.strip().upper().replace("-", "_")


def parse_enum(value, enum_cls: type[IntEnum]) -> IntEnum:
    text = str(value)
    if text.isdigit():
        try:
            return enum_cls(int(text))
        except ValueError:
            pass
    else:
        try:
            return enum_cls[_norm(text)]
        except KeyError:
            pass
    choices = ", ".join(f"{m.name.lower()}={m.value}" for m in enum_cls)
    raise ProbeError(f"invalid {enum_cls.__name__} {value!r}; one of: {choices}")


def parse_int(value, low: int, high: int, what: str) -> int:
    try:
        number = int(str(value), 10)
    except ValueError:
        raise ProbeError(f"{what} must be an integer") from None
    if not low <= number <= high:
        raise ProbeError(f"{what} must be {low}..{high}")
    return number


def parse_onoff(value) -> bool:
    text = str(value).lower()
    if text in ("on", "1", "true", "yes"):
        return True
    if text in ("off", "0", "false", "no"):
        return False
    raise ProbeError(f"expected on/off, got {value!r}")


def cap(value: int, limit: int, what: str, opts: Options) -> int:
    if value > limit and not opts.allow_high:
        raise ProbeError(
            f"{what} {value} is above the safety cap {limit}; use --allow-high"
        )
    return value


def alarm_guard(values, opts: Options, what: str) -> None:
    """Alarm 0 is ACTIVE (audible tone at the scheduled time); 9 is inactive."""
    if not opts.allow_alarm and any(int(v) == 0 for v in values):
        raise ProbeError(
            f"{what} contains ACTIVE (0), an audible alarm; use --allow-alarm"
        )


def parse_time(value) -> ClockTime | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value))
    if not match:
        raise ProbeError(f"time must be HH:MM or null, got {value!r}")
    try:
        return ClockTime(int(match[1]), int(match[2]))
    except ValueError as err:
        raise ProbeError(str(err)) from None


def weekly_times_from_json(obj) -> WeeklyTimes:
    days = obj["days"] if isinstance(obj, dict) else obj
    if not isinstance(days, list) or len(days) != 7:
        raise ProbeError('weekly times: need 7 Sunday-first "HH:MM"/null entries')
    return WeeklyTimes(tuple(parse_time(d) for d in days))


def weekly_alarms_from_json(obj) -> WeeklyAlarms:
    if not isinstance(obj, dict) or len(obj.get("days", ())) != 7 or "sound" not in obj:
        raise ProbeError('alarms: need {"days": [7 Alarm values], "sound": 0..15}')
    from lumalou import Alarm

    days = tuple(parse_enum(d, Alarm) for d in obj["days"])
    return WeeklyAlarms(days, parse_int(obj["sound"], 0, 15, "sound"))


def daily_routine_from_json(obj) -> DailyRoutine:
    if not isinstance(obj, dict):
        raise ProbeError('routine: need {"time": "HH:MM"|null, "slots"|"steps": [...]}')
    when = parse_time(obj.get("time"))
    try:
        if "steps" in obj:
            return DailyRoutine.from_steps(when, obj["steps"])
        slots = obj.get("slots")
        if not isinstance(slots, list) or len(slots) != 12:
            raise ProbeError("routine slots: need exactly 12 entries")
        return DailyRoutine(
            when,
            tuple(
                None if s is None else RoutineTask(int(s["step"]), int(s["task"]))
                for s in slots
            ),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise ProbeError(f"invalid routine: {err}") from None


def load_json_file(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ProbeError(f"cannot read JSON {path}: {err}") from None


def _arity(args: list[str], n: int, usage: str) -> None:
    if len(args) != n:
        raise ProbeError(f"usage: {usage}")


@dataclass(frozen=True)
class Feature:
    name: str
    usage: str
    builder: str
    build: Callable[
        [list[str], Options], tuple[bytes, dict, tuple[str, ...], tuple[str, ...]]
    ]
    dangerous: bool = False
    note: str = ""


def _simple(builder, readback=(), guard=()):
    def build(args, opts):
        return builder(), {}, readback, guard

    return build


def _f_light_color(args, opts):
    _arity(args, 1, "light-color COLOR")
    from lumalou import Color

    color = parse_enum(args[0], Color)
    return C.set_light_color(color), {"color": color.name}, ("light_color",), ()


def _f_light_brightness(args, opts):
    _arity(args, 1, "light-brightness 0..9")
    level = cap(
        parse_int(args[0], 0, 9, "brightness"), BRIGHTNESS_CAP, "brightness", opts
    )
    return C.set_led_brightness(level), {"level": level}, ("led_brightness",), ()


def _f_light_duration(args, opts):
    _arity(args, 1, "light-duration DURATION")
    from lumalou import LightDuration

    value = parse_enum(args[0], LightDuration)
    return (
        C.set_light_duration(value),
        {"duration": value.name},
        ("light_duration",),
        (),
    )


def _f_play(args, opts):
    _arity(args, 1, "play SOURCE")
    from lumalou import Audio

    source = parse_enum(args[0], Audio)
    return (
        C.play_audio(source),
        {"source": source.name},
        ("song_playing",),
        ("currentVolume",),
    )


def _f_playlist_duration(args, opts):
    _arity(args, 1, "playlist-duration DURATION")
    from lumalou import PlaylistDuration

    value = parse_enum(args[0], PlaylistDuration)
    return (
        C.set_playlist_duration(value),
        {"duration": value.name},
        ("playlist_duration",),
        (),
    )


def _f_playlist(args, opts):
    if not 1 <= len(args) <= 12:
        raise ProbeError("usage: playlist SONG [SONG ...]  (1..12 songs, ids 1..12)")
    from lumalou import Song

    songs = [parse_enum(a, Song) for a in args]
    if any(not 1 <= s <= 12 for s in songs):
        raise ProbeError("playlist songs must be ids 1..12 (not noises)")
    playlist = MusicPlaylist.from_songs(int(s) for s in songs)
    return (
        C.set_music_playlist(playlist),
        {"songs": [s.name for s in songs], "slots": list(playlist.slots)},
        ("music_playlist",),
        (),
    )


def _f_volume(args, opts):
    _arity(args, 1, "volume 0..9")
    level = cap(parse_int(args[0], 0, 9, "volume"), VOLUME_CAP, "volume", opts)
    return C.set_volume(level), {"level": level}, ("volume",), ()


def _f_routine_volume(args, opts):
    _arity(args, 1, "routine-volume N")
    level = cap(
        parse_int(args[0], 0, 255, "routine volume"), VOLUME_CAP, "routine volume", opts
    )
    return C.set_routine_volume(level), {"level": level}, ("routine_volume",), ()


def _f_soother(args, opts):
    _arity(args, 1, "soother on|off")
    on = parse_onoff(args[0])
    guard = ("currentVolume",) if on else ()
    return C.set_global_on(on), {"on": on}, ("operation_mode",), guard


_GLOBAL_FIELDS = (
    "lights_on",
    "brightness",
    "music_on",
    "volume",
    "r2r",
    "r2r_alarm",
    "nap_alarm",
    "routine",
)


def _f_global_state(args, opts):
    if not args:
        raise ProbeError(
            f"usage: global-state FIELD=N ... (fields: {', '.join(_GLOBAL_FIELDS)})"
        )
    values = {}
    for item in args:
        key, sep, raw = item.partition("=")
        if not sep or key not in _GLOBAL_FIELDS or key in values:
            raise ProbeError(
                f"bad field {item!r}; use FIELD=N with FIELD in {_GLOBAL_FIELDS}"
            )
        # 0x0F is the builder's "leave unchanged" sentinel; do not allow it.
        values[key] = parse_int(raw, 0, 14, key)
    if "brightness" in values:
        cap(values["brightness"], BRIGHTNESS_CAP, "brightness", opts)
    if "volume" in values:
        cap(values["volume"], VOLUME_CAP, "volume", opts)
    guard = ("currentVolume",) if values.get("music_on") else ()
    return C.set_global_state(**values), values, (), guard


def _f_set_date(args, opts):
    if args not in ([], ["now"]):
        raise ProbeError("usage: set-date [now]")
    now = datetime.datetime.now().astimezone()
    weekday = (now.weekday() + 1) % 7  # Python Mon=0 -> device Sun=0
    return (
        C.set_current_date(now.hour, now.minute, now.second, weekday),
        {
            "hour": now.hour,
            "minute": now.minute,
            "second": now.second,
            "weekday": weekday,
        },
        ("current_date",),
        (),
    )


def _f_clock_settings(args, opts):
    _arity(args, 3, "clock-settings on|off BRIGHTNESS 12|24")
    display = parse_onoff(args[0])
    brightness = cap(
        parse_int(args[1], 0, 9, "clock brightness"),
        BRIGHTNESS_CAP,
        "clock brightness",
        opts,
    )
    fmt = {"12": 0, "24": 1, "h12": 0, "h24": 1}.get(args[2].lower())
    if fmt is None:
        raise ProbeError("clock format must be 12 or 24")
    return (
        C.set_clock_settings(display, brightness, fmt),
        {"display_on": display, "brightness": brightness, "format": fmt},
        ("clock_settings",),
        (),
    )


def _f_bool(builder, readback, usage):
    def build(args, opts):
        _arity(args, 1, usage)
        on = parse_onoff(args[0])
        return builder(on), {"on": on}, readback, ()

    return build


def _f_weekly_times(builder, readback, usage):
    def build(args, opts):
        _arity(args, 1, usage)
        week = weekly_times_from_json(load_json_file(args[0]))
        return builder(week), {"week": week}, readback, ()

    return build


def _f_r2r_alarms(args, opts):
    _arity(args, 1, "r2r-alarms FILE.json")
    alarms = weekly_alarms_from_json(load_json_file(args[0]))
    alarm_guard(alarms.days, opts, "r2r alarms")
    return (
        C.set_r2r_alarms(alarms),
        {"alarms": alarms},
        ("r2r_alarms", "r2r_alarm_status"),
        (),
    )


def _f_nap_alarm(args, opts):
    _arity(args, 1, "nap-alarm ALARM")
    from lumalou import Alarm

    alarm = parse_enum(args[0], Alarm)
    alarm_guard((alarm,), opts, "nap alarm")
    return (
        C.set_nap_alarm(alarm),
        {"alarm": alarm.name},
        ("nap_alarm", "nap_alarm_status"),
        (),
    )


def _f_nap_start(args, opts):
    _arity(args, 1, "nap-start DURATION")
    from lumalou import NapDuration

    duration = parse_enum(args[0], NapDuration)
    return (
        C.start_nap(duration),
        {"duration": duration.name},
        ("nap_current_status", "operation_mode"),
        ("currentVolume",),
    )


def _f_routine_music(args, opts):
    _arity(
        args,
        3,
        "routine-music-settings MUSIC(0..255) TASK_REWARD(0..15) ROUTINE_REWARD(0..15)",
    )
    settings = RoutineMusicSettings(
        parse_int(args[0], 0, 255, "music"),
        parse_int(args[1], 0, 15, "task reward"),
        parse_int(args[2], 0, 15, "routine reward"),
    )
    return (
        C.set_routine_music_settings(settings),
        {"settings": settings},
        ("routine_music_status",),
        (),
    )


def _f_routine_control(args, opts):
    _arity(args, 1, "routine-control CONTROL")
    from lumalou import RoutineControl

    ctrl = parse_enum(args[0], RoutineControl)
    return C.routine_control(ctrl), {"control": ctrl.name}, ("routine_task_status",), ()


def _f_day_routine(args, opts):
    _arity(args, 2, "day-routine DAY FILE.json")
    day = args[0].lower()
    if day not in DAYS:
        raise ProbeError(f"day must be one of {', '.join(DAYS)}")
    routine = daily_routine_from_json(load_json_file(args[1]))
    return (
        C.set_day_routine(day, routine),
        {"day": day, "routine": routine},
        (f"day:{day}",),
        (),
    )


FEATURES: dict[str, Feature] = {
    f.name: f
    for f in (
        Feature(
            "light-color",
            "COLOR (name or 0..9)",
            "set_light_color",
            _f_light_color,
            note="the web client's light-on action",
        ),
        Feature(
            "light-brightness",
            "0..9 (cap 3)",
            "set_led_brightness",
            _f_light_brightness,
            note="does not switch the light on",
        ),
        Feature("light-off", "", "turn_off_backlight", _simple(C.turn_off_backlight)),
        Feature(
            "light-duration",
            "DURATION (min_15..min_1 or 0..5)",
            "set_light_duration",
            _f_light_duration,
        ),
        Feature(
            "play",
            "SOURCE (Audio name or 0..7)",
            "play_audio",
            _f_play,
            note="refused if current volume is above the cap",
        ),
        Feature(
            "audio-off",
            "",
            "turn_off_audio",
            _simple(C.turn_off_audio, ("song_playing",)),
        ),
        Feature(
            "playlist-duration",
            "DURATION (min_15..min_1 or 0..6)",
            "set_playlist_duration",
            _f_playlist_duration,
        ),
        Feature(
            "playlist", "SONG [SONG ...] (1..12)", "set_music_playlist", _f_playlist
        ),
        Feature("volume", "0..9 (cap 3)", "set_volume", _f_volume),
        Feature(
            "routine-volume", "0..255 (cap 3)", "set_routine_volume", _f_routine_volume
        ),
        Feature(
            "soother",
            "on|off",
            "set_global_on",
            _f_soother,
            dangerous=True,
            note="light AND sound; on is refused if current volume is above the cap",
        ),
        Feature(
            "global-state",
            "FIELD=N ...",
            "set_global_state",
            _f_global_state,
            dangerous=True,
            note="multi-field aggregate; nibble semantics unverified",
        ),
        Feature(
            "set-date",
            "[now]",
            "set_current_date",
            _f_set_date,
            note="local time, weekday Sunday=0",
        ),
        Feature(
            "clock-settings",
            "on|off BRIGHTNESS 12|24",
            "set_clock_settings",
            _f_clock_settings,
        ),
        Feature(
            "r2r-status",
            "on|off",
            "set_r2r_status",
            _f_bool(C.set_r2r_status, ("r2r_status",), "r2r-status on|off"),
        ),
        Feature(
            "r2r-times",
            "FILE.json",
            "set_r2r_times",
            _f_weekly_times(C.set_r2r_times, ("r2r_times",), "r2r-times FILE.json"),
        ),
        Feature(
            "sleepy-times",
            "FILE.json",
            "set_sleepy_times",
            _f_weekly_times(
                C.set_sleepy_times, ("sleepy_times",), "sleepy-times FILE.json"
            ),
        ),
        Feature(
            "r2r-alarms",
            "FILE.json",
            "set_r2r_alarms",
            _f_r2r_alarms,
            note="any 0=ACTIVE needs --allow-alarm",
        ),
        Feature(
            "nap-alarm",
            "ALARM (Alarm name or 0..10)",
            "set_nap_alarm",
            _f_nap_alarm,
            note="0=ACTIVE needs --allow-alarm",
        ),
        Feature(
            "nap-start",
            "DURATION (NapDuration name or 0..11)",
            "start_nap",
            _f_nap_start,
            dangerous=True,
            note="starts a nap session",
        ),
        Feature(
            "routine-status",
            "on|off",
            "set_routine_status",
            _f_bool(
                C.set_routine_status, ("routine_mode_status",), "routine-status on|off"
            ),
        ),
        Feature(
            "routine-music-settings",
            "MUSIC TASK_REWARD ROUTINE_REWARD",
            "set_routine_music_settings",
            _f_routine_music,
        ),
        Feature(
            "routine-start",
            "",
            "start_routine_mode",
            _simple(
                C.start_routine_mode,
                ("routine_task_status", "operation_mode"),
                ("routineVolume",),
            ),
            dangerous=True,
            note="starts routine mode",
        ),
        Feature(
            "routine-control",
            "CONTROL (RoutineControl name or 0..4)",
            "routine_control",
            _f_routine_control,
            dangerous=True,
            note="drives a running routine",
        ),
        Feature("day-routine", "DAY FILE.json", "set_day_routine", _f_day_routine),
    )
}

# Builders deliberately absent from the allowlist (tests enforce the split).
EXCLUDED_BUILDERS = {
    "request": "read builder, used by read-all",
    "request_day_routine": "read builder, used by read-all",
    "bcd": "helper",
    "reduce_low_nibbles": "helper",
}


def build_plan(feature_name: str, args: list[str], opts: Options) -> Plan:
    feature = FEATURES.get(feature_name)
    if feature is None:
        raise ProbeError(f"unknown feature {feature_name!r}")
    try:
        payload, decoded, readback, guard = feature.build(list(args), opts)
    except ProbeError:
        raise
    except (ValueError, TypeError) as err:
        raise ProbeError(f"{feature_name}: {err}") from None
    return Plan(
        feature.name,
        feature.builder,
        payload,
        decoded,
        tuple(readback),
        feature.dangerous,
        tuple(guard),
    )


def check_volume_guard(plan: Plan, before_state: dict | None, opts: Options) -> None:
    if not plan.volume_guard or opts.allow_high:
        return
    if before_state is None:
        raise ProbeError(
            "cannot verify current volume (GLOBAL_STATE unread); use --allow-high"
        )
    for fieldname in plan.volume_guard:
        value = before_state.get(fieldname)
        if not isinstance(value, int) or value > VOLUME_CAP:
            raise ProbeError(
                f"{fieldname} is {value}, above cap {VOLUME_CAP}; lower it first or use --allow-high"
            )


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------

BASELINE_FORMAT = "lumalou-hw-probe-baseline/1"
BASELINE_READS: tuple[str, ...] = (
    "global_state",
    "music_playlist",
    "playlist_duration",
    "clock_settings",
    "routine_music_status",
    "routine_volume",
    "light_duration",
    "r2r_times",
    "sleepy_times",
    "r2r_alarms",
    *DAY_READS,
    "r2r_status",
    "r2r_alarm_status",
    "routine_mode_status",
    # nap_alarm_status / nap_alarm are omitted: firmware 0.3.7 never answers
    # them (C.UNANSWERED_REQUESTS) and each timeout costs a reconnect.
)


@dataclass
class RestoreOp:
    block: str
    builder: str
    payload: bytes
    inferred: bool
    verify: tuple[str, ...]  # blocks whose raw bytes (or GS fields) must match
    gs_fields: dict = field(default_factory=dict)


def _raw(block: dict) -> bytes | None:
    if block.get("status") != "ok" or block.get("args_hex") is None:
        return None
    return bytes.fromhex(block["args_hex"])


def plan_restore(
    baseline: dict, *, include_inferred: bool, opts: Options
) -> tuple[list[RestoreOp], list[tuple[str, str]]]:
    """Rebuild writes from saved raw bytes via the library's strict decoders."""
    if baseline.get("format") != BASELINE_FORMAT:
        raise ProbeError("not a probe baseline file")
    blocks = baseline["blocks"]
    ops: list[RestoreOp] = []
    skipped: list[tuple[str, str]] = []

    def typed(name, decode, build, builder):
        raw = _raw(blocks.get(name, {}))
        if raw is None:
            skipped.append((name, "not captured"))
            return
        try:
            ops.append(RestoreOp(name, builder, build(decode(raw)), False, (name,)))
        except ProbeError as err:
            skipped.append((name, str(err)))
        except ValueError as err:
            skipped.append((name, f"undecodable: {err}"))

    typed(
        "music_playlist",
        parse_music_playlist,
        C.set_music_playlist,
        "set_music_playlist",
    )

    def clock(s: ClockSettings):
        cap(s.brightness, BRIGHTNESS_CAP, "clock brightness", opts)
        return C.set_clock_settings(s.display_on, s.brightness, s.format)

    typed("clock_settings", parse_clock_settings, clock, "set_clock_settings")
    typed("r2r_times", decode_weekly_times, C.set_r2r_times, "set_r2r_times")
    typed("sleepy_times", decode_weekly_times, C.set_sleepy_times, "set_sleepy_times")

    def alarms(value: WeeklyAlarms):
        alarm_guard(value.days, opts, "saved r2r alarms")
        return C.set_r2r_alarms(value)

    typed("r2r_alarms", decode_weekly_alarms, alarms, "set_r2r_alarms")
    for day in DAYS:
        typed(
            f"day:{day}",
            decode_daily_routine,
            lambda routine, day=day: C.set_day_routine(day, routine),
            "set_day_routine",
        )

    # Scalars have no standalone response schema; GLOBAL_STATE is typed and
    # carries them, so --include-inferred rebuilds them from its fields only.
    inferred_blocks = (
        "playlist_duration",
        "light_duration",
        "routine_volume",
        "routine_music_status",
        "r2r_status",
        "routine_mode_status",
    )
    gs = (blocks.get("global_state") or {}).get("decoded")
    if not include_inferred:
        skipped.extend(
            (b, "inferred; pass --include-inferred") for b in inferred_blocks
        )
    elif not isinstance(gs, dict):
        skipped.extend((b, "GLOBAL_STATE not captured") for b in inferred_blocks)
    else:

        def inferred(block, builder, fields, build):
            values = {f: gs.get(f) for f in fields}
            try:
                payload = build(*values.values())
            except (ProbeError, ValueError, TypeError) as err:
                skipped.append((block, f"GLOBAL_STATE {values}: {err}"))
                return
            ops.append(
                RestoreOp(block, builder, payload, True, ("global_state",), values)
            )

        def as_bool(v):
            if v not in (0, 1):
                raise ValueError("not 0/1")
            return bool(v)

        inferred(
            "playlist_duration",
            "set_playlist_duration",
            ("playlistDuration",),
            C.set_playlist_duration,
        )
        inferred(
            "light_duration",
            "set_light_duration",
            ("lightDuration",),
            C.set_light_duration,
        )
        inferred(
            "routine_volume",
            "set_routine_volume",
            ("routineVolume",),
            lambda v: C.set_routine_volume(cap(v, VOLUME_CAP, "routine volume", opts)),
        )
        inferred(
            "routine_music_status",
            "set_routine_music_settings",
            ("routineMusicStatus", "taskRewardSfx", "routineRewardSfx"),
            lambda m, t, r: C.set_routine_music_settings(RoutineMusicSettings(m, t, r)),
        )
        inferred(
            "r2r_status",
            "set_r2r_status",
            ("ready2RiseStatus",),
            lambda v: C.set_r2r_status(as_bool(v)),
        )
        inferred(
            "routine_mode_status",
            "set_routine_status",
            ("routineModeStatus",),
            lambda v: C.set_routine_status(as_bool(v)),
        )

    for name, reason in (
        ("r2r_alarm_status", "no dedicated setter"),
        ("nap_alarm_status", "no dedicated setter"),
        ("nap_alarm", "lifetime unknown; not auto-restored (use send nap-alarm)"),
    ):
        skipped.append((name, reason))
    return ops, skipped


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _short(value, width=60) -> str:
    text = (
        json.dumps(value, separators=(",", ":"))
        if not isinstance(value, str)
        else value
    )
    return text if len(text) <= width else text[: width - 1] + "…"


def print_read_table(log: EventLog, records: list[dict]) -> None:
    log.say("")
    log.say(f"{'name':<22} {'status':<16} {'resp':<5} {'ms':>5}  {'args':<30} decoded")
    log.say("-" * 110)
    for r in records:
        resp = f"{r['response_opcode']:02x}" if "response_opcode" in r else "-"
        if r.get("status") == "ok":
            value = r.get("decoded", "undecodable: " + r.get("undecodable", "?"))
        else:
            value = r.get("error", "")
        log.say(
            f"{r['name']:<22} {r.get('status', '?'):<16} {resp:<5} {r.get('ms', 0):>5}  "
            f"{_short(r.get('args_hex') or '-', 30):<30} {_short(value)}"
        )
    counts: dict[str, int] = {}
    for r in records:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    log.emit("summary", f"\nsummary: {counts}", counts=counts)


async def cmd_scan(args, log: EventLog) -> int:
    candidates = await scan_candidates(args.scan_timeout)
    for c in candidates:
        log.add_secret(c.address)
        log.emit(
            "candidate",
            f"name={c.name!r} rssi={c.rssi} match={c.match} adv={c.advertisement}",
            name=c.name,
            rssi=c.rssi,
            match=c.match,
            advertisement=c.advertisement,
        )
    if not candidates:
        log.say("no Lumalou candidates found")
    return 0


async def cmd_read_all(args, log: EventLog, probe: Probe) -> int:
    records = []
    names = READ_NAMES
    if not args.include_unanswered:
        # Firmware 0.3.7 never answers these; each timeout costs a reconnect.
        names = tuple(n for n in READ_NAMES if n not in C.UNANSWERED_REQUESTS)
        log.say(
            f"  skipping {', '.join(sorted(C.UNANSWERED_REQUESTS))} (never answered)"
        )
    for name in (*names, *DAY_READS):
        record = await probe.read(name)
        records.append(record)
        log.say(f"  {name}: {record['status']}")
        # Each read retries the connection via ensure(); stop after two
        # consecutive connect failures instead of hammering the radio.
        if (
            record["status"] == "connect-failed"
            and len(records) >= 2
            and records[-2].get("status") == "connect-failed"
        ):
            log.say("two consecutive connect failures; stopping")
            break
    print_read_table(log, records)
    return 0 if all(r.get("status") == "ok" for r in records) else 1


async def cmd_watch(args, log: EventLog, probe: Probe) -> int:
    deadline = time.monotonic() + args.seconds
    await probe.connect()
    log.say(
        f"watching for {args.seconds:.0f}s; press buttons on the device (Ctrl-C to stop)"
    )
    reconnects = 0
    while (remaining := deadline - time.monotonic()) > 0:
        if not probe.connected:
            if reconnects >= 5:
                log.emit("watch_abort", "too many reconnects; stopping")
                return 1
            reconnects += 1
            try:
                await probe.connect()
            except Exception:
                await asyncio.sleep(2)
                continue
        await asyncio.sleep(min(0.5, remaining))
    log.emit(
        "watch_done",
        f"watch finished (reconnects: {reconnects})",
        reconnects=reconnects,
    )
    return 0


def diff_records(before: dict, after: dict) -> list[tuple[str, str, object, object]]:
    rows = []
    for name in before.keys() | after.keys():
        b, a = before.get(name, {}), after.get(name, {})
        if b.get("status") != "ok" or a.get("status") != "ok":
            rows.append((name, "unavailable", b.get("status"), a.get("status")))
            continue
        bd, ad = b.get("decoded"), a.get("decoded")
        if isinstance(bd, dict) and isinstance(ad, dict) and name == "global_state":
            for key in sorted(bd.keys() | ad.keys()):
                if bd.get(key) != ad.get(key):
                    rows.append(
                        (f"global_state.{key}", "changed", bd.get(key), ad.get(key))
                    )
        elif b.get("args_hex") != a.get("args_hex"):
            rows.append(
                (name, "changed", bd or b.get("args_hex"), ad or a.get("args_hex"))
            )
        else:
            rows.append((name, "unchanged", b.get("args_hex"), a.get("args_hex")))
    return sorted(rows)


async def cmd_send(args, log: EventLog, opts: Options) -> int:
    plan = build_plan(args.feature, args.args, opts)
    log.say("planned write:")
    for line in plan.describe_lines():
        log.say(f"  {line}")
    log.emit(
        "plan",
        feature=plan.feature,
        builder=plan.builder,
        args=plan.args,
        opcode=plan.opcode,
        payload_hex=plan.payload.hex(" "),
        dangerous=plan.dangerous,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        if plan.dangerous and not args.dangerous:
            log.say("  (dangerous: sending would also need --dangerous)")
        log.say("dry run: nothing sent, no Bluetooth used")
        return 0
    if plan.dangerous and not args.dangerous:
        raise ProbeError(
            f"{plan.feature} is marked dangerous; add --dangerous to send it"
        )
    if not args.yes:
        raise ProbeError("refusing to write without --yes (or use --dry-run)")

    candidate = await find_device(log, args.name, args.scan_timeout)
    probe = Probe(log, candidate.device, timeout=args.timeout, echo_notifications=True)
    try:
        await probe.connect()
        reads = ("global_state", *plan.readback)
        before = await probe.read_many(reads)
        gs = (
            before["global_state"].get("decoded")
            if before["global_state"].get("status") == "ok"
            else None
        )
        check_volume_guard(plan, gs, opts)
        await probe.ensure()
        log.emit(
            "send",
            f"-> sending {plan.feature}: {plan.payload.hex(' ')}",
            payload_hex=plan.payload.hex(" "),
        )
        try:
            await probe.client.send(plan.payload)
        except LumalouError as err:
            log.emit(
                "send_failed",
                f"send failed (not retried): {describe(err)}",
                error=describe(err),
            )
            return 1
        log.say(f"sent; logging notifications for {SETTLE_SECONDS:.0f}s")
        send_session = probe.session
        await asyncio.sleep(SETTLE_SECONDS)
        if args.repeat_state_read:
            await repeat_state_read(log, probe, send_session)
        await probe.connect()  # before-reads consumed these response types
        after = await probe.read_many(reads)
    finally:
        await probe.close()
    rows = diff_records(before, after)
    log.say("\nbefore -> after:")
    for name, status, b, a in rows:
        if status == "unchanged":
            log.say(f"  {name:<34} unchanged ({_short(b, 40)})")
        else:
            log.say(f"  {name:<34} {status}: {_short(b, 40)} -> {_short(a, 40)}")
    log.emit("diff", rows=[list(r) for r in rows])
    return 0


async def repeat_state_read(log: EventLog, probe: Probe, send_session: int) -> None:
    """Experiment: a second GLOBAL_STATE request in the write session.

    Goes through the normal library API only; the library is expected to
    refuse (FreshSessionRequiredError) before anything is written.
    """
    if not probe.connected or probe.session != send_session:
        log.emit(
            "repeat_state_read",
            "repeat-state-read: write session already ended; experiment skipped",
            outcome="session-ended",
        )
        return
    record = await probe.read("global_state", retry=False)
    outcome = {
        "ok": "device answered (library allowed it)",
        "fresh-session-required": "library refused before sending",
    }.get(record["status"], record["status"])
    log.emit(
        "repeat_state_read",
        f"repeat-state-read: {outcome}",
        outcome=record["status"],
        same_session=record.get("session") == send_session,
        record=record,
    )


async def cmd_baseline_save(args, log: EventLog, probe: Probe) -> int:
    blocks = await probe.read_many(BASELINE_READS)
    identity = None
    if probe._fingerprint:
        identity = log.device_hash(probe._fingerprint)
    doc = {
        "format": BASELINE_FORMAT,
        "saved_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "library_version": lumalou.__version__,
        "device_hash": identity,
        "blocks": {
            name: {k: v for k, v in record.items() if k not in ("session", "ms")}
            for name, record in blocks.items()
        },
    }
    path = Path(args.file)
    path.write_text(log.redact(json.dumps(doc, indent=2)) + "\n", encoding="utf-8")
    print_read_table(log, list(blocks.values()))
    log.emit("baseline_saved", f"baseline written to {path}", file=str(path))
    return 0 if all(r.get("status") == "ok" for r in blocks.values()) else 1


async def cmd_baseline_restore(args, log: EventLog, opts: Options) -> int:
    baseline = load_json_file(args.file)
    ops, skipped = plan_restore(
        baseline, include_inferred=args.include_inferred, opts=opts
    )
    log.say(f"restore plan from {args.file} (saved {baseline.get('saved_at')}):")
    for op in ops:
        tag = " [inferred from GLOBAL_STATE]" if op.inferred else ""
        log.say(
            f"  {op.block:<22} {op.builder:<28} opcode 0x{op.payload[0]:02x} "
            f"{op.payload.hex(' ')}{tag}"
        )
    for name, reason in skipped:
        log.say(f"  {name:<22} skipped: {reason}")
    log.emit(
        "restore_plan",
        ops=[
            {
                "block": o.block,
                "builder": o.builder,
                "payload_hex": o.payload.hex(" "),
                "inferred": o.inferred,
            }
            for o in ops
        ],
        skipped=skipped,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        log.say("dry run: nothing sent, no Bluetooth used")
        return 0
    if not args.yes:
        raise ProbeError("refusing to write without --yes (or use --dry-run)")
    if not ops:
        log.say("nothing to restore")
        return 0

    candidate = await find_device(log, args.name, args.scan_timeout)
    probe = Probe(log, candidate.device, timeout=args.timeout, echo_notifications=True)
    failures = 0
    try:
        await probe.connect()
        saved_hash = baseline.get("device_hash")
        if (
            saved_hash
            and probe._fingerprint
            and log.device_hash(probe._fingerprint) != saved_hash
        ):
            raise ProbeError(
                "baseline was saved from a different device (or another --out salt)"
            )
        for op in ops:
            try:
                await probe.ensure()
                log.emit(
                    "send",
                    f"-> {op.block}: {op.payload.hex(' ')}",
                    block=op.block,
                    payload_hex=op.payload.hex(" "),
                )
                await probe.client.send(op.payload)
            except LumalouError as err:
                failures += 1
                log.emit(
                    "send_failed",
                    f"   {op.block} failed (not retried): {describe(err)}",
                    block=op.block,
                )
            await asyncio.sleep(RESTORE_SPACING)
        await asyncio.sleep(SETTLE_SECONDS)
        await probe.connect()
        verify_names = list(dict.fromkeys(n for op in ops for n in op.verify))
        after = await probe.read_many(verify_names)
    finally:
        await probe.close()

    blocks = baseline["blocks"]
    mismatches = []
    for op in ops:
        if op.inferred:
            got = after.get("global_state", {}).get("decoded") or {}
            for key, want in op.gs_fields.items():
                if got.get(key) != want:
                    mismatches.append((f"global_state.{key}", want, got.get(key)))
        else:
            want = blocks[op.block].get("args_hex")
            got = after.get(op.block, {}).get("args_hex")
            if want != got:
                mismatches.append(
                    (op.block, want, got or after.get(op.block, {}).get("status"))
                )
    for name, want, got in mismatches:
        log.say(f"  MISMATCH {name}: saved {want} != now {got}")
    log.emit(
        "restore_verify",
        f"restore done: {len(ops) - failures}/{len(ops)} sent, {len(mismatches)} mismatch(es)",
        failures=failures,
        mismatches=[list(m) for m in mismatches],
    )
    return 0 if not failures and not mismatches else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _feature_epilog() -> str:
    lines = ["features (builder in lumalou.commands):"]
    for f in FEATURES.values():
        flag = "  [--dangerous]" if f.dangerous else ""
        note = f"  ({f.note})" if f.note else ""
        lines.append(f"  {f.name} {f.usage}".rstrip() + f"  -> {f.builder}{flag}{note}")
    lines.append("")
    lines.append(
        'JSON: times {"days": ["07:00", null, ...7 Sunday-first]}; alarms '
        '{"days": [7 Alarm], "sound": 0..15}; routine {"time": "HH:MM"|null, '
        '"steps": [[task,...], ...]} or {"time":..., "slots": [12 x null|{"step","task"}]}'
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--out", default="probe-out", help="log directory (default: ./probe-out)"
    )
    common.add_argument(
        "--name", help="exact advertised name to select (default: strongest match)"
    )
    common.add_argument(
        "--scan-timeout", type=float, default=SCAN_TIMEOUT, help="seconds (default 15)"
    )
    common.add_argument(
        "--timeout",
        type=float,
        default=REQUEST_TIMEOUT,
        help="per-read timeout seconds (default 3)",
    )
    common.add_argument(
        "--verbose", action="store_true", help="echo library debug lines to stdout"
    )

    writes = argparse.ArgumentParser(add_help=False)
    writes.add_argument(
        "--yes", action="store_true", help="actually write to the device"
    )
    writes.add_argument(
        "--dry-run", action="store_true", help="print payloads without Bluetooth"
    )
    writes.add_argument(
        "--allow-high",
        action="store_true",
        help=f"lift volume/brightness caps ({VOLUME_CAP})",
    )
    writes.add_argument(
        "--allow-alarm", action="store_true", help="allow Alarm 0 (ACTIVE, audible)"
    )

    parser = argparse.ArgumentParser(
        prog="probe.py",
        description="Opt-in Lumalou (gld09) hardware probe. Run only with the owner present.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "scan", parents=[common], help="list matching advertisements (no connect)"
    )
    read_all = sub.add_parser(
        "read-all",
        parents=[common],
        help="read every named response and all 7 day routines",
    )
    read_all.add_argument(
        "--include-unanswered",
        action="store_true",
        help="also request nap_alarm_status and nap_alarm (time out on 0.3.7)",
    )
    watch = sub.add_parser(
        "watch", parents=[common], help="log all notifications for N seconds"
    )
    watch.add_argument("--seconds", type=float, default=60.0)
    send = sub.add_parser(
        "send",
        parents=[common, writes],
        help="send ONE allowlisted write, then read back and diff",
        epilog=_feature_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    send.add_argument("feature", choices=list(FEATURES), metavar="FEATURE")
    send.add_argument("args", nargs="*", help="feature arguments")
    send.add_argument(
        "--dangerous",
        action="store_true",
        help="required for soother/aggregate/nap/routine actions",
    )
    send.add_argument(
        "--repeat-state-read",
        action="store_true",
        help="experiment: request GLOBAL_STATE again in the write session and record the outcome",
    )
    baseline = sub.add_parser(
        "baseline", help="save or restore persistent profile blocks"
    )
    bsub = baseline.add_subparsers(dest="baseline_command", required=True)
    save = bsub.add_parser(
        "save", parents=[common], help="read profile blocks to a JSON file"
    )
    save.add_argument("file")
    restore = bsub.add_parser(
        "restore",
        parents=[common, writes],
        help="write a saved baseline back and verify",
    )
    restore.add_argument("file")
    restore.add_argument(
        "--include-inferred",
        action="store_true",
        help="also restore scalar settings rebuilt from saved GLOBAL_STATE fields",
    )
    return parser


async def run(args) -> int:
    command = (
        args.command
        if args.command != "baseline"
        else f"baseline-{args.baseline_command}"
    )
    log = EventLog(Path(args.out), command)
    handler = install_logging(log, echo_debug=args.verbose or command == "watch")
    log.emit(
        "start",
        f"lumalou {lumalou.__version__}; log: {log.path}",
        command=command,
        argv=[a for a in sys.argv[1:]],
        library_version=lumalou.__version__,
    )
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(signal.SIGINT, task.cancel)
    opts = Options(
        allow_high=getattr(args, "allow_high", False),
        allow_alarm=getattr(args, "allow_alarm", False),
    )
    probe = None
    try:
        if command == "scan":
            return await cmd_scan(args, log)
        if command == "send":
            return await cmd_send(args, log, opts)
        if command == "baseline-restore":
            return await cmd_baseline_restore(args, log, opts)
        candidate = await find_device(log, args.name, args.scan_timeout)
        probe = Probe(
            log,
            candidate.device,
            timeout=args.timeout,
            echo_notifications=command == "watch",
        )
        if command == "read-all":
            return await cmd_read_all(args, log, probe)
        if command == "watch":
            return await cmd_watch(args, log, probe)
        if command == "baseline-save":
            return await cmd_baseline_save(args, log, probe)
        raise ProbeError(f"unknown command {command}")
    except ProbeError as err:
        log.emit("refused", f"REFUSED: {err}", error=str(err))
        return 2
    except asyncio.CancelledError:
        log.emit("interrupted", "interrupted; disconnecting")
        return 130
    except Exception as err:
        # BleakError etc. Never dump a traceback: it could carry the device
        # address, while this message goes through redaction.
        log.emit("error", f"ERROR: {describe(err)}", error=describe(err))
        return 1
    finally:
        if probe is not None:
            await probe.close()
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(signal.SIGINT)
        for name in ("lumalou", "bleak"):
            logging.getLogger(name).removeHandler(handler)
        log.emit("end")
        log.close()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
