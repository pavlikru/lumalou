# Changelog

All notable changes to the `lumalou-gld09` distribution (a fork of
[stramanu/lumalou](https://github.com/stramanu/lumalou)) are documented here.
The import package remains `lumalou`.

## 0.3.0 - 2026-09-25

### Behaviour aligned with upstream

Hardware testing on firmware 0.3.7 contradicted some of the strictness this
fork had added. The session behaviour now matches upstream `stramanu/lumalou`
there. The fork keeps what Home Assistant needs: `client_factory`, device
fingerprint binding, the schedule/profile codecs and the validated builders.

- An inbound frame never ends the session. Like upstream, the client ignores
  every frame it cannot use and logs it at debug level: MPID length/CRC
  failures, the bare `01 50` route header, truncated or checksum-failing FE
  frames, unknown opcodes, and known responses whose payload fails
  validation. 0.2.1 retired the session with `MalformedResponseError` instead;
  on hardware this happened after every `routine_control` 1, 2 and 3, which
  send a bare `01 50` frame.
- Only the pending request can fail, and only through its own response: a
  frame with the awaited opcode whose FE checksum or payload is invalid
  (`MalformedResponseError`), or a timeout (`RequestTimeoutError`). Both
  still retire the session. A malformed unsolicited frame is not delivered to
  callbacks and does not count as an observation of its type.
- The receive history no longer retires the session after 4096
  notifications (a push-driven session reached that within days). The client
  keeps the last 1024 sequence numbers (`client.RX_HISTORY`) and drops
  duplicates and frames at or below the oldest forgotten number.
- A request refused with `FreshSessionRequiredError` because its type was
  pushed while the request was being scheduled no longer disconnects;
  nothing was written. This now matches the refusal raised before scheduling.

Unchanged and still stricter than upstream: each response type can be
requested once per session, and a pushed observation consumes that
eligibility. Upstream has no such guard (and falls back to cached state on
timeout).

### Added

- Typed decoders for the single-value responses, based on firmware 0.3.7
  reads: `responses.SINGLE_VALUE_RESPONSES` (opcode to request name for
  `led_brightness`, `light_color`, `light_duration`, `volume`,
  `routine_volume`, `song_playing`, `playlist_duration`, `operation_mode`,
  `activity_state`, `current_stage`, `r2r_status`, `routine_mode_status`,
  `r2r_alarm_status`, `nap_current_status`, `transmission_mode`) and
  `parse_single_value` (exactly one byte to `int`). `ResponseEnvelope.decode()`
  returns the `int` for these instead of raising `UnsupportedResponseError`.
- `responses.parse_routine_music_status`: `ROUTINE_MUSIC_STATUS` (0x93) uses
  the SET layout and decodes to `RoutineMusicSettings`.
- `RoutineTaskState` (0 pending, 1 current, 2 done) and
  `RoutineTaskStatus.task_state(task_id)`, `.states_by_task_id` and
  `.current_task_id`. Hardware confirmed that nibble *i* is task id *i* + 1.
- `commands.UNANSWERED_REQUESTS` (`nap_alarm_status`, `nap_alarm`): firmware
  0.3.7 never answers them. The builders remain.
- `protocol.parse_response_frame` reports error `"empty"` for the bare
  `01 50` header, and it includes the unauthenticated `opcode` with a
  `"checksum"` error.
- `tools/hw_probe`: an opt-in hardware probe CLI (not published). It is
  documented with the macOS Bluetooth permission caveat. `read-all` skips the
  unanswered requests unless you pass `--include-unanswered`.

### Changed

- Documented hardware behaviour: routine music and both reward sounds are
  0/1 on the device. The builders still accept the full byte and nibbles. The
  routine volume is 0-9.
- `play_audio` (0-7), `start_nap` (0-11), `routine_control` (0-4) and
  `set_current_date` (hour 0-23, minute and second 0-59, weekday 0-6) reject
  out-of-range values with `ValueError` instead of sending them.
  `set_global_on` requires a boolean.
- `lumalou send` refuses the spec's unsafe opcodes (`SET_TIME_PRESCALER`
  0x52, `SEND_PAIRING_COMPLETE` 0x34), as the web client does.
- Documented that `SET_LIGHT_COLOR` is the web client's light-on action and
  `SET_GLOBAL_ON` starts the soother (light and sound).

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
