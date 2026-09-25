# Changelog

All notable changes to the `lumalou-gld09` distribution (a fork of
[stramanu/lumalou](https://github.com/stramanu/lumalou)) are documented here.
The import package remains `lumalou`.

## 0.2.1 - 2026-09-25

### Fixed

- A write other than a one-byte query, a two-byte setter or the playlist (for
  example `SET_CURRENT_DATE`) no longer retires the session with
  `MalformedResponseError("unsupported SSI route or invalid FE
  length/checksum")`. The device acknowledges every write with
  `00 7f 01 NN 00 00 00 00 00` and `01 10 NN 00`, where `NN` is the written
  MPID plaintext / FE-frame length; both shapes are now recognised for any
  length instead of an exact list of observed values.
- CRC-valid notifications on SSI routes other than `01 50`, and FE frames with
  an opcode outside the response table, are ignored instead of retiring the
  session. They still never complete a request.

### Changed

- Ignored and rejected inbound frames are logged at debug level (`lumalou.client`)
  with their decrypted plaintext in hex. Frames carry device state and
  commands only, never key material or the factory token.
- Malformed FE frames on the `01 50` route now raise
  `MalformedResponseError("invalid FE length or checksum on the application
  route")`; they still retire the session, as do MPID CRC or length failures.

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
