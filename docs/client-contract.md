# Strict Python client contract (unreleased)

This change is deliberately incompatible with unsafe 0.1.0 behavior. It adds
session-isolated reads and transport validation, not hardware-certified restore.
No device testing was performed for this change.

## Home Assistant connection boundary

Construct `LumalouClient(ble_device, client_factory=..., on_response=...,
disconnected_callback=...)`. `ble_device` can be the current `BLEDevice` supplied
by Home Assistant. The factory has the normal Bleak constructor shape:
`factory(device, disconnected_callback=callback)` and returns a transport with
async `connect`, `disconnect`, `start_notify`, `read_gatt_char`, and
`write_gatt_char` methods. This supports an HA-aware adapter without changing
global Bleak functions. The callback supplied to `disconnected_callback` is
synchronous and receives the `LumalouClient`, after state has been invalidated.

`connect(timeout=20)` creates a new transport, keys, nonce, sequence and
generation; it does not reuse a disconnected transport. It performs only the
existing session handshake and ENABLE_RX, never clock/configuration commands.
Passing a `BLEDevice` avoids Bleak's implicit address lookup. A string address
and the explicitly called standalone `scan()` helper remain for CLI callers;
HA must not use that helper. `connect` itself does not start an independent
scanner.

Connection/handshake failure, cancellation, explicit disconnect and unexpected
transport disconnect invalidate callbacks, clear cached observations and fail
the pending request. Old notification closures retain the old generation and
cannot complete a new session's request. Cleanup attempts a bounded five-second
disconnect; repeated caller cancellation does not abandon that attempt. A BLE
backend that refuses disconnect cannot be forced to release hardware; explicit
`disconnect()` reports cleanup failure rather than promising release.

No command retries or automatic reconnections are added. In particular, an
ambiguous Play or other transient command is never replayed.

## Fresh response envelopes

`await client.request(app_data, expected_opcode, timeout=3)` returns an immutable
`ResponseEnvelope(opcode, args, generation, received_at, sequence)`.
`received_at` is monotonic receive time, not wall-clock/device time. `args`
contains exact raw application bytes, without the opcode or frame overhead.
Only a valid response with the exact expected opcode completes a request. Other
known opcodes remain observable through `on_response` but do not complete it.
Commands and requests share a serialization lock; only one request is active.

`request_named(name)` covers the established named requests, including weekly
times, alarms, playlist and clock settings. `request_day_routine(day)` uses the
explicit seven-day response map, including Friday `90` and Saturday `91`.
`request_state()` remains a convenience returning a fresh decoded dict.
`state` is a detached last observation of the current live session only.
Unsolicited valid GLOBAL_STATE notifications update it and call `on_state`.

`ResponseEnvelope.decode()` returns the strict GLOBAL_STATE or typed schedule /
routine / task-status model when a layout is established. Playlist, clock and
other presently unimplemented payload decoders raise `UnsupportedResponseError`
instead of guessing fields. Their envelopes still preserve raw payloads for
further protocol work. Receiving all those raw blocks is not yet a verified
complete backup or restore API.

## No invented request correlation

The MPID RX sequence is not proven to echo the outgoing request sequence. It
is used only to suppress duplicate RX sequence numbers, never as a request ID.
The client retains up to 4096 sequence numbers in one session, then invalidates
the session instead of allowing unbounded growth or silently forgetting them.

Each expected response opcode may be requested only once per session, and only
if that opcode has not already been observed in the current session. This includes
unsolicited observations during handshake and valid wrong-opcode responses while
waiting for another block. Every validated observation consumes eligibility,
before invoking user callbacks. A new query then raises
`FreshSessionRequiredError` before sending anything; callers must reconnect and
obtain a new generation. The client also rechecks immediately before installing
its waiter and calling the transport write, covering notifications received while
the exchange coroutine is being scheduled. A delayed duplicate carrying a new RX
sequence cannot otherwise be distinguished from a response to the first explicit
query after an unsolicited observation. Different, previously unseen profile
blocks can be read sequentially within one session.

If firmware always pushes a required opcode during handshake, that opcode cannot
be actively queried under this conservative contract. The unsolicited envelope
remains a current-session observation, not a solicited verification result. Do
not implement an unbounded reconnect loop or relax the guard to conceal this
limitation; reliable correlation would require additional protocol evidence.

Timeout, malformed response or cancellation invalidates the entire session and
waits for cleanup. A late old-session response cannot complete the next request.
No cached-state fallback remains. Arrival in a current session proves a fresh
transport observation, not causal acknowledgement of a particular write. An
unsolicited same-type observation during a request cannot be distinguished by
this wire format; callers must compare the decoded values and must not infer a
write acknowledgement or full-profile verification merely from arrival.

## Errors and framing

| Error | Meaning |
| --- | --- |
| `RequestTimeoutError` | Response deadline expired; session invalidated |
| `DisconnectedError` | No usable session, interrupted connection or failed cleanup |
| `MalformedResponseError` | Invalid MPID/FE frame or known typed payload |
| `UnsupportedResponseError` | Unknown opcode/request or unavailable typed decoder |
| `FreshSessionRequiredError` | Repeated response opcode, sequence exhaustion or bounded receive history exhausted |

Transport backend exceptions are retained for connect/write failures. Cancellation
still raises `CancelledError` after cleanup, including when cleanup itself fails.
The common application-error base is `LumalouError`.

The frame format is source-backed by `docs/protocol.md`, `docs/reversing.md`, the
original builders and shared independent golden vectors: eight-byte MPID header,
declared encrypted-body length including CRC byte, header CRC-8 and decrypted
body CRC-8. An FE frame has an exact nonzero declared application length and
the documented XOR checksum. The only accepted SSI receive route is `01 50`,
present in both the independent `rxDecrypt` / `responseFrame` golden vectors and
their generator. Bare FE data, the transmit route `01 10`, and all other SSI
routes are rejected, not guessed from channel bytes. This is the supported
receive-route allowlist, not a claim that no other firmware route can ever exist.
New routes require source-backed vectors before support. Parsing never searches
arbitrary bytes for a later FE marker or accepts trailing/truncated bytes. Valid
non-FE events on the confirmed `01 50` route
(such as the existing `01 50 02 ...` golden vector) are ignored as non-application
events. No fragmentation or reassembly format has been invented; a truncated
notification fails validation and retires the session.

GLOBAL_STATE is exactly thirteen bytes (26 nibbles). Short payloads are no longer
zero-padded and long payloads are no longer truncated. The original 14-byte
positive vector, whose final `0A` was previously ignored, is retained as a
negative vector. The new positive sample explicitly contains its 13-byte prefix;
this is a synthetic fixture correction, not a changed hardware capture.

## Breaking changes and tests

- Cache fallback on timeout removed; missing data raises.
- A previously requested or observed response opcode requires a new session.
- Bare FE and SSI routes other than confirmed `01 50` are no longer accepted.
- Invalid GLOBAL_STATE / frame lengths and checksums are rejected.
- Old-session state is cleared on disconnect; `state` returns a copy.
- Factory receives the standard `disconnected_callback` keyword.
- Private `_on_rx` now binds a generation; consumers must use public callbacks.
- Raw undecoded envelope data is not presented as typed profile configuration.

Run Python tests with the asyncio pytest plugin. When using an environment that
also contains Home Assistant's autoloaded pytest plugin, isolate plugin loading:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=packages/python/src \
python -m pytest -p pytest_asyncio.plugin packages/python/tests
```

Tests use only fake BLE transports: disconnect, cancelled/failed handshake,
repeated cancellation, stale/late/duplicate/wrong-opcode frames, timeout without
cache fallback, malformed frames, reconnect and independent raw/typed blocks.
