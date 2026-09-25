# Changelog

All notable changes to the `lumalou-gld09` distribution (a fork of
[stramanu/lumalou](https://github.com/stramanu/lumalou)) are documented here.
The import package remains `lumalou`.

## 0.2.0 - 2026-09-25

First release of the fork. Upstream base: `stramanu/lumalou` `main` at
`9fa5ecf` (`lumalou` 0.1.0 on PyPI). Contains the changes proposed in
[stramanu/lumalou#2](https://github.com/stramanu/lumalou/pull/2) (head `e030bfd`).

### Added

- `LumalouClient(client_factory=...)`: inject the BLE client constructor
  (for example Home Assistant's `establish_connection` wrapper) and an optional
  `disconnected_callback`.
- Authenticated device-key binding: `parse_factory_device_fingerprint()`
  verifies the signed FACTORY token and returns a SHA-256 fingerprint;
  `LumalouClient(expected_device_fingerprint=...)` refuses sessions with a
  different device; `LumalouClient.device_fingerprint` exposes it per session.
- Strict reads: `request()`, `request_named()`, `request_day_routine()` return a
  session-bound `ResponseEnvelope` with `decode()`; a response type is read at
  most once per session (`FreshSessionRequiredError` otherwise).
- Typed codecs: `WeeklyTimes`, `WeeklyAlarms`, `DailyRoutine`, `RoutineTask`,
  `ClockTime`, `RoutineTaskStatus`, `MusicPlaylist`, `ClockSettings`,
  `RoutineMusicSettings`, `OpaqueBlock`, `CurrentDate`.
- Command builders in `lumalou.commands` for R2R times, sleepy times, R2R
  alarms, day routines and routine music settings; `set_clock_settings` now
  validates through `ClockSettings`.
- Passive advertisement decoding (`parse_advertisement`).
- Exceptions: `FactoryIdentityError`, `FactoryIdentityMismatchError`,
  `MalformedResponseError`, `UnsupportedResponseError`, `RequestTimeoutError`,
  `DisconnectedError`, `FreshSessionRequiredError`.

### Changed

- The client now authenticates the FACTORY token before any session write and
  rejects unknown or malformed responses instead of guessing.
- Distribution renamed to `lumalou-gld09`; version 0.2.0.

### Security

- No DFU/OTA or firmware update commands; the Nordic DFU service is never
  accessed.
