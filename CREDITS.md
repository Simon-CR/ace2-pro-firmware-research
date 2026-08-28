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

His material: <https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9>

A concrete illustration of the debt: we spent a long stretch convinced patched images were being
rejected for their *size*. The actual answer — an 8-byte magic trailer checked by the IAP task —
was found by reading the IAP section of his analysis and following it into the disassembly. The
map was already drawn; we were just slow to read it.

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

Everything else is someone else's work, gratefully used.
