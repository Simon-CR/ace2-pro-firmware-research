# RFID: what the ACE accepts, what it refuses, and what we can now read

## The reader

A generic **MFRC522-class ISO 14443A** front end, one per pair of slots. `sub_800DEB6` programs
the classic register set (TxControl `0x14`, ModWidth `0x24`, RFCfg gain `0x26 ← 0x48`, …) and runs
the standard anticollision/SELECT cascade (SEL `0x93/0x95/0x97`, NVB `0x20` then `0x70`, UID BCC
check). It will enumerate **any** compliant tag — the restriction is entirely in firmware.

The register map is standard MFRC522 even though `VersionReg` reads `0x18` rather than a genuine
chip's `0x91`/`0x92` — i.e. a clone with a standard layout.

**Hardware fact that matters: slots 2 and 3 share one reader *and one antenna*, which can see
both bays' tags at the same time.** The identify path throws away the low index bit
(`(index << 1) & ~2`), so index 2 and index 3 energise the same coil. If two tags are in range you
get `code 4` (ANTICOLLISION) and no reliable read. See
[04-tag-operations.md](04-tag-operations.md) for how to work around this — it is not optional,
and ignoring it invalidated an entire round of our testing.

## What the firmware accepts

The acceptance gate is purely structural — **there is no cryptography of any kind** in the RFID
path. No signature, no HMAC over tag data, no MIFARE sector-key crypto. The only "auth" calls are
the ISO 14443A anticollision handshake.

A tag is accepted if:

1. it selects cleanly as 14443A with a valid BCC, and
2. **page 4 begins `7B 00 65 00`** — two little-endian u16s: magic **123** and version **101**.

The RFID cache task (`0x0800EA14`) compares the header against `123` and against `456`, where
`456` is the firmware's own "already validated" sentinel that it writes back into the slot record.
(hakimio's note that "magic values 123 and 456 appear in RFID validation" is half right: `123` is
the external gate, `456` is internal state, and neither is ever compared against arbitrary tag
content.)

Everything after that is the plain Anycubic page layout documented by
[DnG-Crafts/ACE-RFID](https://github.com/DnG-Crafts/ACE-RFID), which our raw dumps match byte for
byte.

**Practical consequence:** a tag written in the Anycubic layout is indistinguishable from a
factory tag, because there is nothing to distinguish. Anyone can write acceptable tags with an
ordinary NTAG213/215 — no keys required.

## Why Bambu tags fail on stock firmware

Bambu spool tags are **MIFARE Classic 1K** with encrypted sectors. The firmware only ever emits
anticollision/SELECT, HALT (`0x50`) and NTAG **READ** (`0x30`) — a whole-image sweep finds **no**
`MFAuthent` (CommandReg `0x0E`), no 6-byte key load, no SHA-256 constants, no HKDF labels and no
Bambu master key.

So a Bambu tag *selects* fine (that is where its UID comes from) and then NAKs the first
unauthenticated read. That is the `READFAILED (6)` you see. It is not a hardware limit, an
antenna problem or a positioning problem — it is a missing code path.

## Native On-Chip Bambu MIFARE Classic Authentication (V1.1.60O)

Starting in firmware `V1.1.60O` (commit `9fb5dbe`), Bambu Lab tags are authenticated and decoded **entirely on-chip** on the STM32F103/GD32F303 Cortex-M3 MCU with zero host reliance.

### Autonomous On-Chip Cryptographic Engine

A freestanding, zero-heap C and Thumb-2 assembly implementation (`native_tag_decoder.c` / `uid_stub.s`) implements RFC 5869 HKDF-SHA256 directly in SRAM:

```
HKDF-Extract(salt=BAMBU_MASTER_KEY, ikm=UID) -> PRK
HKDF-Expand(PRK, info=b"RFID-A\0", L=96) -> Key Stream (16 sectors x 6 bytes)
```

Key derivation stream offsets (6 bytes per sector key):
- **Sector 0 Key A:** Offset `0..5` -> `2C4E3DBA1935` (for UID `1EF5E298`)
- **Sector 1 Key A:** Offset `6..11` -> `0CAAB242E8B2` (for UID `1EF5E298`)

### RC522 Hardware Register Timing & Framing Invariants

To execute MIFARE Classic hardware mutual authentication on the MFRC522 transceiver:

1. **Card Selection Cascade Reset (`rfid_select`):** Prior to issuing authentication, standard ISO 14443A cascade level 1 anticollision/SELECT must be executed to transition the card from IDLE/HALT to the ACTIVE state.
2. **`PCD_MFAuthent` (`0x0E`) Command Execution:**
   - Command code: `CommandReg (0x01) <- 0x0E`
   - Authentication mode: `0x60` (Auth-A)
   - Block address: `0x04` (Sector 1) or `0x00` (Sector 0)
   - 6-byte derived key loaded into FIFO
   - 4-byte card UID loaded into FIFO
3. **CRITICAL Framing Invariant (`TxCRCEn=0` / `RxCRCEn=0`):**
   - Transmit CRC (`TxModeReg 0x12` bit 7 `TxCRCEn`) MUST be cleared to `0`.
   - Receive CRC (`RxModeReg 0x13` bit 7 `RxCRCEn`) MUST be cleared to `0`.
   - *Why:* The RC522 Crypto1 coprocessor handles parity and framing bits internally during the 3-pass mutual authentication handshake. If `TxCRCEn` is enabled, the transmitter appends an extraneous 2-byte CRC_A to the auth payload, immediately causing card framing rejection.
4. **Hardware State Latch (`MFCrypto1On`):**
   - Polling `Status2Reg (0x08)` bit 3 (`MFCrypto1On`): When authentication succeeds, bit 3 latches to `1` in silicon.
5. **Decrypted Payload Extraction:**
   - With `MFCrypto1On = 1`, standard `PCD_Transceive (0x0C)` with MIFARE `0x30` READ commands and CRC enabled (`TxCRCEn=1`, `RxCRCEn=1`) reads blocks directly through the hardware Crypto1 stream cipher.
   - **Block 4:** Unpacks detailed material string (e.g. `PLA Translucent`).
   - **Block 5:** Unpacks physical RGBA color swatch (e.g. `[171, 128, 232, 255]`, hex `#AB80E8`), nominal spool weight (1000g), and filament diameter (1.75mm float).
   - Populates Nanopb `FilamentInfoResponse` directly into slot SRAM with `version = 0x0102`, `sku = "SM1EF5E298"`, `brand = "Bambu Lab"`, `code = 0` (SUCCESS).

### Live Spool Hardware Ground Truth

Proven on physical spool `1EF5E298` in Slot 3:
- Sector 0 Key A: `2C4E3DBA1935` -> `Status2 = 0x08 (MFCrypto1On)`
- Sector 1 Key A: `0CAAB242E8B2` -> `Status2 = 0x08 (MFCrypto1On)`
  * Block 4: `PLA Translucent`
  * Block 5: `ab 80 e8 ff e8 03 00 00 00 00 e0 3f` -> RGBA `[171, 128, 232]`, 1000g, 1.75mm
  * Result: `code = 0` (SUCCESS), `brand = "Bambu Lab"`, `color = "#AB80E8"`

## Bambu tags are permanently read-only

After successful authentication, MIFARE `WRITE` (0xA0) to an **all-zero, unused** data block was
NAKed (both phases returned `0x04`, not the `0x0A` ACK). The sector trailers explain why:

```
trailer, every sector: 00000000 0000 87 87 87 69 000000000000
  data blocks   C1C2C3 = 010   read: KeyA|B,  write: NEVER
  trailer block C1C2C3 = 101   access bits themselves: write NEVER
```

`010` makes every data block permanently read-only, and because the trailer is *also* write-locked
the access bits can never be returned to a writable state — not with KeyB, not with any key, not
by Bambu. This is not a key problem or a protocol problem; the tags are factory-sealed.

**So a Bambu tag can never be re-purposed for a refill.** It will report its original filament
forever. The practical lifecycle is: use the factory tag while the spool holds its original
filament (the ACE can now read it fully), and when refilling with something else, remove or
destroy the factory tag and apply your own writable tag. Never leave two tags on the same face —
that is the `code 4` anticollision case and it blocks reads entirely. (Aluminium tape over the
unwanted tag detunes it and is a reversible alternative to destroying it.)

## Writing tags you own

NTAG `WRITE` (0xA2) through the same path works. Proven by writing `DEADBEEF` to a verified
all-zero page and restoring it:

```
page 38 before : 00000000
write          -> page 38: deadbeef      *** confirmed ***
restore        -> page 38: 00000000
```

Note that writing *identical* bytes and reading them back proves nothing — only a changed value
does. We initially "proved" the write path that way and had to retract it.

Safety rules for any write:

- **Never write a MIFARE sector trailer** (`block % 4 == 3`). A wrong access-bit pattern destroys
  that sector permanently.
- **Never write NTAG lock bytes or OTP pages** (pages 0–3), and avoid the configuration pages
  (NTAG213 41–44, NTAG215 130–134). These are one-way.
- Read the target first, keep the original bytes, and verify after writing.
- Block 0 is the read-only manufacturer block on MIFARE — the UID cannot be changed, which is
  good, because that is the identity everything keys on.

## Behaviour summary by tag type

| | Anycubic-format NTAG | Bambu MIFARE Classic | OpenSpool / NDEF NTAG |
|---|---|---|---|
| Select / anticollision | works, 7-byte UID | works, 4-byte UID | works |
| Page read (`0x30`) | works | NAKed (needs auth) | works |
| Page-4 header gate | `7B 00 65 00` → decoded | n/a | NDEF magic → fails the gate |
| Stock firmware result | `SUCCESS` + full fields | `READFAILED (6)` | `READFAILED`-class |
| With UID passthrough | unchanged | **UID in `sku`, version `0x0201`** | **NO — stub never fires, see below** |
| Writable? | yes (it is your tag) | **never** | yes |

The useful distinction: Bambu fails at the *read* stage (crypto), OpenSpool fails at the *format*
stage.

**CORRECTED 2026-09-01 — they do NOT both land on the UID path.** That was a prediction in this
document and it is wrong, proven on hardware. The UID stub hooks **one** exit: the `READFAILED`
(code 6) path, "card selected but the read was refused", which is what MIFARE crypto produces. An
OpenSpool tag reads fine (`0x30` works, row 3 above) and fails *later*, at the page-4 header gate —
a different exit, which the stub does not touch. So it falls through to the positional Anycubic
parser and returns garbage as success. See "V1.1.3W" below.

**The fix is a second hook on the format-gate exit**, reusing the same stub. Then UID-keying really
does cover every case, which is what this paragraph originally claimed.

## V1.1.3W — the build that is actually running, and what it does (2026-09-01)

**Never recorded in this repo until now**, though it is in the 2026-08-28 session history:

```
19:32  Built: UID stub preserved at 0x080197A0, new 170-byte RC522 stub at 0x080197E0,
       magic intact, CRC 0x833C
19:50  1.1.3W flashed
```

**So `V1.1.3W` = the UID stub PLUS the RC522 passthrough** that exposes the firmware's own
transceive routine at `0x0800F32C`. Both capabilities are present; tag writing should work on this
build. It is the successor to `V1.1.3R` (first RC522 stub, which could not drive the RF layer by
hand).

Observed 2026-09-01 with an **OpenSpool NDEF tag** in lane 1, live on the machine:

```
ACE[0]: Slot 1 RFID detected -> querying get_filament_info...
ACE[0]: Slot 1 RFID full data -> sku=application/json{", temp=28770°C (min=26719, max=30821),
        color=RGB(58,34,101), hotbed={'min': 8804, 'max': 8762}, brand=
```

**That is neither stock nor UID passthrough.** Per the table above, stock returns a `READFAILED`
class for a tag that fails the page-4 header gate, and the UID-passthrough patch returns the UID in
`sku`. W returns **code 0 with the raw NDEF payload run through the positional Anycubic parser**:

- `sku = application/json{"` is the NDEF MIME-type record header.
- `material = ,"version":"1.0","t` is more of the same JSON, read at the material offset.
- `temp`, `hotbed` and `color` are whatever bytes landed at those offsets.

**Why, given the UID stub IS in this build:** the stub hooks exactly one exit — `READFAILED`
(code 6), which is what a Bambu tag's NAKed page read produces. An OpenSpool tag never reaches it.
Its pages read fine; it fails at the **page-4 header gate**, a different exit that falls through to
the positional parser. One hook, two failure modes, only one covered.

### Why that is the worst of the three outcomes

| firmware | foreign tag result | is it safe? |
|---|---|---|
| stock | `READFAILED` | yes - honest failure, lane reads untagged |
| UID passthrough (`M`) | UID in `sku` | yes - a stable key to look up |
| **`W` (running)** | **code 0 + garbage that looks structured** | **no** |

A `READFAILED` is honest. A UID is useful. Garbage returned as *success* is neither, and it reached
three consumers before anything caught it:

1. **The heater.** `_ACE_PRE_TOOLCHANGE` sets the pre-toolchange target from the lane's RFID temp
   (`heating to 210C for T2 (source: rfid)`). A lane reporting `28770` offers that as a setpoint.
2. **The gcode parser.** The payload contains double quotes, and lane text is interpolated into
   `RESPOND MSG="..."`, which terminates the string early:
   `Malformed command 'RESPOND MSG="  T1  READY  ,"version":"1.0","t  (no spool assigned)"'`
3. **The panel**, which displayed `,"version":"1.0","t | 28770°C` as a filament.

### Host-side mitigation added 2026-09-01

`instance.py` `_handle_rfid_info_response` now **rejects the whole decode** before it reaches
inventory, on any of:

- `hotbed.min > hotbed.max` (here 8804 > 8762) - no real tag describes an inverted range
- nozzle temp outside `0..500`
- quotes or control characters in `sku` / `brand` / `material`

Rejected decodes log and are treated as **no tag**, which is the honest state and the one the
preload search handles safely. The whole decode is discarded rather than individual fields
sanitised: a positional misparse means every field came from the wrong offset, so a field that
happens to look sane is still meaningless.

**This is a workaround, not the fix.** The fix is the UID passthrough stub in firmware, which turns
every foreign tag into a stable key the host can look up in FilaMan/Spoolman. Until W is either
identified or replaced with a build that has it:

- **Anycubic-format tags** (write your own - SM22 and SM24 on this machine) work correctly.
- **Untagged lanes** are safe and honest; assign with `MMU_GATE_MAP GATE=<n> SPOOLID=<n>`.
- **An OpenSpool tag is worse than no tag on this build.** Remove it or overwrite it in Anycubic
  format until the firmware question is settled.

### The actual fix

**Add a second hook on the page-4 header-gate exit**, branching to the same 64-byte UID stub at
`0x080197A0` that already serves `READFAILED`. No new stub, no size growth beyond the 4-byte branch
— the same edit shape that committed first time for the read path. Then OpenSpool and any other
foreign NDEF tag return their UID exactly as Bambu tags do, and the host keys on it.

Until then, on `V1.1.3W`: **an OpenSpool tag is worse than no tag.** Write Anycubic-format tags for
spools you own (that path needs no firmware change at all), and leave the rest untagged.

## Where spool data comes from, and who wins (design, 2026-09-01)

Settled with Simon. Two independent questions that were being conflated: *where does identity come
from* and *where does displayed data come from*.

### Resolution order

```
1. FilaMan, if reachable AND the spool resolves   -> AUTHORITATIVE
2. Tag content                                    -> fallback display only
3. Neither                                        -> prompt to bind
```

**FilaMan trumps the tag whenever it answers.** It is the live, editable record: a material
corrected in the backend must not be overridden by a snapshot written to a tag months earlier. The
tag is never the authority on a spool that resolves.

**But the tag must still be able to render on its own.** Simon: *"if filaman backend is not
available, we can still display spool id (if there's one), color, material, temp."* The printer's
link to that subnet has measured 42/82/189 ms RTT and has produced `HTTP error: 500 Timeout while
connecting` flapping before, so an unreachable backend is a real operating state, not a hypothetical.
A panel that renders nothing without the network is a panel that goes blank on a bad WiFi minute.

**This is why UID-only decoding is not sufficient**, and why the firmware build must return the raw
tag bytes rather than just an identifier: a UID renders nothing offline.

### The tag's two jobs, which are separate

| | used | source of |
|---|---|---|
| **identity** (`SM<n>` in the SKU) | ALWAYS - it is the lookup key | which FilaMan spool this is |
| **descriptive data** (material, colour, temps, diameter) | only when the lookup fails or the backend is down | fallback display |

Identity is what makes a spool self-describing with no pre-registration, works from either tag on a
dual-tagged spool, and survives re-tagging - none of which UID-keying manages. See
`ace_mmu_shim.py:_spool_from_sku` / `_autobind_spools`, already live: `SM22` -> spool 22.

### Disagreement is a finding, not a nuisance

When both sources are present and they conflict - tag says PLA, FilaMan says PETG - **the backend
wins, and the mismatch is surfaced.** Silently overriding hides the two cases that actually cause
it: the tag is on the wrong spool, or the spool was refilled with a different material. Purge
temperature and transition volume both derive from material, so a wrong answer here is not cosmetic.

### What this means for tags we write

Write the FULL descriptive set plus `SM<spool_id>`, in whatever format. A tag written that way is
self-sufficient by construction: it renders correctly on a machine that has never seen this FilaMan
instance, which is also what makes a spool portable between printers.
