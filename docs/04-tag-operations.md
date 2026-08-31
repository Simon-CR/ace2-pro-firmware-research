# Practical tag operations

How to actually get a tag read or written, in the order that works. These are the sequences we
arrived at after several wrong turns; each rule exists because skipping it produced a wrong
answer, not because it seemed tidy.

## Rule 0 — the antenna sees more than one bay

**Slots 2 and 3 share a reader and a single antenna, which can see both bays' tags at once.**
If two tags are in range you get `code 4` (ANTICOLLISION) and no reliable read — worse, you may
get a *successful* read of the wrong tag and never notice.

This invalidated an entire round of our testing: we "authenticated a Bambu tag" and got a clean
failure, when in fact the reader had selected the neighbouring NTAG, which cannot do MIFARE
authentication at all. The result was meaningless.

**Therefore, before any tag operation:**

1. Rotate the *other* lane's spool until the reader no longer reports that lane's tag.
2. Confirm you see either nothing, or the UID you actually want.
3. Only then park the target tag.
4. Restore both lanes afterwards.

Never act while `code 4` is possible.

## Rule 1 — park the tag, then work

The antenna is fixed in the bay and the tag rides on the spool, so a tag is only in the field
while it sweeps past. Two consequences:

- **A stationary read is nearly useless.** We polled a *known-good* tag 192 times over 45 seconds
  of hand rotation and got `FAILED` every time; the tag simply was not in front of the coil at
  those instants.
- **You cannot poll during a driver-issued move**, because Klipper's gcode queue is serial and
  your polls queue behind the macro. Drive the rotation with **raw asynchronous** ACE commands
  (`FEED_OR_ROLLBACK`) so the queue stays free.

The routine that works:

```
loop:
    rotate the lane a small step (15-25 mm) with a raw async FEED_OR_ROLLBACK
    wait for the slot to go idle
    probe with FILAMENT_IDENTIFY
    if it answers -> STOP. the tag is now parked in the field.
```

Observed: the tag comes into range within 15–510 mm. **One revolution of a 1 kg spool is roughly
600 mm of filament**, not the ~250 mm we first assumed, so allow at least that before concluding
there is no tag.

Once parked the spool is stationary and there is no time pressure — the reader also stays powered
for at least 7.85 s after a scan, and re-arms on each identify.

**Do all tag work while parked.** Restoring the lane position moves the tag back out of the field;
we caught ourselves restoring between tests and then wondering why the next step failed.

## Rule 2 — the reader must be powered, and CRC must be on

Before raw frames will work:

```
op 6                      SELECT (this also powers the reader: GPIO write + 20 ms settle)
write BitFramingReg 0x0D  = 0x00
read/or  TxModeReg  0x12 |= 0x80      TxCRCEn
read/or  RxModeReg  0x13 |= 0x80      RxCRCEn
```

Then send frames **without** appending CRC bytes — the reader adds and checks them. Sending a
manual CRC with CRC enabled produces a malformed frame and total silence, which is very easy to
misread as "the card is not there".

One exception: **`RxCRCEn` must be cleared for an NTAG `WRITE`**, whose reply is a CRC-less 4-bit
ACK. See "The NTAG WRITE path, in detail" below.

## Reading an NTAG (Anycubic-format, OpenSpool, blank)

**Read it a page at a time.** Park the tag, `op 6` SELECT, then for each page stage `0x30 | page`
and transceive with command `0x0C`. Each `READ` returns 16 bytes (four pages) into the RX region,
so nine transceives cover pages 4–39. This is how `data/anycubic_ntag_dump.json` was taken.

`op 7` (bulk page read) **is not a shortcut for this.** It does work — it returns `144` (`0x90`),
which is the real byte count for pages 4–39 — but it writes into `BUF+0` while `op 4` reads at
`BUF+64` with a 6-bit offset, so only dump bytes 64–127 (**pages 20–35**) can be read back out.
The pages that carry the sku, brand and material are exactly the ones you cannot reach. See
[01-firmware-patches.md](01-firmware-patches.md) for the mechanism and the one-instruction fix.

## Reading a Bambu tag

```
park tag
op 6 SELECT
enable CRC (rule 2)
for each sector you want:
    stage 0x60 | block | key[6] | uid[4]
    transceive with RC522 command 0x0E        (MFAuthent)
    read Status2Reg (0x08): bit 3 set = authenticated
    stage 0x30 | block, transceive 0x0C       (Crypto1 handled in silicon)
```

Keys come from the UID via the
[RFID-Tag-Guide](https://github.com/Bambu-Research-Group/RFID-Tag-Guide) derivation. `Status2Reg`
bit 3 (`MFCrypto1On`) is the authoritative "the key was correct" signal — if it is clear, nothing
downstream will work and there is no point reading further.

## Writing a tag

**NTAG** — single-phase: stage `0xA2 | page | b0 b1 b2 b3`, transceive `0x0C`. Three non-obvious
rules apply — clear `RxCRCEn`, ignore the ACK, re-SELECT before verifying. They are in "The NTAG
WRITE path, in detail" below, and skipping any of them produces a *silently* wrong result.

**MIFARE Classic** — two-phase: stage `0xA0 | block`, transceive, expect ACK `0x0A`; then stage
the 16 data bytes, transceive, expect ACK. (On Bambu tags this always NAKs with `0x04` — they are
permanently read-only, see [03-rfid-and-tags.md](03-rfid-and-tags.md).)

Safety, in order of how badly you will regret ignoring it:

1. **Never write a MIFARE sector trailer** (`block % 4 == 3`). A wrong access-bit pattern makes
   that sector unreadable *forever*.
2. **Never write NTAG pages 0–3** (UID, lock bytes, OTP) or the configuration pages
   (NTAG213 41–44, NTAG215 130–134). One-way changes.
3. **Read the whole tag first and keep the dump.** Everything we wrote was restorable because we
   had one.
4. **Verify with a changed value.** Writing identical bytes and reading them back proves nothing —
   we initially "proved" our write path that way and had to retract it. Write something different
   to a known-empty location, confirm it, then restore.

## The NTAG WRITE path, in detail

These four rules come from **decay71** (author of *multiACE*), who
drove the write path harder than we did, on our RC522 passthrough patch. Reported in the
[ACE 2 Pro gist thread](https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9).

### 1. Disable RX CRC for the WRITE transceive

Rule 2 above says "CRC on", and for `READ` that is right. For `WRITE` it is not: the tag answers
`0xA2` with a **4-bit ACK**, which carries no CRC, so the reader raises a protocol error and
discards the reply. Clear `RxCRCEn` (`RxModeReg 0x13`, bit 7) for the write frame. **Leave
`TxCRCEn` set** — the `0xA2` frame itself still needs its CRC_A appended by the reader. Restore
`RxCRCEn` before the next read.

### 2. The WRITE ACK does not come back through the tunnel — verify by read-back instead

The tag only emits the ACK after roughly **4 ms** of internal programming, which outlasts the
transceive's receive window. So through the passthrough you see `op 5` report `bits = 0x00` and
`op 4` return a **stale byte left over from the previous frame** — not a NAK, not an error, just
nothing.

**The write still succeeds.** decay71 confirmed this by read-back, and it matches our own
`DEADBEEF` result, where the helper returned `238` and the write had nonetheless taken effect.

Practical rule: **never treat the ACK as your success signal — it is not observable.** Verify
every page, or every 4-page chunk, by reading it back. Anything that branches on the ACK byte is
branching on garbage.

### 3. A WRITE leaves the tag's read pointer shifted — re-SELECT before verifying

A verify-read of **page 4** issued immediately after a write returned **page 19's** bytes.
Deterministically, reproduced twice. An `op 6` SELECT between the write and the verify read resets
it.

So the verify sequence is `WRITE → op 6 SELECT → READ`, not `WRITE → READ`. Skipping the re-SELECT
does not fail loudly; it silently hands you a different page's contents, which will read as a
successful verify of the wrong data if you are comparing against a buffer rather than against the
page you meant to write.

### 4. `op 7` (bulk page read) cannot return its payload

Independently reproduced by decay71: `op 7` returns a constant `0x90` for every `arg1`/`arg2`
combination, with `sku`, `tag` and the unparsed fields all empty, and no way to pull a page back.

That is expected, and `0x90` is not a failure — it is `144`, the byte count for pages 4–39, i.e.
the documented success value. The payload lands in device RAM at `0x20000704` and never crosses
the protobuf boundary; the passthrough returns one byte per call in `code` and touches no other
response field. `op 4`'s 6-bit offset then keeps all but pages 20–35 out of reach. Staying on
per-page `op 4` reads, as decay71 did, is the correct call until the patch is rebuilt with a
wider staging-buffer read op. Mechanism and fix in
[01-firmware-patches.md](01-firmware-patches.md).

## Designing a write macro

If you wrap this in a Klipper macro, two things belong in the design:

- **The anticollision guard is part of the sequence**, not a precaution: clear the other lane,
  confirm, park, act, restore.
- **Offer to flip the spool.** Each face carries a different tag with a different UID. Writing the
  same payload to both faces makes the spool orientation-independent, which is otherwise a real
  problem — and it avoids needing multi-UID support in your filament backend.
