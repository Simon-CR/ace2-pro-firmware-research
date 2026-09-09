# ACE2-Open Firmware Changelog

All notable changes to the **ACE2-Open** firmware research and binary patch system are documented in this file.

The firmware uses a non-destructive patch architecture: changes are applied as surgical binary hooks, freestanding Thumb-2 assembly stubs, and freestanding C extensions against the base Anycubic V1.1.31 firmware (`79fb22e7914bae1dc75ac91b30739c19`), preserving the stock bootloader (`0x08000000 - 0x08008000`) and the mandatory 8-byte IAP magic trailer (`61 A5 63 5A 65 A5 32 5A`).

Versions ending in **`O`** (e.g. `V1.1.47O`) designate Open Firmware builds, enabling upstream multiACE auto-detection of extended features.

---

## [V1.1.47O] - 2026-09-09

### BRIEF Summary
* **Dual-Grab Concurrent Loading**: Unlocked feeder gears so an empty lane (e.g. T0) immediately bites and grabs new filament upon photogate insertion, even while an adjacent sibling lane (e.g. T1) is actively printing or assisting.
* **Sibling Busy Lock Removal**: Bypassed the artificial firmware mutex in `sub_800F698` (`0x0800F6F8`) that silently discarded insertion events if the paired lane was in motion.
* **RFID State-8 Busy Lock Removal**: Bypassed the insertion lockout in `sub_800F698` (`0x0800F724`) when the shared RC522 antenna state equals 8.
* **Walk-Away Bite-and-Clamp Integration**: Paired with multiACE's `_insert_grab_and_defer` host logic to limit insertion feed to ~20 mm, firmly clamping the filament tip at the feeder entrance so the human operator can walk away immediately with zero risk of Bowden tube crowding.
* **100% Feature Retention**: Fully preserved native on-chip RFID decoding (OpenSpool, Spoolman, FilaMan, Prusament, Creality CFS, Bambu Lab) from V1.1.46O.

---

### Deep Technical Breakdown (Why & How)

#### 1. The "Why" (Problem Statement)
On stock Anycubic firmware (and V1.1.46O), if Lane 1 was actively feeding, rolling back, or assisting (e.g., during a print), inserting a fresh spool into Lane 0 resulted in complete hardware silence: the drive gears refused to spin, the filament would not bite, and the operator had to stand at the machine babysitting the spool until Lane 1 stopped.

Disassembly of the sole photogate sensor callback in the firmware (`sub_800F698`, registered for EXTI mask `0x1111` across INSERT channels 0, 4, 8, 12) revealed that the hardware DC gearmotors have completely independent PWM timers (`TIM2_CCR1..4`), direction GPIOs, and input capture counters (`TIM4_CH1..4`). The refusal to bite was **100% artificial software logic**:
1. At `0x0800F6AE`, the callback calculates `sibling = slot ^ 1` (pairing Slot 0 with 1, and Slot 2 with 3).
2. At `0x0800F6D4`, it loads the sibling's motion state.
3. At `0x0800F6F8`, if the sibling is `feeding`, `rollback`, `assisting`, or `rollback_assisting` (states 1..4), it branches to the epilogue (`0x0800F6C4`), silently dropping the insertion edge.
4. At `0x0800F724`, if the shared RC522 reader state equals 8, it similarly branches to the exit.

#### 2. The "How" (Binary Patch Details)
Both obstruction checks were neutralized by replacing the conditional branch opcodes with 16-bit Thumb-2 `NOP` instructions (`0xBF00` / `00 bf`):

| Address | Stock Instruction | Patched Instruction | Purpose |
|---|---|---|---|
| `0x0800F6F8` | `bpl.n 0x0800F6C4` (`E4 D5`) | `nop` (`00 BF`) | Bypasses sibling busy check; allows insertion motor trigger |
| `0x0800F724` | `beq.n 0x0800F6C4` (`CE D0`) | `nop` (`00 BF`) | Bypasses RFID busy check; allows insertion motor trigger |

#### 3. Safety & Host Bite-and-Clamp Protocol
Allowing autonomous preload while a sibling is printing introduces a physical hazard: stock preload feeds up to **1700 mm at 50 mm/s** looking for an RFID tag. If unconstrained, Lane 0 would push filament 400+ mm down the Bowden tube directly into the 4-in-1 splitter/hub while Lane 1 is printing.

To eliminate this, V1.1.47O is designed to operate in tandem with multiACE's **Bite-and-Clamp Protocol**:
- When filament is inserted into Slot 0 during an active Slot 1 print, the V1.1.47O MCU starts the feed motor.
- The operator feels the drive gears bite the filament tip (~20 mm).
- multiACE host logic (`_insert_grab_and_defer`) detects the insertion, polls the hardware quadrature encoder, and sends `stop_feed_filament` the instant displacement reaches `INSERT_GRAB_MM = 20` mm.
- Motor 0 halts immediately; the filament tip is clamped firmly at the entrance of the ACE unit (inside the feeder mechanism, 0 mm into the Bowden tube).
- The operator walks away.
- When the print on Lane 1 finishes, multiACE's `_on_print_end` automatically drains the queue, safely completing the transport sweep, RFID decode, and park-datum move for Lane 0.

---

## [V1.1.46O] - 2026-09-09

### BRIEF Summary
* **Native On-Chip Multi-Format RFID Decoding**: Embedded freestanding C decoder (`native_tag_decoder.c`) directly in MCU flash, decoding OpenSpool, Spoolman (`sm_id`), FilaMan, Prusament, Creality CFS, and Bambu Lab tags in **<1 ms** (255 ms roundtrip over serial) with zero host Python tunneling.
* **Dual Hook Architecture**: Integrated `HOOK_RAWTAG` (`0x0800E842`) for live `FILAMENT_IDENTIFY` (cmd 68) and `HOOK_EXTRACT` (`0x0800FE36`) for the autonomous background scanner task.
* **Page-Read Cross-Bay Isolation**: Hooked `HOOK_PAGEREAD` (`0x0800E228`) to eliminate stale buffer memory bleed between antenna-sharing slot pairs (Lanes 0/1 and Lanes 2/3).
* **48-Page Memory Expansion**: Applied 3-byte extend-read pokes (`0x0800E220`, `0x0800E216`, `0x0800E21C`), expanding tag read window from 32 to 48 pages (192 bytes) to capture larger NDEF JSON records.
* **Trailing 'O' Versioning**: Transitioned version string to `V1.1.46O\0` for explicit open-firmware identification and UI feature gating.
* **Automated `patch.json` Distribution**: Emitted portable JSON patch schema containing byte-exact pokes and relocations, consumed directly by pure-Python web flashers.

---

### Deep Technical Breakdown (Why & How)

#### 1. The "Why" (Eliminating Host Tunneling Latency & Memory Bleed)
Stock Anycubic firmware only understood its own proprietary binary RFID layout (magic `123`, version `101`). Reading third-party tags previously required host-side software (multiACE) to execute ~25 raw SPI register transactions over 230,400 baud USB serial per tag, taking **2.5 to 4.5 seconds** per spool and consuming significant host CPU cycles.

Furthermore, because Lanes 0/1 share RC522 Reader 0 and Lanes 2/3 share Reader 1, a failed tag read on an empty slot would leave the previous slot's memory intact in the shared 140-byte page buffer (`0x20000704`), causing the printer to report phantom filaments.

#### 2. The "How" (Freestanding C Decoder & Assembly Hooks)
1. **Decoder Implementation (`native_tag_decoder.c`)**:
   - Compiled as freestanding ARM Thumb-2 code (`-fno-builtin`, `-nostdlib`, `-fPIE`).
   - Implements ultra-fast string parsing for NDEF JSON (OpenSpool keys `type`, `material`, `color`, `min_temp`, `max_temp`, `bed_min_temp`, `bed_max_temp`, and Spoolman `sm_id`).
   - Parses Prusament text blocks (`PRUSAMENT`, material tokens, hex colors, temperature ranges).
   - Parses Creality CFS filament format.
   - Populates Nanopb `FilamentInfoResponse` struct fields directly in MCU RAM.

2. **Assembly Hooks**:
   - **`rawtag_stub.s`** hooked at `0x0800E842` (cmd 68 live query): Displaces `add.w lr, sp, #140`. Calls `decode_cmd68_tag`. If recognized, sets `r0 = 0` and branches directly to `epilogue` (`0x0800E904`), returning the protobuf response in 255 ms.
   - **`rawtag_extract_stub.s`** hooked at `0x0800FE36` (background worker): Displaces `mov r6, r0; cmp r0, #140`. Calls `decode_native_tag`. If recognized, branches to `scan_exit` (`0x0800FE98`), bypassing Anycubic's proprietary parser.
   - **`HOOK_PAGEREAD` (`0x0800E228`)**: Intercepts the page-read epilogue. Clears the target buffer on read failure, preventing stale data from bleeding into adjacent slots.

3. **Extend-Read Pokes**:
   - `0x0800E220`: Changed `cmp r7, #124` to `cmp r7, #172` (expands read loop from 8 iterations / 32 pages to 12 iterations / 48 pages).
   - `0x0800E216` & `0x0800E21C`: Re-targeted branch targets to maintain clean loop exits.

---

## [V1.1.3O] - 2026-08-28

### BRIEF Summary
* **First Open Firmware Release**: Discovered and documented the 8-byte IAP magic trailer validation rule (`61 A5 63 5A 65 A5 32 5A`), enabling safe custom binary flashing without bricking.
* **RC522 Register Passthrough**: Hooked unused command index paths to allow raw host-level SPI register access to the onboard NXP MFRC522 contactless reader ICs.
* **Raw Tag Dump Exposure**: Allowed raw page dumps of high-frequency RFID tags over serial for reverse-engineering.

---

### Deep Technical Breakdown (Why & How)

#### 1. The "Why" (The IAP Validation Mystery)
Early attempts to modify Anycubic ACE 2 Pro firmware resulted in silent rejection by the MCU IAP bootloader. While the flasher reported success, the device never booted the new code.

Disassembly of the IAP commit task at `0x080140B4` revealed two mandatory checks:
1. **Trailing Magic Trailer**: The last 8 bytes of the staged image in flash (`0x08024000 + size - 8`) must match exactly `61 A5 63 5A 65 A5 32 5A`. Any new code must be inserted *before* the trailer:
   ```
   patched = stock[:-8] + custom_code + magic
   ```
2. **Kermit CRC-16**: A standard CRC-16/Kermit (`init=0x0000`, polynomial `0x8408` reversed) must match the checksum announced in `IAP_UPGRADE`.

#### 2. The "How" (RC522 Hook)
Hooked `HOOK_RC522` (`0x0800E7DA`) to map raw register read/write primitives (`rc522_read_reg` at `0x0800F574`, `rc522_write_reg` at `0x0800F5D0`, and `rc522_transceive` at `0x0800F32C`), exposing direct low-level transceive capabilities over USB serial.
