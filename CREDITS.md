# Credits

## hakimio — the foundation of all of this

**[hakimio](https://github.com/hakimio) did the hard part.** This repository would not exist, and
could not have existed, without his work.

Before it, the ACE 2 Pro was a black box. Every existing project — ValgACE, DuckACE, BunnyACE,
szkrisz/ACEPROSV08 — targeted the *first-generation* ACE Pro and simply could not talk to an
ACE 2: the generation change swapped native USB serial for **RS-485**, moved from 115200 to
**230400 baud**, and replaced custom binary frames with **Protocol Buffers**. Nothing predating
his research can communicate with the device at all.

What he produced, and what we relied on directly:

- **The protocol itself** — frame format, sequence rules, the DISCOVER/ASSIGN handshake, and the
  `.proto` schema (`ace2-pro.proto`).
- **`ace2-pro-shell.py`** — an interactive shell that made the device explorable.
- **`ace2-ota-update.py`** — the OTA updater. *Every flash in this project used it.* Our two fixes
  are small changes to his tool, not a replacement for it.
- **The MCU firmware analysis** — FreeRTOS task map, dispatch architecture, the command handler
  map, key SRAM variables, the slot state machine, the dryer state machine, the OTA/IAP protocol,
  and the RFID task entry points. **Every address we hook in this repository was found through
  his map.** When we needed `sub_800DEB6`, `sub_800E18C` or the IAP handlers, we knew where to
  look because he had already charted the territory.
- **`feed-check.txt`** — the check_length/error_length analysis.
- **`FEED_OR_ROLLBACK` mode 3 (rollback / unwind assist) — his, not ours.** He shipped
  `FEED_MODE_UNWIND_ASSIST = 3` with a working `unwind_assist` method in `ace2_protocol.py` on
  **2026-08-09**, nineteen days before this repository's first commit. We found the mode
  independently in the disassembly and, until 2026-08-31, wrote it up as though nobody sent it.
  That was wrong and has been corrected in place. What remains ours is the *semantics* — that
  `speed` and `length` are discarded, that **BUF_BACK** is the stop condition, and the admission
  table — not the mode.
- **Two field observations that corrected our own documents**, both from his driver comments:
  `ASSIST_ERROR` arrives **~1 s** after the toolhead stops pulling (we had been foregrounding the
  MCU's 4000 ms limit, wrong by ~4×), and slot status **`ready` means "armed but idle"** because the
  ACE toggles `assisting` ↔ `ready` with the toolhead's pull — so re-arming on `ready` spams
  `start_*_assist` until the device answers `FORBIDDEN`.

His material: <https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9>

His ACE 2 driver: <https://github.com/hakimio/SnapmakerU1-Extended-Firmware/tree/ace-2>
(`overlays/firmware-extended/39-ace-support/`)

A concrete illustration of the debt: we spent a long stretch convinced patched images were being
rejected for their *size*. The actual answer — an 8-byte magic trailer checked by the IAP task —
was found by reading the IAP section of his analysis and following it into the disassembly. The
map was already drawn; we were just slow to read it.

## decay71 — the NTAG WRITE path

**decay71**, author of **multiACE**, took the RC522 passthrough patch
and drove the *write* side of it further than we had, then reported back in the
[gist thread](https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9). Three of the four
things he found we did not know, and two of them fail *silently* — our own write documentation was
incomplete in a way that would have cost the next person hours:

- **`RxCRCEn` must be cleared for the `0xA2` WRITE transceive.** The tag's ACK is 4 bits and
  carries no CRC, so with RX CRC on the reader flags a protocol error and drops the reply. TX CRC
  stays enabled. We had documented "CRC on" as a flat rule.
- **The WRITE ACK is not observable through the tunnel at all.** The tag emits it only after
  ~4 ms of programming, past the transceive's receive window; `op 5` reports `bits = 0x00` and
  `op 4` hands back a stale buffer byte. **The write still succeeds** — he verified by read-back.
  This explains, properly, the meaningless `238` return we recorded for our own `DEADBEEF` write
  and had merely noted as "not a status code".
- **A WRITE leaves the tag's read pointer shifted.** A verify-read of page 4 straight after a
  write returned *page 19's* bytes, deterministically, twice; an `op 6` re-SELECT resets it. We
  had never verified a write without an intervening select, so we never saw this — and it is the
  kind of bug that reads as a successful verify of the wrong data.
- He also **independently reproduced the `op 7` bulk-read dead end**, which is what made us go
  back to the disassembly and find that `op 4`'s 6-bit offset puts most of `op 7`'s output out of
  reach. That is our bug, in our patch, and his report is why it is now documented.

## Bambu-Research-Group — RFID-Tag-Guide

<https://github.com/Bambu-Research-Group/RFID-Tag-Guide>

The Bambu Lab tag key-derivation scheme, the public master key, and the tag block layout are
entirely theirs (`deriveKeys.py`, written by **thekakester** and **Vinyl Da.i'gyu-Kazotetsu**).
Our "Bambu decryption on an ACE" is nothing more than their derivation applied through the ACE's
own reader. We also managed to get their HKDF argument order backwards for several hours — the
fix was reading their source instead of reconstructing it from memory.

## DnG-Crafts — ACE-RFID

<https://github.com/DnG-Crafts/ACE-RFID>

The decoded Anycubic tag page layout (sku, brand, type, ABGR colour, temperatures, diameter,
length). Our raw page dumps agree with their layout byte for byte, which is how we knew our
reads were correct.

## Kobra-S1 / ACEPRO

<https://github.com/Kobra-S1/ACEPRO>

The maintained Klipper driver for the ACE, and the only live option for running an ACE 2 on a
non-Anycubic machine. Our host-side work runs alongside it, and several findings here are bug
reports for it rather than discoveries about the hardware.

## What is original to this repository

To be precise about the boundary, the following were established here:

- That **custom firmware can be built, flashed and run** on an ACE 2 Pro at all.
- **What the IAP task validates** before committing an image: an 8-byte magic trailer at the end
  of the staged image, plus a whole-image CRC — and therefore how to build an acceptable patch.
- **Two silent-`SUCCESS` traps** in the OTA path that make a failed flash look successful.
- **Two bugs in the OTA updater** (a 37-minute stall caused by never returning early, and a
  response-bit filter that makes the tool deaf to the bootloader during recovery).
- That **the bootloader implements the protocol**, so recovery does not need SWD.
- The **UID passthrough** and **RC522 passthrough** patches, and the reader power-on and CRC
  configuration required to make raw frames work.
- **Full Bambu tag decryption performed by the ACE**, and the proof from the access bits that
  **Bambu tags are permanently read-only**.
- That slots 2 and 3 **share one antenna** which can see both bays simultaneously.
- The **assist-mode semantics**: that `speed` and `length` are discarded by the handler (hardcoded
  to unbounded and 50 mm/s), that **BUF_RST stops mode 2 and BUF_BACK stops mode 3**, that both
  assists run unbounded on a strand nothing holds, and the state-admission table for
  `FEED_OR_ROLLBACK`. **The existence of mode 3 is explicitly not on this list** — see above.
- That `assist_error` (0x83) is set by **both buffer switches asserting at once**, which is the
  mechanism behind the ~1 s figure hakimio measured, and that the MCU's 4000 ms limit is a separate
  later backstop rather than the working deadline.

Everything else is someone else's work, gratefully used.
