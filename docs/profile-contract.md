# Profile contract: source-backed pieces and explicit gaps

This is an **unreleased, hardware-unverified contract**, not a complete profile,
backup, import or safe restoration implementation. No device was contacted.
The fresh Python response envelope still refuses to decode layouts without
evidence; a well-framed response is not necessarily an understood profile field.

## Evidence and attribution

Source: the deployed [Lumalou web bundle](https://lumalou.emanuelestrazzullo.dev/assets/index-CP__DzxD.js),
SHA-256 `30bef51fe4ed6728ccd4a811b7f855368cc587d804a578660da368c2ece70b09`.
It was independently downloaded and its hash verified for this extension.
Source commit is unknown. The implementations adapt the encoding rules of
Emanuele Strazzullo's MIT-licensed Lumalou project; the root MIT license remains
unchanged. Literal vectors in `spec/profile-vectors.json` are hand-written
synthetic examples, **not hardware captures**.

Audited symbols in that exact artifact:

- `_h`: validate song IDs 0–18, filter zero, truncate to twelve, pad with zero.
- `xh`: clock SET arguments are `[display_on, brightness << 4 | format]`;
  display is 0/1, brightness 0–9, format 0/1. The first high nibble is zero.
- `Ih`: routine music/reward SET arguments are
  `[music_byte, task_reward << 4 | routine_reward]`; accepted wire ranges are
  music 0–255 and each reward 0–15. These do not prove song IDs or boolean enums.
- `hh`: 13-byte GLOBAL_STATE nibble mapping, including clock and rewards.
  The original pads short inputs. Both package parsers now demand exactly
  thirteen bytes and retain all raw field values, including unknown enums.
- `td`, `Ra`, `yh`, `gh`: routine, weekly-time, alarm, task-status codecs.
- `te`, `ph`, `Oa`: command, response and named-query tables.
- `_onRx` plus the application's response handler: only GLOBAL_STATE and the
  schedule/task blocks receive application decoding. Other reply arguments are
  logged, not interpreted. SET shape does **not** establish response shape.

## Strict SET representations

`lumalou.profile` (Python) and `profile.ts` (JavaScript) provide:

| Representation | SET command | Guarantee / limit |
| --- | --- | --- |
| `MusicPlaylist` | 40 | Exactly twelve slots, IDs 0–18; order, duplicates and zero positions retained. `from_songs` / `playlistFromSongs` filters zero for explicit edits, rejects more than twelve nonzero IDs instead of silently truncating. Interior zero acceptance by hardware is unverified. |
| `ClockSettings` | 79 | Strict boolean display, brightness 0–9, format 0/1. No masking/coercion; reserved high nibble must be zero. |
| `RoutineMusicSettings` | 69 | Full music byte and reward nibbles retained; no narrowing to boolean or guessed sound enum. All three fields must be explicit. |
| Weekly times, weekly alarms, daily routines | 46 / 48 / 4A / day SET | Existing Python codecs now have JavaScript counterparts and shared literal vectors. |

`decode_*_set` / `decode*Set` names deliberately identify **SET arguments**.
They must not be used to parse similarly named responses. The command builders
use these models, and known scalar builders reject overflow, fractional values,
booleans where integers are required, and string coercion. Source-backed normal
volume/brightness ranges are 0–9, colour 0–9, light duration 0–5 and playlist
duration 0–6. Routine volume has only an established byte representation:
0–255 prevents wrapping but is **not** a validated hardware range. Nap alarm
uses the upstream `Alarm` enum 0–10; persistence is not established.

`OpaqueBlock(data, length)` is an explicit, exact-length, byte-preserving fallback
for unknown/reserved encodings. The caller must supply an independently
established length. It does not discover or register a response schema, cannot
be passed to a typed command builder, and carries no field semantics. Python
requires immutable bytes; JavaScript copies both input and returned bytes.
Unknown daily-task encodings and clock reserved bits must not be normalized
into supported values. Preserve the response envelope's original bytes, or an
opaque block when its length is known, and keep the profile unsupported.

Task-status encoding only serializes its seven runtime bytes for round trips.
It is **not** a setter: opcode 68 is REQUEST_ROUTINE_TASK_STATUS.

## Every read contract and remaining ambiguity

All 28 named requests remain in `spec/read-requests.json`; the seven individual
day requests remain in `spec/schedule-vectors.json`.

| Requests | Response payload contract |
| --- | --- |
| `global_state` | Exactly 13 bytes, 25 named raw integer fields. Unknown enum values retained, not validated as safe writable settings. |
| `r2r_times`, `sleepy_times` | Exactly 14 bytes, seven Sunday-first BCD time pairs or FF FF. Midnight and no time remain distinct. |
| `r2r_alarms` | Exactly 4 bytes, seven alarm nibbles and uninterpreted sound nibble. |
| Seven `request_day_routine` days | Exactly 14 bytes; preserve original slot positions, zeros, step numbers and task order. Friday/Saturday responses are 90/91, not contiguous with Sunday–Thursday 2B–2F. |
| `routine_task_status` | Exactly 7 bytes: current-step byte and twelve raw state nibbles. State meanings/sentinels unproven; runtime-only. |
| `music_playlist`, `clock_settings`, `routine_music_status` | No standalone response length or field layout established. SET lengths cannot be substituted. Clock/reward fields are available only through GLOBAL_STATE's mapping. |
| `current_date`, `toyic_fw_version` | No response lengths or field encodings established. Current-date SET's four BCD bytes do not establish the reply format. No firmware string/endianness/zero-termination assumptions. |
| `led_brightness`, `light_color`, `light_duration`, `volume`, `routine_volume`, `song_playing`, `playlist_duration` | Named response identities only; no dedicated response payload schema in the pinned bundle. Related GLOBAL_STATE fields do not establish standalone reply lengths. |
| `operation_mode`, `activity_state`, `current_stage`, `transmission_mode`, `time_prescaler` | Named response identities only. Prescaler SET remains prohibited. |
| `routine_mode_status`, `r2r_status`, `r2r_alarm_status`, `nap_current_status`, `nap_alarm_status`, `nap_alarm` | Named response identities only; scalar/boolean length and value interpretation must not be invented. |

Consequently there is **no complete fresh typed profile** yet. Playlist content
readback is missing even though playlist SET is implemented. A collection of
raw, well-framed replies must not be called a semantically validated snapshot.
The Python client still checks generation and response identity and never
substitutes cached values. Existing conservative same-opcode freshness limits
continue to apply; this extension adds no transaction-correlation claims.

JavaScript has codec parity for profile SET and schedule blocks, **not** Python
transport/session-validation parity. Its legacy browser client is not a safe
restoration coordinator. This extension does not audit or repair all JS framing.

## UI labels are not firmware semantics

The pinned English/Italian `taskNames` arrays associate IDs by array position:
0 `—`, 1 Get dressed, 2 Wash up, 3 Brush teeth, 4 Bathroom, 5 Backpack, 6 Meal,
7 Story, 8 Tidy up, 9 Heart, 10 Swirl, 11 Star. The selector excludes zero even
though the wire builder accepts task ID zero inside a nonzero step. This does
not justify treating task zero as an empty slot: `10` is not padding `00`.
These labels remain documentation, not new firmware enums.

The `Dh` song-name array matches existing `Song` IDs 0–18: no song, Sleep Baby
Sleep, Hour Glass, Frère Jacques, How Lovely the Evening, Tárrega Lágrima,
What's the Matter Dear, Suo Gân, Water Color Dreams, Dance of the Jellyfish,
Inside the Bubble, Paper Kites, Crickets in Space, Pink Noise, Ocean, Rain,
Brown Noise, Nature, Highway. Audio-source IDs are a separate 0–7 enum.

Rewards UI writes both reward nibbles as 0/1, labels them "Sound on each task"
and "Sound at the end", and displays any nonzero value as on. No named
reward-sound IDs were found. Neither that UI policy nor the accepted 0–15 wire
range proves hardware behavior for other values.

## Missing write/lifetime proofs and safety boundary

- Nap alarm 4F has a named constant and upstream byte builder, but no call site
  or codec in the pinned web bundle. Persistent versus current-Nap lifetime is
  unknown. Do not silently include it in an automatic persistent restore plan.
- No dedicated ready-to-rise alarm-status setter exists in the audited tables.
  Upstream's SET_GLOBAL_STATE aggregate can write its status nibble, while 4A
  writes the weekly alarm block. Whether the global alarm status is independent
  or derived from weekly alarms is unproven. Do not add a guessed opcode or
  automatically replay the aggregate to bridge this gap.
- `set_r2r_status` and `set_routine_status` exist, but persistence, activation
  effects and safe ordering relative to schedule/reward writes need hardware
  evidence. No SET routine-task status or firmware command is added.
- Current time must be computed afresh by the host; it is not a saved profile
  timestamp. Play/off, Nap start, routine start/control and global on/aggregate
  remain separate runtime actions, not automatically restorable profile blocks.
- There were no device, DFU/OTA, SET_TIME_PRESCALER, pairing-complete, release,
  tag or push operations. No versions changed.

Hardware acceptance still needs response formats for all raw blocks (especially
playlist, firmware and independent settings), nonzero and disabled schedule
vectors, numeric limits, unknown/reserved values, exact model/firmware identity,
field persistence across power loss, setter side effects, request correlation,
write acknowledgement/readback, write ordering and independent weekly-day
execution. Source tests cannot establish these properties.

## Executed checks

Python 3.10, 3.11 and 3.12: **360 tests passed** on each interpreter. JavaScript:
typecheck, **20 tests**, and production/declaration builds passed. Test loops
exhaust all 65,536 clock SET pairs (exactly forty supported encodings), all
65,536 music/reward SET pairs, all BCD time pairs, all task bytes in each routine
slot, all playlist bytes in each slot, and every runtime task-status byte in
each position. Shared literal vectors cover all seven day identities.

Code generation ran with no generated diff. Scoped Python E/F lint (excluding
the repository's pre-existing lambda/line-length style), import sorting,
format checks and `git diff --check` passed. No hardware acceptance is implied.
