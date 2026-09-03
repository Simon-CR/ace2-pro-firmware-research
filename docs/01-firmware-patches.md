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
| 4 | read RX buffer byte at `arg1` (`arg1` is 6 bits, so `BUF+64 .. BUF+127` only) | byte |
| 5 | read received bit length | byte |
| 6 | **SELECT a card** (powers the reader, then `sub_800DEB6`) | 0 = ok |
| 7 | **bulk page read** pages 4–39 (`sub_800E18C`) — `arg1`/`arg2` ignored | byte count (`144`/`0x90` = ok) |
| 8 | clear the cached tag record for slot `arg1` | 0 |

Frame buffer is the firmware's own tag-page buffer at `0x20000704`
(`+0` TX, `+64` RX, `+128` received bit length), which is idle while background scanning is off.

Every op returns a **single byte in the response's `code` field**. Nothing the passthrough does
ever populates `sku`, `type`/`tag` or any other response field — only the *UID passthrough* patch
writes `sku`, and that is the normal identify path, not this one.

#### op 7's readback window is too narrow — a known defect

`op 7` works: it calls `sub_800E18C(ctx, 0x20000704)`, which issues nine 16-byte `READ 0x30`
frames and returns the accumulated byte count. **`0x90` (144) is the success value**, not a status
code or an echo — 36 pages × 4 bytes. It takes no arguments; the disassembly has no `ubfx` on the
op-7 path, so `arg1`/`arg2` are dead and the return is constant across every argument you try.

The defect is on the way back out. `op 7` writes its 144 bytes at `BUF+0`, but `op 4` reads at
`BUF+64+arg1` with `arg1` masked to **6 bits**. So `op 4` can only address dump bytes 64…127 —
**pages 20…35**. Pages 4…19 (magic, version, sku, brand, material) and 36…39 are unreachable, and
those are the pages anyone actually wants. Consequence: `op 7` is not usable as a whole-tag dump,
and per-page `0x30` transceives plus `op 4` remain the only complete read path. Our own tag dump
in `data/anycubic_ntag_dump.json` was taken that way, not with `op 7`.

Two further side effects of the 144-byte write at `BUF+0`:

- it **destroys anything staged in the TX region** (`BUF+0..63`), so re-stage after every `op 7`;
- it **overwrites the bit-length byte at `BUF+128`**, so an `op 5` after an `op 7` returns page 36
  byte 0, not a bit count.

You can confirm `op 7` really transferred data without changing the firmware: after `op 6` then
`op 7`, `op 4` with `arg1 = 0..3` returns **page 20**. On an Anycubic-format tag that is the ABGR
colour word (`ff 59 d9 f7` on ours), which is unmistakable.

**The fix, if this patch is rebuilt:** the packed request word has bits 15..14 free, so widening
the offset argument is one instruction. Add an op 9 that reads `BUF + ubfx(r5, 8, 8)` — an
absolute 0…255 staging-buffer offset — rather than changing `op 4` and breaking `op 3` callers.

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

### A second use case: the reader as a rotation sensor

The spool tag passes the antenna once per revolution. Polling for tag presence at a fixed interval
therefore tells you whether the spool is actually **turning** — not just whether the motor is being
commanded to turn.

That matters because the feed motor and the spool share one coupling
([10](10-spool-drive-and-feed-telemetry.md)), so `magnitude_mm` reports the motor faithfully even
when the spool is jammed, binding in its cradle, or the wrong size. A tag that appears and
disappears on a regular cadence is positive evidence of rotation; a tag that stays put — or never
appears — while the motor reports hundreds of millimetres is a stuck spool.

This gives the drying rotisserie (`klipper/ace_dryroll.cfg`) a real feedback signal instead of an
open loop, and it costs nothing but a periodic `SELECT`.

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

## V1.1.3W: what it contains, and the one defect blocking raw tag reads (2026-09-01)

`V1.1.3W` was flashed 2026-08-28 19:50 and is what the machine runs today:

```
uid stub    64 bytes at 0x080197A0   (Bambu UID -> sku, version 0x0201)
rc522 stub 170 bytes at 0x080197E0   (exposes the firmware's own high-level RFID primitives)
```

**Source, build script and calling convention are all in `firmware/` in this repo** -
`rc522_stub.s`, `uid_stub.s`, `build_patch.py`, `apply_patch.py`, `patch.json`. Built images and
the stock image are in `L:ce2-fw-analysis\ex\` and mirrored on the printer at
`~/printer_data/ace2-fw-analysis/ex/`.

*(An earlier revision of this section claimed these were lost. That was wrong - a `find` that only
covered `docs/` and a `-maxdepth 5` search from `/`. Everything was where it should be.)*

### Calling convention

The stub hooks `FILAMENT_IDENTIFY` (cmd 68) on its `index >= 4` rejection path - dead code, since
there are only four slots - so normal identify on slots 0-3 is untouched. The operation is packed
into the request's single uint32:

```
bit31=1 | bit24=reader | op<<16 | arg1<<8 | arg2

op 0  read register arg1                       -> byte
op 1  write arg2 to register arg1              -> 0
op 2  store arg2 into staging buffer at arg1   -> 0
op 3  transceive: cmd arg2, arg1 TX bytes      -> helper status
op 4  read RX buffer byte at arg1              -> byte
op 5  read received bit length                 -> byte
op 6  SELECT a card (sub_800DEB6)              -> status (0 = ok); UID at BUF+13..19
op 7  bulk page read 4..39 (sub_800E18C)       -> byte count (144 = 0x90 ok), data at BUF
op 8  clear the cached tag record for slot arg1 -> 0
```

Staging buffer `0x20000704` (the firmware's own tag-page buffer, idle while background scanning is
disabled): `+0` TX/select scratch, `+64` RX, `+128` rx bit length.

v3 exists because v2 could drive registers but never got a card to answer: `0x0800F32C` is only the
transceive STEP, while every working read wraps it in `sub_800DEB6` (reset + antenna + analog init +
REQA/anticollision/SELECT). v3 exposes the firmware's own high-level primitives instead, which are
proven because normal identify uses them.

### The defect that blocks raw tag reads, and its fix

Recorded in the stub's own header:

> **op 7** takes no args, and writes 144 bytes at `BUF+0` while **op 4** reads `BUF+64` with a
> 6-bit offset - so **only dump bytes 64..127 (pages 20..35) can be read back**, and the write
> clobbers the staged TX frame and the bit-length byte at `BUF+128`.

**This is what stands between the machine and Simon's requirement** (*"I want the firmware to be
able to give us the tag ID and the data"*). The UID is reachable today via op 6. The page data is
read by op 7 but only its second half can be retrieved - and **page 4 is in the unreachable half**,
which is precisely where every format's detection signature lives (Anycubic `7B 00 65 00`, NDEF
magic, etc). So format detection is impossible through the current readback window.

**The fix is already designed in that same comment:** bits 15..14 of the packed word are free, so

> a future **op 9** reading `BUF + ubfx(r5,8,8)` would expose the whole buffer.

A few instructions, no size growth beyond the op, and it turns the firmware into the format-blind
transport the host-side parser needs. Do this one before the format-gate hook - it is what makes
every non-Anycubic format readable at all.


## The raw-tag hook (designed 2026-09-01, not yet built)

### Why a third hook

The two existing hooks each cover one exit and leave the case that matters uncovered:

| tag | path taken | result |
|---|---|---|
| Anycubic layout | normal decode | full fields — correct |
| Bambu MIFARE | `READFAILED(6)` exit → **UID stub** | UID in `sku` — useful |
| OpenSpool / FilaMan / Creality / OpenPrintTag | **neither** — pages read fine, then the positional parser runs on them | **code 0 + garbage that looks structured** |

The third row is the whole problem. Those tags read *perfectly*; they simply are not in Anycubic's
layout, so parsing them at Anycubic's offsets yields `sku=application/json{"`, `temp=28770C` and
an inverted hotbed range — returned as SUCCESS. Stock firmware's honest `READFAILED` is safer than
what `W` currently does.

### Where the bytes are

The handler at `0x0800E7A8` reads the tag into a stack buffer and only then parses it:

```
800e80c:  add.w  r8, sp, #4        @ scratch base
800e82a:  add.w  r1, r8, #24       @ r1 = sp+28   <- destination for page data
800e82e:  bl     0x800e18c         @ the page read; returns byte count in r0
800e832:  cmp    r0, #140
800e834:  bcs.n  0x800e842         @ >=140 bytes -> positional parse
800e836:  movs   r0, #6            @ READFAILED   (UID stub hooks here)
800e842:  ...                      @ positional parse starts
```

**At `0x0800E842` the complete raw tag image is at `sp+28` with its length in `r0`, before any
positional interpretation.** That is the interception point, and it is strictly better than
hooking a later "format gate": nothing has been misread yet, and the firmware has already done
every hard part — parking the tag, driving the RF layer, CRC, anticollision.

### The design

Hook `0x0800E842`. The stub tests the first four bytes at `sp+28`:

- `7B 00 65 00` (magic 123, version 101) → branch to the original parse. **Anycubic tags are
  completely unaffected**, which keeps the risk profile of the existing patches.
- anything else → copy raw bytes into the response and return `code = 0` with a version sentinel
  (the same convention `uid_stub.s` established with `0x0201` for "this sku is a UID").

The host then owns format identification entirely: firmware stays format-blind, and support for a
new tag layout becomes a Python change with no reflash.

**Why this retires most of the host-side work:** reading a foreign tag needs no RC522 passthrough,
no `op 9`, no host RF state machine, and above all no spool-rotation ritual — the automatic
identify already parks the tag. It also removes the stale-RX-buffer trap, which is invisible and
cost two failed read attempts on 2026-09-01: the shared staging region holds whatever the last
scan left there, so a stale buffer is indistinguishable from a successful read unless two
different pages are compared.

### The one real constraint: `sku` is 19 bytes

Response layout is `+4 u16 version, +8 sku[19], +140 u32 code`. A 144-byte tag image does not fit,
so the transfer has to be chunked. The chunk source is free — the bytes are already in RAM, so
each chunk costs one identify against a cached read, with no re-read and no anticollision exposure.

Open design point, to settle before building: **how the host selects a chunk.** The request index
cannot carry it — `cmp r0, #4` at `0x0800E7D2` rejects anything above 3 before the read happens
(that rejection is what the RC522 passthrough hooks). Candidates:

1. a static counter in the stub, cycling chunks on successive calls, with the chunk number
   returned in `version` so the host can reassemble regardless of ordering;
2. raw bytes rather than hex in `sku` (19 per call instead of 9) — needs the host to read
   protobuf field 3 as bytes, since the driver currently does `.decode(errors="ignore")` and
   would mangle them;
3. an additional response field, which means the stub builds protobuf itself — more invasive.

(1) + (2) gives 19 bytes per call, 8 calls for a full image, and no protobuf work.

## V1.1.41: the live read answered worse than the background one (2026-09-02)

A foreign tag read through **cmd 68** came back `sku=None, version=0x0202`, while the *same tag*
read moments later by the background worker resolved correctly via `rawtag_extract_stub`. No error,
no log — the synchronous path just quietly returned less than the asynchronous one.

The cause was **our own patch, not stock ROM**. `rawtag_stub`'s "not Anycubic" branch committed the
`0x0202` sentinel and branched straight to `epilogue` (`0x0800E904`), so it never reached `resume`
(`0x0800E846`) and therefore never reached the sm_id inject at `HOOK_CMD68` (`0x0800E8A2`) — which
sits on the Anycubic-success path, *after* the native sku/version stores. Every foreign tag on the
live-read path bypassed the inject by construction.

That left the host to recover through the **op-9 raw walk**, which is the wrong tool for a
synchronous caller: ~2.88s (144 reads x 0.02s), torn-prone (a firmware-side scan splices the R5
buffer mid-walk), and cross-reader-contaminated (slot 3 has returned slot 0's `SM22`). Binding off
it is exactly the wrong-bind the design forbids, and its latency is what defeated the motion-gated
identify loop — the loop's synchronous check completed long before the fetch returned.

**The fix** searches `0x20000704` for `sm_id` *before* committing the sentinel and, on a hit,
answers like a native decode (`version 101` + `"SM<n>"`), skipping the `0x0202` commit entirely.
On a miss the original raw path runs byte for byte, so nothing regresses for a genuinely
unrecognised tag.

Two facts make this safe, both verified by disassembly rather than assumed:

- **cmd 68 reaches the same page read.** It calls `rfid_pageread` (`0x0800E18C`) at `0x0800E82E` —
  there is no caller-specific variant — so the three extend-read pokes have already staged pages
  4-51 (192 bytes) at `0x20000704` on this path too. The `sm_id` really is there to find.
- **The preceding memcpy does not destroy it.** It copies the 140-byte local image back over the
  first 140 bytes of `0x20000704` with identical data, never touching the 144-191 tail where
  `sm_id` lives.

The Anycubic branch is untouched: `beq` still replays the displaced `add.w lr, sp, #140` and
resumes at `0x0800E846`.

### The build was broken, and had to be fixed first

`build_patch.py` could not complete at all on binutils 2.45: two absolute `b.w` literals
(`0x0800FE3A` in the extract stub, `0x0800E8A6` in the cmd68 stub) had no `SYMS` entry and failed
with `Unknown destination type (ARM/Thumb)` / `dangerous relocation`. Every other branch target in
these stubs resolves through a named symbol, which only warns. Named them `extract_resume` and
`cmd68_resume`. Same 4-byte encoding, so no stub changes size and no appended-tail address moves.

