# Flashing, the IAP task, and how not to lose an afternoon

Everything here was learned the hard way on firmware V1.1.31. The three-step OTA sequence itself
(`IAP_UPGRADE` → `IAP_FIRMWARE` → `IAP_UPGRADE_FINISH`) is documented in hakimio's analysis; this
document covers what the *device* does with it, which is where the traps are.

## Memory map

| Region | Purpose |
|---|---|
| `0x08000000 – 0x08008000` | Bootloader (32 KB), reports `V1.0.2`. Never written by OTA. |
| `0x08008000 – 0x08024000` | Application (~114 KB available; V1.1.31 uses 71 592 bytes) |
| `0x08024000 –` | OTA staging area — chunks land here first |

Nothing overwrites the running application until the commit, which is why a failed *transfer* is
harmless.

## What the IAP task actually validates

This is the single most useful thing in this document. Before committing a staged image, the IAP
task checks, at `0x080140B4` onward:

1. **An 8-byte magic signature in the LAST 8 BYTES of the staged image**, compared byte by byte
   at `staging_base + announced_size - 8`:

   ```
   61 A5 63 5A 65 A5 32 5A
   ```

   Any mismatch branches to the reject path at `0x080141D8`.

2. **A checksum over the whole staged image** (computed by `0x08010464` over the announced size)
   compared against the 16-bit CRC the host announced in `IAP_UPGRADE`.

**Consequence for patching:** you may grow the image freely, but the magic must remain the final
8 bytes. Append your code *before* the trailer:

```
patched = stock[:-8] + your_code + magic
```

We wasted a long time believing the bootloader rejected images by *size*, which never made sense
(Anycubic's own releases differ in size). It was always the magic being displaced.

## Two silent-`SUCCESS` traps

Both of these let a flash report a flawless three-step success while the device does nothing.

**Trap 1 — `IAP_UPGRADE` ignores you if an OTA is already pending.** The handler at `0x0800D4E4`
begins:

```
ldrb r2,[r5,#24]      ; OTA state byte at 0x20001554
cbnz r2, skip         ; if non-zero, skip the ENTIRE handler
...                   ; (store size, CRC, version; set state = 1)
skip: movs r0,#1      ; ...and return, having stored nothing
```

The result code was already set to 0 at entry, so the host sees `SUCCESS`. **One failed attempt
therefore poisons every later attempt** until the state clears.

**Trap 2 — `IAP_UPGRADE_FINISH` only arms the commit if the state byte is exactly 2**
(`0x0800D5C4`), and returns 0 either way.

### The procedure that works

1. **Power-cycle the ACE before flashing.** This clears the OTA state byte. A *successful* flash
   reboots the device by itself and needs no manual cycle — the power cycle is for recovering
   from a failed one.
2. Flash.
3. **Verify by reading back a version string embedded in your image** — never trust the tool's
   success report. This is why our builds set `V1.1.3O` rather than keeping `V1.1.31`: it makes
   "did it actually commit?" answerable.

Note that a stock re-flash is *unfalsifiable*: if it silently fails, the device still reports the
same version and looks perfect. Use a marked image if you need certainty.

## Two bugs in the OTA updater

Both are in hakimio's `ace2-ota-update.py`. They are small fixes to an otherwise excellent tool —
`tools/ota_updater.patch` contains them, to be applied to his original rather than shipped as a
fork.

**Bug 1 — flashes take 37 minutes instead of 27 seconds.** `send_recv` loops until its full
timeout even after the device has answered:

```python
deadline = time.time() + timeout
while time.time() < deadline:
    ...collect matching responses...     # never breaks early
return results
```

With `T_CHUNK = 2.0` and ~1119 chunks that is exactly 37 minutes, almost all of it idle.
Returning as soon as a matching reply arrives gives a **26.5 s** measured flash. (We keep a
deliberate 15 ms pause per chunk in case the MCU acks before its flash write settles.)

**Bug 2 — the tool is deaf to the bootloader.** It filters replies on the `0x80` response bit,
but **the bootloader answers with `flags = 1`**. So during recovery — exactly when you need it —
it reports *"No response. ACE may not be connected or initialised."* and a perfectly healthy
device looks bricked. Match on the command id instead.

This second one is the dangerous one. We hit it twice and briefly believed we had destroyed the
unit.

## Recovery does not need SWD

Contrary to the reasonable assumption that only the application implements IAP, **the bootloader
speaks the protocol**. In IAP mode `GET_INFO` returns:

```
version      = the string the host announced in IAP_UPGRADE
boot_version = V1.0.2        (the application returns this field empty)
status       = upgrading
```

Re-flashing a stock image from that state restores the device immediately. Combined with the fact
that OTA never writes below `0x08008000`, the bootloader is very hard to lose.

The residual risk is a patched application that faults *before* the IAP task registers its
commands — then there is no RS-485 receiver and SWD is the only way back. Keep patches off the
boot path (all of ours hook a single command handler, which cannot run during boot).

## Flash endurance

STM32F1/GD32F1-class flash is rated **10 000 erase/write cycles per page**, and each OTA rewrites
the whole application region — so the count is simply "number of flashes", with no wear-levelling
benefit. Roughly ten flashes is 0.1% of budget. Iterate freely; this is not a practical limit.

Caveat: this is clone silicon, so treat the datasheet figure as optimistic.

## No password is needed to flash

The `.swu` password protects the ZIP that Anycubic distributes; it is only needed to **extract**
a base image. The flasher takes a raw `.bin` directly, so once you have an image no password is
involved. This repository ships no Anycubic firmware and no passwords — only patch bytes that
apply to an image you obtain yourself.
