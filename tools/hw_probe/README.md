# Lumalou hardware probe

An explicit, opt-in CLI for collecting hardware evidence from a Fisher-Price
Lumalou (`gld09`). It uses the `lumalou` library in this repository directly,
without Home Assistant. It is a development tool. It is not part of the
published package, and nothing runs it automatically.

## Safety rules

- Run it only while the device owner is present.
- Every write goes through a named builder in `lumalou.commands` from a fixed
  allowlist. There are no raw opcodes. DFU/OTA/firmware, factory reset,
  pairing-complete and `SET_TIME_PRESCALER` are not reachable. The time
  prescaler is only ever read.
- `send` and `baseline restore` print the feature, the decoded arguments, the
  opcode and the payload hex, and they refuse to write without `--yes`.
  `--dry-run` prints the payload without using Bluetooth.
- Volume and brightness are capped at 3 (this includes clock brightness and
  routine volume). `--allow-high` lifts the caps. `play` and `soother on` are
  refused when the device's current volume is above the cap.
- Alarm value 0 means ACTIVE (an audible tone at the scheduled time), and 9
  means inactive. Alarm writes that contain 0 need `--allow-alarm`.
- Runtime actions need `--dangerous` in addition to `--yes`: `soother` (light
  and sound), `global-state` (the multi-field aggregate), `nap-start`,
  `routine-start` and `routine-control`. `baseline restore` never sends them.
- The tool never prints or logs the BLE address/UUID or the factory
  fingerprint. The device is shown as a 12-character salted hash. The salt is
  kept in `<out>/.salt`.
- Ctrl-C disconnects cleanly.

## macOS setup

- macOS only grants Bluetooth to a process whose app bundle declares
  `NSBluetoothAlwaysUsageDescription`. A terminal app that has been allowed
  under System Settings → Privacy & Security → Bluetooth usually passes its
  permission on to the Python it starts. When it does not (for example when
  the terminal is embedded in another tool, or CoreBluetooth aborts or scans
  find nothing), launch Python from a tiny wrapper app instead:

  ```text
  LumalouProbe.app/Contents/Info.plist   CFBundleIdentifier, CFBundleExecutable=run,
                                         CFBundlePackageType=APPL, LSUIElement=true,
                                         NSBluetoothAlwaysUsageDescription=<reason>
  LumalouProbe.app/Contents/MacOS/run    #!/bin/zsh script that cd's to the repo and
                                         runs tools/hw_probe/.venv/bin/python
                                         tools/hw_probe/probe.py <args> > <log> 2>&1
  ```

  Ad-hoc sign it (`codesign --force --sign - LumalouProbe.app`), start it with
  `open LumalouProbe.app` (arguments can be passed through a file the script
  reads), allow the Bluetooth prompt once, and read the log it writes. Output
  goes to the log because `open` does not attach a terminal.
- Only one BLE central can be connected. Home Assistant, the Fisher-Price app
  or a browser Web Bluetooth tab block the probe completely. Disable the
  Lumalou config entry in HA before you start, and re-enable it when you
  finish.

## Usage

Run these commands from the repository root. `uv` builds a small environment
from `tools/hw_probe/pyproject.toml` and installs the library from
`packages/python` in editable mode.

```bash
P="uv run --project tools/hw_probe python tools/hw_probe/probe.py"

$P scan                                   # matching advertisements, no connect
$P read-all                               # every named read + 7 day routines
                                          # (--include-unanswered adds the nap
                                          # alarm reads that 0.3.7 never answers)
$P watch --seconds 120                    # log notifications while pressing buttons
$P send --help                            # list the write allowlist
$P send light-brightness 2 --dry-run      # show the payload, no Bluetooth
$P send light-color night_light --yes     # one write, then read back and diff
$P baseline save probe-out/baseline.json
$P baseline restore probe-out/baseline.json --dry-run
$P baseline restore probe-out/baseline.json --yes
```

Common options: `--name <advertised name>` selects a device when there is more
than one match. `--out DIR` sets the log directory (the default is
`./probe-out`, which is git-ignored). `--timeout` sets the per-read timeout.
`--verbose` echoes library debug lines to stdout.

Device selection: the tool scans for 15 s. A device matches if it advertises
company ID 950 with manufacturer data that starts with `MB` (the same matcher
Home Assistant uses), or if its advertised name is all digits (the AP number).
It picks the strongest signal.

Every run writes `<out>/<command>-<timestamp>.jsonl`. The file contains every
read (raw args hex, decoded value or the reason it could not be decoded, and
timing), every notification, and the library's debug log (ignored and
rejected frames with hex).

## Notes

- The library answers each response type once per session. After a write,
  `send` waits 2 s while logging notifications. It then reads back in a new
  session, leaving a 1.5 s gap after the disconnect. `--repeat-state-read`
  records what happens when GLOBAL_STATE is requested a second time in the
  write session. It uses only the normal library API.
- `baseline restore` rebuilds every write from the saved raw bytes with the
  library's strict decoders. It covers the playlist, clock settings, r2r and
  sleepy times, r2r alarms and the seven day routines. Scalar settings are
  restored from GLOBAL_STATE, which carries the same values as their
  standalone single-byte responses. With `--include-inferred`, the tool
  rebuilds them from the saved GLOBAL_STATE fields: playlist and light
  duration, routine volume, routine music/rewards, r2r and routine status. The
  nap alarm and the alarm statuses are not restored. After restoring, the tool
  reads the blocks back in a new session and prints any mismatch.
- JSON inputs for `send`:
  - times: `{"days": ["07:00", null, ...]}` (seven entries, Sunday first)
  - alarms: `{"days": [9, 9, 9, 9, 9, 9, 9], "sound": 0}`
  - routine: `{"time": "19:30", "steps": [[3], [7, 1]]}`, or twelve raw
    `slots`

## Tests

```bash
cd tools/hw_probe && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```

The tests use a fake client and never touch Bluetooth.
