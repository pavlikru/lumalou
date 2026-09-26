"""Command-line interface.  `lumalou --help`"""

from __future__ import annotations

import argparse
import asyncio
import json

from ._generated import COMMANDS, DAY_ROUTINE, Color
from .client import LumalouClient

_COLORS = {c.name.lower(): int(c) for c in Color}
_AUDIO = {
    "playlist": 0,
    "custom": 1,
    "pink": 2,
    "ocean": 3,
    "rain": 4,
    "brown": 5,
    "nature": 6,
    "highway": 7,
}


# spec/protocol.json "unsafeCommands"; the deployed web client refuses them too.
# `lumalou send` never sends these, not even with --dangerous.
_UNSAFE_OPCODES = frozenset(
    {COMMANDS["SET_TIME_PRESCALER"], COMMANDS["SEND_PAIRING_COMPLETE"]}
)

# Whole-device writes: SET_GLOBAL_STATE overwrites every setting at once and
# SET_GLOBAL_ON switches the whole device (`lumalou soother` is the typed way).
# `lumalou send` needs --dangerous for these.
_DANGEROUS_OPCODES = frozenset(
    {COMMANDS["SET_GLOBAL_STATE"], COMMANDS["SET_GLOBAL_ON"]}
)

_OPCODE_NAMES = {code: name for name, code in COMMANDS.items()}
for _kind, _days in DAY_ROUTINE.items():
    for _day, _code in _days.items():
        _OPCODE_NAMES[_code] = f"{_kind}_{_day.upper()}_ROUTINE"


def _opcode_name(opcode: int) -> str:
    return _OPCODE_NAMES.get(opcode, "UNKNOWN")


def _raw_command(text: str, *, dangerous: bool = False) -> bytes:
    """Parse and vet `lumalou send` input before any Bluetooth I/O.

    Opcodes outside spec/protocol.json (firmware/OTA commands are not in it)
    and whole-device writes are refused unless ``dangerous`` is true. The
    spec's unsafe opcodes are always refused.
    """
    try:
        data = bytes.fromhex(text)
    except ValueError:
        raise SystemExit(f"not a hex string: {text!r}") from None
    if not data:
        raise SystemExit("nothing to send")
    opcode = data[0]
    if opcode in _UNSAFE_OPCODES:
        raise SystemExit(
            f"refusing unsafe opcode 0x{opcode:02x} ({_opcode_name(opcode)})"
        )
    if not dangerous:
        if opcode in _DANGEROUS_OPCODES:
            raise SystemExit(
                f"opcode 0x{opcode:02x} ({_opcode_name(opcode)}) is a whole-device "
                "write; pass --dangerous to send it anyway"
            )
        if opcode not in _OPCODE_NAMES:
            raise SystemExit(
                f"opcode 0x{opcode:02x} is not in the protocol spec; "
                "pass --dangerous to send it anyway"
            )
    return data


def _describe_raw(data: bytes) -> str:
    args = data[1:].hex() or "(none)"
    return f"opcode 0x{data[0]:02x} {_opcode_name(data[0])}, args {args}"


def _color_id(v):
    return _COLORS[v.lower()] if v.lower() in _COLORS else int(v)


def _audio_id(v):
    return _AUDIO[v.lower()] if v.lower() in _AUDIO else int(v)


async def _resolve_address(addr):
    if addr:
        return addr
    devices = await LumalouClient.scan(timeout=8.0)
    if not devices:
        raise SystemExit("No device found. Turn the Lumalou on and try again.")
    print(
        f"[auto] using {devices[0]['name']} ({devices[0]['address']}, {devices[0]['rssi']} dBm)"
    )
    return devices[0]["address"]


async def _run(args):
    if args.cmd == "scan":
        for d in await LumalouClient.scan(timeout=args.timeout):
            print(
                f"  {d['rssi']:>4} dBm  {d['name'] or '(no name)':<20} {d['address']}  mfg={d['manufacturer']}"
            )
        return

    raw = None
    if args.cmd == "send":
        raw = _raw_command(args.hex, dangerous=args.dangerous)
        print(_describe_raw(raw))
        if not args.yes:
            raise SystemExit("not sent: add --yes to send these bytes")
    address = await _resolve_address(args.address)
    async with LumalouClient(address) as luma:
        if args.cmd == "state":
            print(json.dumps(await luma.request_state(), indent=2))
        elif args.cmd == "light":
            if args.color is not None:
                await luma.light_color(_color_id(args.color))
            if args.brightness is not None:
                await luma.brightness(args.brightness)
            if args.off:
                await luma.light_off()
        elif args.cmd == "sound":
            if args.source is not None:
                await luma.play(_audio_id(args.source))
            if args.volume is not None:
                await luma.volume(args.volume)
            if args.off:
                await luma.audio_off()
        elif args.cmd == "soother":
            await luma.soother(not args.off)
        elif args.cmd == "nap":
            await luma.nap(args.minutes)
        elif args.cmd == "time":
            await luma.sync_time()
            print("clock synced")
        elif args.cmd == "send":
            await luma.send(raw)


def build_parser():
    p = argparse.ArgumentParser(
        prog="lumalou", description="BLE control for the Fisher-Price Lumalou."
    )
    p.add_argument("-a", "--address", help="device address/UUID (default: auto-scan)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="find Lumalou devices")
    s.add_argument("--timeout", type=float, default=8.0)

    sub.add_parser("state", help="read a fresh GLOBAL_STATE snapshot")

    li = sub.add_parser("light", help="control the light")
    li.add_argument(
        "-c",
        "--color",
        help=(
            "warm|red|yellow|orange|green|blue|purple|night_light|cool|rainbow "
            "or 0-9 (switches the light on)"
        ),
    )
    li.add_argument(
        "-b", "--brightness", type=int, help="0-9 (does not switch the light on)"
    )
    li.add_argument("--off", action="store_true", help="turn the light off")

    au = sub.add_parser("sound", help="control audio")
    au.add_argument(
        "-s",
        "--source",
        help="playlist|custom|pink|ocean|rain|brown|nature|highway or 0-7",
    )
    au.add_argument("-v", "--volume", type=int, help="0-9")
    au.add_argument("--off", action="store_true", help="stop audio")

    so = sub.add_parser("soother", help="start/stop the soother (light and sound)")
    so.add_argument("--off", action="store_true")

    n = sub.add_parser("nap", help="start a nap")
    n.add_argument("minutes", type=int, help="duration id (1=15, 2=30, ... 10=180)")

    sub.add_parser("time", help="sync the device clock")

    se = sub.add_parser(
        "send",
        help="send raw app_data in hex (needs --yes)",
        description=(
            "Send raw app_data (opcode + args). Prints the decoded opcode and "
            "sends only with --yes. Unsafe opcodes (0x52, 0x34) are always "
            "refused; whole-device writes (0x01, 0x03) and opcodes outside "
            "the protocol spec also need --dangerous."
        ),
    )
    se.add_argument("hex", help="e.g. 3c05 = blue light")
    se.add_argument("-y", "--yes", action="store_true", help="actually send the bytes")
    se.add_argument(
        "--dangerous",
        action="store_true",
        help="allow whole-device writes and opcodes outside the spec",
    )
    return p


def main(argv=None):
    try:
        asyncio.run(_run(build_parser().parse_args(argv)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
