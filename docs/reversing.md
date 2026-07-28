# Reversing the Lumalou

How this project went from a discontinued app to a working, byte-exact reimplementation of the
Fisher-Price Lumalou (`gld09`) Bluetooth LE protocol. This is the *process* write-up — the
narrative, the tools, and the dead ends. For the finished protocol spec see
[`protocol.md`](protocol.md); the machine-readable form is [`spec/protocol.json`](../spec/protocol.json).

Everything here follows one rule: **every claim is backed by a symbol in the decompiled app or a
packet in a live capture.** Where something was a guess, it says so.

## Why

The *Fisher-Price Smart Connect* app was removed from Google Play in March 2025 and its backend
went dark in August 2025. The Lumalou itself is a perfectly good device that speaks a **local**
BLE protocol — no cloud is involved in day-to-day control. So the whole thing was recoverable
without any server, provided the on-air protocol could be understood. This is a
personal interoperability project (EU Directive 2009/24/EC, art. 6), not a redistribution of
anyone's code.

## Tooling

| Job | Tool |
|---|---|
| Unpack APKs | `unzip` / `apktool` |
| Decompile Java/Kotlin | `jadx` |
| Reverse the native crypto lib | Ghidra, plus **Unicorn** (ARM64) to run the library's own exported functions |
| Live GATT + control | Python with [`bleak`](https://github.com/hbldh/bleak) and [`cryptography`](https://cryptography.io) |
| Capture on-air traffic | Android **btsnoop HCI** log → Wireshark |

The choice to reimplement in plain Python (rather than ship the native `.so`) came late, once the
key derivation was understood well enough to rewrite cleanly.

---

## 1. Triage — which APK to attack

Four versions of the app were on hand. They are not equivalent targets:

| Version | Date | Framework | `assets/gld09.json` | Native MPID lib |
|---|---|---|---|---|
| **8.6.4** | Dec 2020 | **native (Java/Kotlin)** | ✅ present | ✅ |
| 9.0.1 | Mar 2023 | Flutter (AOT) | ❌ | ✅ |
| 9.1.0 | Nov 2023 | Flutter (AOT) | ❌ | ✅ |
| 10.0.0 | Jan 2024 | Flutter (AOT) | ❌ | ✅ |

Two facts decided the target:

1. **8.6.4 is the only native build.** From 9.0.1 on the app is Flutter with an AOT-compiled
   `libapp.so` (~14 MB Dart snapshot) — expensive to read statically. 8.6.4 is four `classes*.dex`
   files that `jadx` opens cleanly.
2. **8.6.4 is the only build that still bundles the product config** (`assets/gld09.json`). From
   9.x the per-device config was fetched at runtime from the (now-dead) backend.

The clincher: `lib/arm64-v8a/libnative-lib.so` — the MPID crypto library — is **byte-identical
across all four versions**. Whatever the crypto turned out to be in 8.6.4 would be valid for the
2024 device, because the on-air protocol is versioned on the device side (`apiLevel: 1`) and 8.6.4
already supports it. So: **do all static work on 8.6.4**, keep the Flutter build only as a
cross-reference.

## 2. Mapping the GATT surface

`assets/gld09.json` describes the device in the clear — product code `gld09`, `apiLevel 1`,
`isMagicDevice: true`, and a custom GATT service. Five UUIDs, all off one base:

```
service  4cea0001-c678-4202-b5d3-712dbb5e5b14
tx       4cea0002   app → device   (write)          encrypted command frames
rx       4cea0003   device → app   (notify)         encrypted state / responses
factory  4cea0004   device → app   (read)           signed manufacturing token
session  4cea0005   app → device   (write)          app public key + salt
```

These were confirmed three ways: in the config JSON, in the bytecode
(`strings classes*.dex | grep 4cea000`), and finally against the real device with a `bleak`
service dump.

A crucial thing surfaced only in the **live** dump: a second service,
`00001530-1212-efde-1523-785feabcd123` — the **Nordic DFU (firmware update)** service. Writing to
its control point or packet characteristics can brick the device. It went straight onto a
permanent **blacklist**: the library never touches `0000153x-…` and never emits an OTA opcode.

The advertisement is worth noting for discovery: the device advertises under its numeric AP number
(e.g. `1102184718`), **not** the string "Lumalou", and does not advertise the service UUID.
Manufacturer data (company `950` = Mattel) carries `"MB"` plus an ASCII firmware version
(`0.3.7`).

Interestingly, the Lumalou's `commandTypes`, `presets`, and `encryptionMethods` are all **empty**
in `gld09.json` — unlike older devices, whose opcodes are listed right in the JSON. That was the
first hint that everything interesting for this device lives in **code**, not data.

## 3. Following the code

Inside `com.mcpp.mattel.*` (MPID = "Mattel Product ID", the magic-device BLE stack), the pieces
fell out in order:

- **`MpidPeripheral`** — the characteristic roles. The session bootstrap reads the token from
  `factory`, writes app material to `session`, sends commands on `tx`, and subscribes to `rx`.
- **`MpidService`** — the crypto boundary: `mpidEncryptData` on the way to `tx`, decrypt on the way
  in from `rx`. The JNI entry points (`Java_…_MpidService_mpidEncrypt*Internal`) hand off to the
  native library.
- **`ManufacturingKey`** — a **hardcoded manufacturing public key** (keyID 12). This is what the
  app uses to verify the device's token. Its presence is the proof that verification is **local** —
  there is no server in the trust path, which is what made an offline reimplementation possible at
  all.
- **`libnative-lib.so`** (`com.mcpp.mattel.mpidlibrary`) — the actual crypto: symbols
  `mpid_AES_128_CTR`, `AES_CTR_xcrypt_buffer`, `crc8_calc`, `crc8_table`. So: **AES-128-CTR** for
  confidentiality and **CRC-8** for integrity, both done natively.

At this point the shape was clear (ECDH-ish handshake, AES-CTR frames, CRC-8) but two things were
still opaque: the exact **key derivation**, and the **frame layout**.

## 4. The handshake — deriving the session key

The token read from `factory` decomposes as:

```
[25:58]  device compressed P-256 public key (33 B)
[...]    a config table describing device attributes
[-64:]   ECDSA P-256 signature (Mattel)
[-4:]    a 4-byte device salt
```

The handshake is a local ECDH:

1. Read the token from `factory`; verify its signature against the hardcoded manufacturing key.
2. Generate an ephemeral P-256 keypair. Compute `shared = ECDH(app_priv, device_pub)` — the 32-byte
   X coordinate.
3. **Stretch it into a session key.** This was the hardest single fact to pin down, and it lives in
   the native lib, not the Java: **100 rounds** of AES-128-CTR, each round re-encrypting the 32-byte
   secret in place. Per round the **key is the first 16 bytes of the current secret**, and the
   **IV/counter block** is the fixed pattern `00×7 | round | 00 | "mattel" | 00` — seven zero bytes,
   the round index `0…99`, a zero, the ASCII `6d 61 74 74 65 6c`, and a final zero. After 100 rounds
   the first 16 bytes of the result are the AES-128 data-channel key. Note the `"mattel"` bytes live
   in the **IV**, not the key; at round 0 that IV reads `00×9 | "mattel" | 00`. (This is the same
   derivation written from the wire's point of view in [`protocol.md`](protocol.md).)
4. Write `app_pubkey_compressed(33) || app_nonce(4)` to `session`. The device runs the same ECDH and
   the same stretch, and both sides now hold the same key.

The round count and the `"mattel"` IV came out of reversing `mpid_AES_128_CTR` and
`mpidEncryptInternal` in Ghidra. Reading AArch64 by hand is error-prone, so the fastest route to
*certainty* was to **run the library** rather than trust the disassembly: map `libnative-lib.so`
into a **Unicorn** ARM64 instance, apply its relocations, stub the handful of libc calls it needs
(`malloc`, `memcpy`, `__read_chk` → `/dev/urandom`, `__stack_chk_fail`, …), set up a TLS block so the
stack-canary read at `TPIDR_EL0+0x28` doesn't fault, run `.init_array` (the C++ static
constructors), and then call the exported functions directly.

The worked example is `crc8_calc`: feed it `"123456789"` and it returns the same byte as a plain
Python CRC computed over the 256-entry table lifted from the library's own memory — a self-contained
check that the harness really is executing the native code. The AES-CTR stretch was pinned the same
way: emulate the native routine, diff its output against a candidate Python implementation, and
iterate until every byte matched. The whole handshake — token signature verification included — was
then run end-to-end in the emulator before a single byte was rewritten by hand. The decisive
confirmation came later on hardware: the clean-room key derived in Python **decrypts real `rx`
notifications with a valid CRC-8**, which is only possible if the derivation matches the device's
exactly.

## 5. Frame format

A command is built in three layers and then MPID-enveloped:

```
app command   [opcode] + args
FE-frame      0xFE | len | app_command | XOR(len ^ bytes)
SSI0 wrap     0x01 0x10 | FE-frame                       (routes BLE → the toy's IC)
MPID frame    0x7E | seq(4 BE) | len+1(2 BE) | crc8(header) | AES-128-CTR(body + crc8)
```

Details that only fell into place with the emulator and then a live capture:

- **CRC-8** is poly `0x07`, init `0xFF`.
- **AES-128-CTR IV**, TX: `seq(4) || app_nonce(4) || device_salt(4) || 00000000`.
- **IV, RX** is the mirror image: `seq(4) || device_salt(4) || app_nonce(4) || 00000000`. The salt
  and nonce swap places by direction — a detail that is invisible in the Java and only obvious once
  you try to decrypt a real notification and it comes out as garbage until you flip them.
- A separate transport command, raw `01 50 01` (`ENABLE_RX`), is needed after the handshake before
  the device will stream data responses on `rx`.

## 6. The command set

With the envelope solved, the opcodes came from **`FPLumaModel`** (plus `FPMBEnums` and the binary
helpers in `FPBinaryManipulationKt`), read line by line. An application command is just
`[opcode] + args`; the model class enumerates every one — light colour (`0x3C`), brightness
(`0x3A`), audio (`0x3F`), playlist (`0x40`), timers, nap and weekly schedules, clock, and the
read-back requests.

Two encoding helpers matter for arguments:

- **BCD** for clock values: `bcd(n) = (n//10)<<4 | n%10`; a `null` field is `0xFF`.
- **nibble-pack** for compact multi-field commands: values are packed two-per-byte
  (`(a<<4) | (b & 0x0F)`), with `0x0F` meaning "leave unchanged".

The device's whole state is read back as **`GLOBAL_STATE`** (response `0x02`): a 26-nibble
snapshot. That nibble layout, incidentally, is why light colour is a **preset index and never an
RGB value** — the device stores its current colour in a single 4-bit field, so it has no way to
represent an arbitrary colour.

## 7. Validating without bricking anything

The order of operations here was deliberately conservative:

1. **Emulate, then reimplement.** Run the native functions to get a bit-exact reference, rewrite the
   handshake, stretch, framing, and CRC in plain Python, and check them against **golden vectors**
   (input → expected bytes). This is now the cross-language contract in
   [`spec/vectors.json`](../spec/vectors.json): every binding must reproduce the same bytes.
2. **Read before write.** On the real device, the first thing attempted was the handshake plus a
   *read* of `GLOBAL_STATE` — no state change. When a `tx` frame produced an `rx` notification that
   **decrypted with a valid CRC-8**, that was the proof the derived session key matched the device's.
3. **First write, chosen to be trivially reversible.** The very first write was a light-colour
   command. The cloud turned blue. Nothing about that can harm the device, and it confirmed the
   whole stack end to end.

Only after that did anything with side effects (timers, schedules, playlist writes) get exercised.

## 8. Capturing live traffic

Static analysis says what the app *can* send; a capture says what it *did* send. Enabling **btsnoop
HCI logging** on Android and driving the official app while it still worked produced ground-truth
packets, opened in Wireshark. These were used to sanity-check the frame layout, confirm the
salt/nonce IV ordering by direction, and verify sequence-number handling — the kind of thing that is
ambiguous in code but unambiguous in a real exchange.

## 9. Dead ends and lessons

- **The Flutter builds (9.x/10) are a trap for static analysis** — an AOT Dart snapshot, not
  readable like DEX. Because the native crypto lib is identical across versions, they were never
  needed; the 2020 native build carried everything.
- **`gld09.json` looked promising and mostly wasn't.** It gave the GATT map, but its opcode/preset
  tables are empty for this device — the older devices in the same app *do* list opcodes in JSON,
  which briefly suggested the Lumalou's would too. They live in code instead.
- **Don't trust code alone for the crypto.** The IV salt/nonce ordering and the exact stretch
  constant were only *certain* after emulation and a live capture. Reading the disassembly got the
  hypothesis; running the code and watching the wire confirmed it.
- **Two device generations.** This app also drives an older family (Swing, Mobile, Sleeper, …) over a
  *different* characteristic layout (`state`/`stateUpdate`) and crypto. Their opcodes do **not**
  carry over to the magic-device generation the Lumalou belongs to. Don't assume across generations.

## Reproduce it yourself

Roughly, start to finish:

```bash
# 1. unpack + decompile the native build
unzip -d apk8 fisher-price-8.6.4.apk
jadx -d jadx8 fisher-price-8.6.4.apk
#    read: com.mcpp.mattel.MpidPeripheral / MpidService / ManufacturingKey / FPLumaModel

# 2. reverse the native crypto
#    open lib/arm64-v8a/libnative-lib.so in Ghidra; focus on
#    mpid_AES_128_CTR, mpidEncryptInternal, crc8_calc

# 3. confirm the KDF by running it
#    map the .so into Unicorn (ARM64), stub libc, run .init_array,
#    call the exported functions and diff their output vs your Python

# 4. talk to the device (read-only first)
pip install bleak cryptography
#    dump the GATT table, read the factory token, do the handshake,
#    request GLOBAL_STATE — no writes yet

# 5. (optional) capture ground truth
#    enable btsnoop HCI on Android, drive the app, open the log in Wireshark
```

The Python package in this repo is the cleaned-up result of steps 3–4. If you have a **Bunny**
(`gmn58`) or **Whisper** (`ghp38`) — same handshake, different commands — a capture or a test would
help extend support; please [open an issue](https://github.com/stramanu/lumalou/issues).

## Safety

The libraries **never** write to the DFU service (`0000153x-…`) and never send OTA opcodes. A wrong
write there bricks the device. If you follow this process on your own hardware: read before you
write, and make your first write something harmless like a light colour.
