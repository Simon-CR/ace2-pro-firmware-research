# What we added to the firmware, and why

Firmware designation: **ACE2-Open**, based on Anycubic **V1.1.31**.
The image reports version string `V1.1.3O` so you can always tell what is running.

All patches hook **dead or error paths** of one handler — `GET_FILAMENT_INFO` /
`FILAMENT_IDENTIFY` (command 68) at `0x0800E7A8`. Normal operation is untouched: Anycubic tags
decode exactly as before, slots, feeding, drying and everything else are unmodified.

> Addresses below are for V1.1.31 only. Do not apply them to another version.

---

## Patch 1 — UID passthrough

**Hook:** `0x0800E836`, the `READFAILED` exit (`movs r0,#6 ; b.n`) → `b.w` to a 64-byte stub.

**What it does.** Reaching that instruction means the ISO 14443A select *succeeded* — the
anticollision cascade completed and the tag's UID is already sitting in the handler's scratch
buffer at `sp+17` — but the NTAG page read was refused. Instead of returning `READFAILED`, the
stub formats the UID as hex into the response's `sku` field, sets `version = 0x0201` as a
sentinel meaning "this sku is a raw tag UID", sets `code = 0`, and rejoins the handler's normal
epilogue.

**Why we wanted it (use case).** The ACE cannot read Bambu Lab tags — they are MIFARE Classic and
the firmware has no authentication path — so a Bambu spool is simply "unknown" forever. But the
UID is read off the wire *before* any authentication, and it is unique and immutable. Reporting
it turns every unreadable tag into a stable identity that a filament backend (Spoolman/FilaMan
via `extra.nfc_id`) can look up. One mechanism covers Bambu tags, OpenSpool tags, and blank NTAGs
alike, without the firmware needing to understand any of their formats.

**Cost:** 4 bytes changed, 64 bytes added. Anycubic tags never reach this path.

---

## Patch 2 — RC522 passthrough

**Hook:** `0x0800E7DA`, the `index >= 4` rejection (`movs r0,#1 ; b.n`) → `b.w` to the stub.
There are only four slots, so this path is unreachable in normal use — which makes it free real
estate that cannot interfere with anything.

**Interface.** The sub-command is packed into the request's `index` field, and the result comes
back in the response's `code` field:

```
bit31 = 1        forces index >= 4 so we land in the stub
bit24            reader select (0 -> ctx 0x20001604, 1 -> ctx 0x20001608)
bits 23..16      op
bits 13..8       arg1 (register number / buffer offset / frame length)
bits  7..0       arg2 (value / RC522 command byte)
```

| op | action | returns |
|---|---|---|
| 0 | read RC522 register `arg1` | register byte |
| 1 | write `arg2` to register `arg1` | 0 |
| 2 | stage `arg2` into the frame buffer at `arg1` | 0 |
| 3 | transceive: RC522 command `arg2`, `arg1` bytes from the buffer | helper status |
| 4 | read RX buffer byte at `arg1` | byte |
| 5 | read received bit length | byte |
| 6 | **SELECT a card** (powers the reader, then `sub_800DEB6`) | 0 = ok |
| 7 | **bulk page read** pages 4–39 (`sub_800E18C`) | byte count (144 = ok) |
| 8 | clear the cached tag record for slot `arg1` | 0 |

Frame buffer is the firmware's own tag-page buffer at `0x20000704`
(`+0` TX, `+64` RX, `+128` received bit length), which is idle while background scanning is off.

**Why we wanted it (use case).** Reading a Bambu tag needs MIFARE authentication, and writing any
tag needs arbitrary frames — neither exists in the firmware and neither is worth reimplementing
in ARM assembly. Exposing the reader instead moves all the protocol work to the host, where it
can be written and debugged in Python. The result is that the ACE can do things the OEM firmware
cannot: authenticate and decrypt Bambu tags, dump raw pages of any tag, and **write** tags.

**Cost:** 4 bytes changed, ~276 bytes added.

### Two details that are essential, and cost us hours

1. **op 6 must power the reader before selecting.** The stock handler does a GPIO write taken
   from `readerObj+44/+48` followed by a 20 ms settle (`0x0800E7F2`) *before* calling the select
   routine. Without it, select always returns failure — the chip simply is not up.
2. **Raw frames need the firmware's register setup.** Every firmware transceive is preceded by
   (from the HALT routine at `0x0800E108`):

   ```
   BitFramingReg (0x0D)  = 0
   TxModeReg     (0x12) |= 0x80     TxCRCEn
   RxModeReg     (0x13) |= 0x80     RxCRCEn
   Status2Reg    (0x08) &= ~0x08    clear MFCrypto1On
   ```

   CRC generation/checking is **off** by default in this state. If you also append CRC bytes by
   hand — as we did — every frame is malformed twice over and the card never answers. Enable CRC
   and send frames *without* manual CRC.

   Also note the helper's `r1` really is the RC522 command: HALT uses `Transmit` (4), not
   `Transceive` (12), because no reply is expected.

---

## Patch 3 — clear cached tag record (op 8)

**What it does.** Zeroes the cached record for one slot: base `0x20000054 + slot × 164`, with
`version` (u16) at `+286`, `sku[19]` at `+288`, `type[19]` at `+328` and colours at `+348` — the
fields served by the `GET_FILAMENT_INFO` (cmd 13) handler at `0x0800E910`.

**Verified on hardware:**

```
slot 2 BEFORE : version 101  sku 'SM24'  type 'PLA'
op 8 (clear)  -> 0
slot 2 AFTER  : version 0    sku ''      type ''
slot 0        : version 101  sku 'SM22'  type 'PLA+'   <- control, untouched
```

Two things worth knowing:

* **It clears the firmware's record only.** The Klipper driver keeps its own copy, so the panel
  still showed `SM24` afterwards. A lane that should read as genuinely unknown needs both cleared.
* **A live identify (cmd 68) does not repopulate the cache** — only the background scan does, on
  insert/preload. So after clearing, the record stays empty until the spool is preloaded again.

**Why we wanted it (use case).** The firmware caches a decoded tag per slot and that cache is
sticky. Precisely:

* **Measured:** it survives an eject. After `MMU_EJECT` completed and the slot read `empty`,
  `cmd 13` still returned `version 101 sku 'SM24'`. Toggling `SET_RFID_ENABLE` off and on does not
  clear it either.
* **Inferred:** the *insert* is what overwrites it. A lane held `SM25`/PETG before a different
  spool went in, and read empty afterwards — so the insert-triggered tag search rewrites the
  record, and writes an empty one when it decodes nothing. (Before/after states, not a directly
  observed transition.)
* **Note the two caches disagree.** The Klipper driver clears its own view immediately on eject
  (`rfid False`), while the firmware keeps its record. That mismatch is exactly what misled us:
  the panel said one thing and the device said another.

During testing this repeatedly produced stale answers that looked like live reads and sent us
down false trails. More practically, when a spool is swapped you want the lane to report "nothing
decoded yet" rather than the previous spool's identity. After op 8 the cache reads genuinely
empty, which is the honest state.

---

## Building

```bash
python3 firmware/build_patch.py --base ACE2_V1.1.31_20260306.bin --out ACE2-Open.bin
```

`build_patch.py` verifies the base image, assembles the stubs (needs `arm-none-eabi-as`/`ld`/
`objcopy`), inserts them **before the 8-byte magic trailer** (see
[02-ota-and-iap.md](02-ota-and-iap.md) — this is mandatory), applies the hooks, sets the version
string, and prints the CRC.

If you have no ARM toolchain, `firmware/patch.json` contains the assembled bytes and their
offsets, and `firmware/apply_patch.py` applies them with no toolchain required.

**No Anycubic firmware is distributed here.** You supply your own base image; the patch files
contain only our own code.
