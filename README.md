# Anycubic ACE 2 Pro — firmware research, custom patches, and RFID findings

Research notes and working firmware patches for the **Anycubic ACE 2 Pro**, produced while
integrating one with a Voron Trident running Klipper.

**Headline result:** the ACE 2 Pro can be made to read *any* NFC tag — including **Bambu Lab
spool tags, fully decrypted** — and to write tags you own. Stock firmware can do neither.

---

## Credit where it is overwhelmingly due

**Nearly all of the foundational work here is [hakimio](https://github.com/hakimio)'s.**

Before his research there was no public understanding of the ACE 2 Pro at all. Every project
that existed (ValgACE, DuckACE, BunnyACE, ACEPROSV08) spoke only to the *first-generation* ACE
Pro — the ACE 2 changed the physical layer (RS-485 instead of USB serial), the baud rate, and
the wire format (Protocol Buffers instead of custom binary frames). None of them could talk to
it. hakimio reverse-engineered the protocol, wrote the interactive shell and the `.proto`
schema, produced the OTA updater, and published a detailed MCU firmware analysis mapping the
FreeRTOS tasks, the frame format, the dispatch architecture, the command handler map, the SRAM
variables, the slot state machine and the IAP protocol.

**This repository stands entirely on that work.** Every address we cite, every handler we hook,
and the very ability to flash anything at all came from his analysis. If you find anything here
useful, go read his material first — it is the primary source:

- [ACE2 firmware analysis, protocol shell, `.proto`, OTA updater, feed-check notes](https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9)
- [hakimio's GitHub](https://github.com/hakimio)

Also essential, and likewise not ours:

- **[Bambu-Research-Group / RFID-Tag-Guide](https://github.com/Bambu-Research-Group/RFID-Tag-Guide)**
  — the public Bambu Lab tag key-derivation scheme and tag layout. Our Bambu decryption is
  simply their `deriveKeys.py` applied through the ACE's own reader.
- **[DnG-Crafts / ACE-RFID](https://github.com/DnG-Crafts/ACE-RFID)** — the decoded Anycubic tag
  page layout.
- **[Kobra-S1 / ACEPRO](https://github.com/Kobra-S1/ACEPRO)** — the maintained Klipper driver
  this work runs alongside.

See [CREDITS.md](CREDITS.md) for a precise breakdown of what is theirs versus what is new here.

---

## What is actually new in this repository

Everything below was established on real hardware (firmware **V1.1.31**), and each item says why
it was worth doing.

| Finding | Why it matters |
|---|---|
| **Custom firmware runs on the ACE 2 Pro** — built, flashed, booted, verified | OTA integrity is CRC-16 only; there is no signature. The device is fully modifiable. |
| **What the IAP task actually validates** — an 8-byte magic trailer plus a whole-image CRC | Explains why naive patches are silently rejected, and tells you how to build one that is accepted. |
| **Two silent-`SUCCESS` traps in the OTA path** | A perfect-looking three-step flash can be a complete no-op. Cost us hours; documented so it costs you none. |
| **Two bugs in the community flasher** | One made every flash take 37 minutes instead of 27 seconds. The other makes a *recoverable* device look bricked. |
| **The bootloader speaks the protocol** | Recovery does **not** require SWD/JTAG, contrary to reasonable assumption. |
| **UID passthrough patch** | Makes the ACE report the UID of any ISO 14443A tag it cannot decode — Bambu, OpenSpool, blank NTAG. |
| **RC522 passthrough patch** | Gives the host full reader control: raw registers, select, page reads, arbitrary frames — i.e. read *and write* arbitrary tags. |
| **Full Bambu tag decryption via the ACE** | Material, type, colour, weight, diameter, temperatures, production date — from the factory tag, read through Anycubic hardware. The authentication and decode run **on the host**, driving the reader through the passthrough; they are not in the firmware. |
| **Bambu tags are permanently read-only** | Proven from the access bits. Settles the "can I rewrite a Bambu tag for a refill?" question: no, and never. |
| **NTAG writing works** | The ACE can write tags you own — something stock firmware cannot do at all. |

Full detail in [docs/](docs/).

### What runs where — this distinction matters

| | in the firmware | on the host |
|---|---|---|
| **UID passthrough** | **yes — self-contained.** Any unmodified host sees `sku` = UID hex, `version 0x0201`, on any tag the ACE cannot decode | — |
| RC522 passthrough (registers, select, page read, arbitrary frames, cache clear) | yes — the mechanism | the driving logic |
| **Bambu authentication and decode** | **no** | yes — key derivation, `MFAuthent`, block reads and parsing all run in Python |
| Tag parking, anticollision handling, NTAG writing | — | yes |

So the ACE is not reading Bambu tags by itself: it acts as a reader that host scripts operate.
A host that does not know about the passthrough (a Snapmaker U1, an Anycubic printer) gets the
**UID** but not the material data.

Moving the authentication and decode **into** the firmware — filling the normal
`FilamentInfoResponse` fields — would make a Bambu spool indistinguishable from an Anycubic one to
*any* host with no host-side changes. That is the obvious next step: roughly 1.5–2.5 KB of code
(SHA-256 dominates) against ~42 KB of free flash, and the required sequence is fully documented in
[docs/04-tag-operations.md](docs/04-tag-operations.md).

---

## Documentation

| Document | Contents |
|---|---|
| [docs/01-firmware-patches.md](docs/01-firmware-patches.md) | What we added to the firmware, why, where it hooks, and the use case for each |
| [docs/02-ota-and-iap.md](docs/02-ota-and-iap.md) | How flashing really works, what the IAP task validates, the silent-success traps, recovery |
| [docs/03-rfid-and-tags.md](docs/03-rfid-and-tags.md) | Tag formats, the acceptance gate, Bambu decryption, write-protection |
| [docs/04-tag-operations.md](docs/04-tag-operations.md) | Practical sequences: parking a tag, anticollision, reading, writing |
| [docs/05-protocol-notes.md](docs/05-protocol-notes.md) | Other protocol findings: feed/rollback modes, buffer gating, the STOP window |
| [FLASHING.md](FLASHING.md) | How to build and flash — **and the risks** |
| [examples/README.md](examples/README.md) | Installing the Klipper extra, and G-code usage |

## Using it

**Start here:** [examples/README.md](examples/README.md) — installing the Klipper extra that
provides `ACE_RAW_CMD`, and G-code examples for reading a tag's UID, clearing a lane's cached
record, selecting and dumping a tag, parking a tag by rotating the lane, and the undocumented
rollback-assist feed mode.

Nothing in this repository works without the extra in `klipper/`, so install that first.

## Contents

- `klipper/` — the Klipper extra providing `ACE_RAW_CMD` / `ACE_RAW_FEED` / `ACE_RAW_STOP`
- `examples/` — G-code usage examples
- `firmware/` — patch sources (ARM Thumb-2) and the build script
- `tools/` — host-side scripts: reader driver, tag parking, key derivation reference, flasher patch
- `data/` — real tag dumps captured during the work

**No Anycubic firmware binaries are distributed here.** The patches are applied to an image you
supply yourself, obtained from your own device's OTA update.

---

## Safety and honesty

- Flashing modified firmware can brick hardware. Recovery over RS-485 works **as long as the
  bootloader is intact and your tooling can hear it** — see [docs/02](docs/02-ota-and-iap.md),
  including the flasher bug that makes a healthy device appear dead.
- The findings here are from **one device** on firmware V1.1.31, with clone silicon
  (`VersionReg` reads `0x18`, not a genuine MFRC522 `0x91`/`0x92`). Your addresses may differ on
  other versions.
- Where something is inferred rather than measured, the documents say so.
- Nothing here defeats a security control: the Bambu key scheme is public, Anycubic tags carry
  no cryptography at all, and the OTA path has no signature to bypass. We could not forge a
  Bambu-signed tag and make no attempt to.

## Licence

Documentation and original code here: MIT (see [LICENSE](LICENSE)). Third-party work referenced
above belongs to its authors under their own terms — please take it from the original sources
rather than from copies.
