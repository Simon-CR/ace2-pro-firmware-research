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

## Reading Bambu tags from the ACE

With the RC522 passthrough, the host performs the authentication itself and the reader handles
Crypto1 in silicon. Key derivation is entirely
[Bambu-Research-Group/RFID-Tag-Guide](https://github.com/Bambu-Research-Group/RFID-Tag-Guide)'s
work:

```python
HKDF(uid, 6, master, SHA256, 16, context=b"RFID-A\0")
```

In PyCryptodome's signature `HKDF(ikm, key_len, salt, hash, num_keys, context)` — so **the UID is
the input keying material and the master key is the salt.** Getting those backwards produces
completely different keys and fails silently (authentication simply returns with the crypto bit
clear). We lost hours to exactly that; take the derivation from their source rather than from
memory.

Result on a real spool (UID `899334FC`):

```
sector 0 KeyA A62EB420CE32  -> Status2 = 0x08 (MFCrypto1On)
  blk  0  899334fc d2 08 0400 ...        UID, BCC, SAK 08, ATQA 0004
  blk  1  "G00-K00" / "GFG00"            material variant id + filament id
  blk  2  "PETG"                         filament type
sector 1 KeyA EB22C585C318
  blk  4  "PETG Basic"                   detailed type
  blk  5  000000ff e8030000 0000e03f     colour RGBA, 1000 g spool,
                                         diameter 1.75 as an IEEE-754 float
  blk  6  41 00 08 00 ... 04 01 e6 00    temperatures
  blk  8  ... cd cc 4c 3e                float 0.2
  blk 12  "2026_05_02_08_50"             production timestamp
  blk 14  4a 01 -> 330                   length (m)
```

`Status2Reg` bit 3 (`MFCrypto1On`) is the authoritative "the key was right" signal.

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
| With UID passthrough | unchanged | **UID in `sku`, version `0x0201`** | UID in `sku` |
| Writable? | yes (it is your tag) | **never** | yes |

The useful distinction: Bambu fails at the *read* stage (crypto), OpenSpool fails at the *format*
stage. Both land on the UID path, which is why UID-keying covers every case without per-format
decoders in firmware.

## V1.1.3W — the build that is actually running, and what it does (2026-09-01)

**Undocumented until now.** The machine has been running `V1.1.3W` since at least 2026-08-31 (it
appears in `11-operator-interface.md` and `12-panel-visual-design.md` only as an *observed* value).
The documented builds are `V1.1.3O` (patches), `V1.1.3U` (first proven flash) and `V1.1.3M` (UID
passthrough). **W is none of them and its behaviour was never recorded.**

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

So W appears to **bypass or fail-open the page-4 header gate** without the UID formatting stub.

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

### Open, and it matters

**Nobody knows what `V1.1.3W` contains.** It is running on the machine, it is not stock, and no
build record exists. Before any further firmware work: dump the running image and diff it against
stock `V1.1.31` and against the `M` build, and record the result here. A device running an
unidentified image is not a base to patch from.
