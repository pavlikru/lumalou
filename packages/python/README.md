# lumalou (Python)

> **Fork notice.** `lumalou-gld09` is an independent fork of
> [stramanu/lumalou](https://github.com/stramanu/lumalou) by Emanuele Strazzullo,
> maintained at [pavlikru/lumalou](https://github.com/pavlikru/lumalou). It
> ships the changes proposed in
> [stramanu/lumalou#2](https://github.com/stramanu/lumalou/pull/2) (strict
> profile reads, typed schedule codecs and setters, authenticated device-key
> binding, injectable BLE client factory) until they are released upstream.
> The import package is still `lumalou`; do not install it alongside the
> upstream `lumalou` distribution. MIT licensed, original attribution preserved.

Where to report problems:

- Bugs in `lumalou-gld09`: <https://github.com/pavlikru/lumalou/issues>
- The Home Assistant integration: <https://github.com/pavlikru/ha-lumalou/issues>
- The original project (upstream `lumalou` packages, web app):
  [stramanu/lumalou](https://github.com/stramanu/lumalou/issues)
- Security problems: privately, see
  [SECURITY.md](https://github.com/pavlikru/lumalou/blob/gld09/SECURITY.md)

Local BLE control for the Fisher-Price Lumalou. Pure Python (async, [bleak](https://github.com/hbldh/bleak)).

```bash
pip install lumalou-gld09
```

## Library

```python
import asyncio
from lumalou import LumalouClient, Color

async def main():
    devices = await LumalouClient.scan()
    async with LumalouClient(devices[0]["address"]) as luma:
        await luma.light_color(Color.RAINBOW)
        await luma.play(0)            # guided sleep playlist
        await luma.volume(5)
        print(await luma.request_state())

asyncio.run(main())
```

`request_state()` returns a fresh, strictly validated `GLOBAL_STATE` snapshot;
it is not a complete device backup. For other blocks use `request_named()` or
`request_day_routine()`, which return a `ResponseEnvelope` bound to the current
session. The device pushes GLOBAL_STATE and other responses after commands and
on physical changes; pass `on_state` / `on_response` to receive them. As in
upstream, frames the client cannot use are ignored and never end the session.
See the repository's
[client contract](https://github.com/pavlikru/lumalou/blob/gld09/docs/client-contract.md) and
[schedule codec reference](https://github.com/pavlikru/lumalou/blob/gld09/docs/schedule-codecs.md).

## CLI

```bash
lumalou scan
lumalou light --color blue --brightness 7
lumalou sound --source ocean --volume 5
lumalou soother                 # --off to stop
lumalou time                    # sync clock
lumalou state                   # read a fresh GLOBAL_STATE snapshot
lumalou send 3c05               # decode raw app_data (opcode + args), send nothing
lumalou send --yes 3c05         # send it
```

Add `-a <address>` to target a device; otherwise it auto-scans.

`lumalou send` prints the decoded opcode and sends only with `--yes`. It always
refuses the spec's unsafe opcodes `0x52` (`SET_TIME_PRESCALER`) and `0x34`
(`SEND_PAIRING_COMPLETE`). Whole-device writes `0x01` (`SET_GLOBAL_STATE`) and
`0x03` (`SET_GLOBAL_ON`), and any opcode that is not in the protocol spec, also
need `--dangerous`. Prefer the typed subcommands; raw bytes can leave the
device in an unexpected state.

Requires Python 3.10+, a Bluetooth LE adapter, and radio range of the device.

## Development

```bash
pip install -e ".[dev]"
pytest        # runs the shared golden vectors (spec/vectors.json)
```

## Disclaimer

Independent reverse-engineering project for interoperability. Not affiliated
with or endorsed by Mattel or Fisher-Price. "Fisher-Price" and "Lumalou" are
trademarks of their respective owners. Use at your own risk.

## License

MIT. See the repository's
[LICENSE](https://github.com/pavlikru/lumalou/blob/gld09/LICENSE).
