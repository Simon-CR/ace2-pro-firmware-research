# Anycubic ACE 2 Pro — reverse-engineered protocol notes

Findings from running an ACE 2 Pro on a Voron Trident under Klipper, via the Kobra-S1/ACEPRO
driver over RS-485. Everything here was measured on hardware, not read from a datasheet. Where
something is inferred rather than proven, it says so.

Firmware observed: **V1.1.31**. Transport: `/dev/ttyACM0` (CH343 USB-RS485), protobuf-ish
payloads over the ACE2 framing.

---

## 1. `GET_FEED_INFO` (command 76) — undocumented, and the most useful thing here

The driver declares `FeedInfoResponse` and never implements a decoder, so this command's payload
is discarded. It carries **per-slot movement telemetry**.

Response: field 1, **repeated once per slot, in slot order**. Each entry is a sub-message:

| field | type | meaning |
|---|---|---|
| 1 | varint | motor counts for the **last** operation |
| 2 | varint | last operation magnitude, **mm** (matches the commanded value) |
| 3 | varint (two's-complement int64) | **signed delivered movement, mm.** Negative = retract |

### It is a last-operation register, not an odometer

Field 1 does **not** accumulate — it was observed dropping 2643 → 1206 when a smaller move
followed a larger one. Anything not read before the next operation is lost.

### Fields 1 and 2 are the same quantity; field 3 is independent

`f1/f2` is constant across slots and distances, so field 2 is just field 1 converted to mm:

```
f1/f2 = 12.348, 12.351, 12.367, 12.383, 12.400   (~12.37 counts/mm)
```

`f3/f2` is not constant, so field 3 is a **separate physical measurement**:

```
cmd    f2    f3     f3/f2
 30    30   -27     0.900
 45    44   -42     0.955
 60    60   -58     0.967
120   120  -119     0.992
723   723  -733     1.014     <- extruder also pulling
```

### The deficit is a fixed offset, not proportional slip

On a plain retract the shortfall is **1–3 mm regardless of distance**, which is the signature of
backlash / lost motion at direction reversal rather than filament sliding through the gears.
*(Inferred from the constant offset; not independently confirmed against a second sensor.)*

When `f3 > f2` something **else** is moving the filament — on a toolchange park that is the
extruder pulling, so it doubles as confirmation the extruder has grip.

### Practical readings

| observation | meaning |
|---|---|
| `f3 ≈ f2 − 2mm` | normal |
| `f3 ≈ 0` while `f2` is large | gears turning, filament stationary → **grinding** |
| `f3 > f2` | another actuator is pulling (extruder engaged) |
| `f3 << f2` | operation aborted early → jam |

---

## 2. `GET_STATUS` (command 6) — raw field map

The driver decodes fields 1, 2, 3, 4, 7, 8, 9 and then **discards `raw_fields`**, so anything
else is invisible.

| field | meaning |
|---|---|
| 1 | work state: `1` = ready, `2` = busy |
| 2 | dryer sub-message (below) |
| 3 | temperature, °C |
| 4 | humidity, % RH |
| 5 | **unknown**, observed constant `1` |
| 6 | **unknown**, observed constant `1` |
| 7 | `feed_assist_count` — only present during assist |
| 8 | `cont_assist_time`, **milliseconds** on ACE2 (seconds on ACE1) |
| 9 | repeated, one sub-message per slot |

Dryer sub-message: `1` state, `2` target_temp, `3` duration, `4` remain_time.
**`duration` and `remain_time` are SECONDS**, while `DRYING` takes minutes — an easy off-by-60.

Slot sub-message: `1` slot_state, `2` filament_state. At rest only field 2 is present.

### Slot status codes

| code | meaning |
|---|---|
| 0 | ready |
| 1 | feeding |
| 2 | unwinding |
| 3 | `assisting` — feed assist (mode 2) engaged; normal for a loaded lane mid-print |
| 4 | `rollback_assisting` — rollback assist (mode 3); never sent by the driver, measured 2026-08-28 (§5) |
| 5 | preload |
| 6 | upgrading |
| 129–133 | gear error |

### `filament_state` → RFID state

`0` no tag · `2` identified · `3` currently identifying

---

## 3. There is NO spool-rotation telemetry

Worth stating plainly, because the idea is attractive: if you could measure filament length per
spool revolution you would have the circumference, hence the radius, hence remaining filament —
with no initial weight, no tare, no density guess.

**The ACE does not expose it.** Probed: idle `GET_STATUS`, `GET_STATUS` sampled during live
motion, and `GET_FEED_INFO`. Every counter is linear feed-gear travel.

The decisive evidence is the counts-per-mm constant measured on two spools of very different
radius:

```
spool with 491 g remaining   f1/f2 = 12.348
spool with  70 g remaining   f1/f2 = 12.351
```

Those radii differ roughly 2×. If the counter came from the spool motor the ratio would differ by
the same factor. It does not move in the third decimal — it is the feed gear.

Deriving remaining filament in-situ therefore needs **added hardware** (a photo-interrupter and a
mark on the spool flange) or an external scale.

---

## 4. `GET_FILAMENT_INFO` (command 13) — RFID payload

Fields: `1` index, `2` version, `3` sku, `4` type, `5` colors (repeated, packed RGBA),
`6` extruder_temp {min,max,min_speed,max_speed}, `7` hotbed_temp {min,max}, `8` diameter,
`9` total, `10` icon_type, `11` current, `12` code.

### The sku field is capped at 16 bytes

A tag written with an NDEF URI came back as exactly `https://eu.store` — 16 characters, silently
truncated. Any identifier you write to a tag must fit in **16 bytes** to survive.

### There is no tag UID

The payload carries the decoded Anycubic-format fields only. The NFC tag's serial is never
exposed, so an ACE slot **cannot** be matched against an external NFC registry (FilaMan,
Spoolman) by UID. Matching has to go through a short identifier embedded in the sku.

### RFID read behaviour

- The antenna is **fixed in the slot bay**; the tag rides on the spool. A read therefore requires
  **spool rotation** — roughly one revolution, ~600 mm of filament on a full spool.
- `FILAMENT_IDENTIFY` returns FAILED while the device is busy, so reads happen at a standstill.
- The decode is **cached until the slot reads EMPTY**. Swapping the spool on the holder is not
  enough — the filament must leave the lane entrance for the slot to go empty and clear the
  cache. Otherwise the old spool's material, colour and temperature persist against the new one.
- On `empty → ready` with an RFID spool the driver **preserves** the previous metadata rather
  than forcing a re-read.
- A foreign tag on the same spool can block the read entirely: `rfid = 2` (IDENTIFIED) with every
  field empty and version 0.

---

## 5. `FEED_OR_ROLLBACK` (command 8)

Params: `index`, `length`, `speed`, `mode`. The driver only ever sends modes 0 and 1, but the
firmware accepts **four**:

| mode | slot `status_detail` | slot code | sent by | behaviour |
|---|---|---|---|---|
| 0 | `feeding` | 1 | driver `ACE_FEED` | `length` mm forward at `speed` |
| 1 | `unwinding` | 2 | driver `ACE_RETRACT` | `length` mm backward |
| 2 | `assisting` | 3 | driver `ACE_ENABLE_FEED_ASSIST` | open-ended; feeds when the in-line buffer is pulled |
| 3 | `rollback_assisting` | 4 | nobody — driver has the decoder string, no caller | open-ended rewind (accepted, `SUCCESS`) |

*(Corrected 2026-08-28; the first version of these notes said "direction only, no other mode".)*

Mode 3 measured 2026-08-28 with a raw `FEED_OR_ROLLBACK {index:0, speed:10, length:0, mode:3}`:
the device answers `SUCCESS` and the slot reports `rollback_assisting` — first time that state was
seen on hardware.

### Both assist modes are motion commands on a free strand

With nothing holding the strand (bowden disconnected at the outlet, or the extruder idler open),
mode 2 feeds continuously and mode 3 rewinds continuously. Neither waits for a buffer/hall event
when there is no back-pressure. Treat "enable assist" as "run this direction unless the buffer
resists". Still untested: mode 3 with the strand held in the extruder nip — whether it tensions the
buffer and stops (a real retract-assist primitive) or keeps pulling.

### Mode 3 used for a real extraction, 2026-08-28 13:00 — PROVEN

First production-shaped use. Cold lane, tip between post-gear and entry, slack in the bowden.
Armed `FEED_OR_ROLLBACK {index:3, speed:90, length:0, mode:3}` from `ready`, then plain extruder
retracts (5 mm steps at 20 mm/s): entry cleared after 30 mm, slot held `rollback_assisting`
throughout, `STOP_FEED_OR_ROLLBACK` returned it to `ready` at once. No pacing between the two
motors was needed — the ACE only takes up, so the extruder sets the rate.

Notable: the hub encoder registered **0 pulses across the whole 30 mm**, while the toolhead entry
switch went 1 → 0. Slack in the bowden absorbs short extractions entirely, so hub-encoder movement
is not a valid liveness test for a pull at the toolhead; the switches are.

### Mode 3 buffer test, 2026-08-28 11:44–11:46 (bowden off at the outlet, strand free)

Sent `FEED_OR_ROLLBACK mode 3` with the buffer **AT REST** (`back=False`). Device: `SUCCESS`, slot
`rollback_assisting`, device `busy`, assist counter ticking. Then the buffer was pushed by hand toward
the spool side:

| time | buffer (`GET_KEY_STATE`) | slot |
|---|---|---|
| 11:44:31 | AT REST, back=False, chn_buf_feed=False | ready (before arming) |
| 11:44:53 | AT REST | rollback_assisting |
| 11:45:16 | **OFF REST**, back=False, **chn_buf_feed=True** | rollback_assisting |
| 11:45:21 | OFF REST, **back=True**, chn_buf_feed=False | rollback_assisting |
| 11:45:26 | AT REST, back=False | rollback_assisting |
| 11:45:36 | OFF REST, back=False, chn_buf_feed=True (second push) | rollback_assisting |

So under mode 3 the firmware tracks the buffer leaving rest and reaching `back`, and raises
`chn_buf_feed` the moment the buffer leaves rest, with no error state. **Operator observation (PROVEN):** under mode 3 the feeder wheel rewinds *continuously*; it
**stops while the buffer is held at the `back` (inward, spool-side) position and resumes the moment
the buffer is released** back to rest.

So the buffer is a *stop* condition, not a trigger. Mode 3 = "rewind until the strand goes taut
(buffer pulled back), then wait; resume when slack reappears". That is the exact mirror of mode 2,
which reads as "feed until the buffer is compressed (forward), then wait; resume when the extruder
takes filament". Both modes run without limit on a free strand because nothing ever moves the buffer
— which is what the two earlier "runaway" observations were.

Practical meaning: with the strand held in the extruder nip, mode 3 keeps the bowden side taut and
follows every extruder reverse move 1:1 with zero slack build-up — a genuine retract-assist
primitive for cut-retracts, parks and tandem extraction. Admission rule (§8): it can only be entered
from `ready`, so a running feed-assist must be stopped first.

**There is no `STOP_FEED_ASSIST` command on ACE2 — and the "soft stop didn't end mode 3" reading
was wrong.** The ACE2 command table has only `FEED_OR_ROLLBACK` (8), `STOP_FEED_OR_ROLLBACK` (9) and
`UPDATE_SPEED` (10) for motion; the driver's `build_stop_feed_assist_request` emits
`STOP_FEED_OR_ROLLBACK`. At 11:46:03 `ACE_DISABLE_FEED_ASSIST T=0` sent *nothing* (the driver's
`_disable_feed_assist` returns early when its own `_feed_assist_index` is not that slot — assist had
been enabled raw, so the driver thought it was off); the raw `STOP_FEED_OR_ROLLBACK` at 11:46:05 ended
mode 3 immediately (`ready`/`ready`). So one stop command ends every mode, and every "assist disable"
the driver performs is that same command.

Consequence for the ~20–30 s reject window above: it cannot be caused by the command itself, or every
routine assist-disable in a toolchange would trigger it. The remaining candidates are stopping a
*feed/rollback in flight* (decel/settle state) versus stopping an idle assist, or something the
08-27 sequences did around the stop. Open; firmware analysis of the stop primitive (`0x800b1be`) and
the filament task's post-stop state is the way to settle it.

### Mode-2 assist already follows an extruder retract

Bench measurement (T0, nozzle 210 °C, mode-2 assist active): 5 × 5 mm reverse moves at the
extruder gears gave hub-encoder pulses +2/+1/+4/+6/+7 = 18.6 mm through the hub for 25 mm at the
gears — ~7 mm of buffer/slack take-up, then ~1:1. The slot stayed `assisting` throughout (never
`rollback_assisting`), `BUF_BACK` never set, no error. Restoring +25 mm forward gave +13 mm at the
hub (same slack). So a cutter retract or a park pull under mode-2 assist does move the strand at the
tip 1:1 with the gears; state 4 is not what makes it work.

### `STOP_FEED_OR_ROLLBACK` poisons the device for ~20–30 s

After `STOP_FEED_OR_ROLLBACK` the device keeps answering, but feed/retract requests issued in the
next ~20–30 s silently do nothing (seen twice on 2026-08-27; the retracts never moved the strand,
the driver logged connection timeouts). Caution: the driver's "assist disable" is this same command (there is no separate
`STOP_FEED_ASSIST` on ACE2, see below), so the window is not a property of the command itself. Mechanism
open — see §8.

### `FORBIDDEN` (code 2) is not an error

It means **the previous operation is still executing**. On chunked motion it is the normal case,
not an incident. The correct backoff is that move's own duration — a flat multi-second wait costs
an order of magnitude more than the thing it waits for.

Note also that a readiness check against the driver's cached status races the device: the status
cache refreshes at ~1 Hz, so any command issued faster than that can be cleared to send and still
come back FORBIDDEN.

---

## 6. Return Detection Module (RDM)

Board marking `NF031_4TO1_Sensor V1.1`. It sits in the 4-to-1 hub on the **merged** path — it is
not four per-lane switches. Two optical interrupters.

6-pin connector:

| pin | colour | function |
|---|---|---|
| 1 | white | encoder |
| 2 | green | filament detect |
| 3, 4 | — | unused |
| 5 | black | GND |
| 6 | red | +3.3 V |

### Encoder scale

**0.93266 mm per pulse**, measured (100 mm → 105 edges, 200 mm → 212 edges).

Anycubic's published figure of `1.86532` is **per full cycle**. Counting every edge gives exactly
half of it. Using the published number reports double the real distance.

The detect line is a clean analogue swing (0.16 V empty / 3.27 V occupied) and thresholds well as
an ADC input where no spare digital pin exists.

---

## 7. Other machine-specific findings

These are setup-dependent but cost real time to discover.

- **The ACE's autoload is an RFID *search*, not a load to a position.** It stops wherever it finds
  a tag or gives up — observed at 754 mm, 1588 mm, and once 64 s past the hub. Any distance
  measured "from park" inherits that randomness. Defining park at a sensor instead makes it
  repeatable.
- **An undecodable tag makes the search run longer than no tag at all**, because the device keeps
  feeding rather than giving up. That is a lane-collision risk.
- **`SET_FEED_CHECK` (command 19)** is sent during ACE2 setup with `check_length` and
  `error_length` — native jam detection exists and is enabled by the driver, though it logs
  nothing.
- Commands seen in the catalog but unexplored here: `LINEAR_KEY_CALIBRATE` (15), `RFID_TEST` (69,
  takes `{enable: bool}` — a mode toggle, not a probe), `SET_VALVE` (66), `GET_KEY_STATE` (73),
  `FLASH_LED` (70), `SET_OUTPUT` (72).

---

## 8. Firmware static analysis — `ACE2_V1.1.31_20260306.swu` (2026-08-28)

Method: the `.swu` is a password-protected ZIP (ZipCrypto) around `update_swu/setup.tar.gz` →
`ACE2_V1.1.31_20260306.bin` (71 592 B, md5 `79fb22e7914bae1dc75ac91b30739c19`), a plain Cortex-M3
Thumb-2 image, base `0x08008000`, SP `0x20009A80`, reset `0x08008244`, **not encrypted** (entropy
6.6–6.8). STM32F1/GD32F1-class peripherals; FreeRTOS tasks `Command Processing`, `filament move`,
`IAP upgrade`, `OdometerTimer`, `motor1_run`, `Motor2_run`, `dry`, `fan_timer`, `flash led`.
Disassembled with objdump + radare2 against hakimio's function map (575 functions, 26-entry protobuf
dispatch table at `0x08018C5C`; all 33 registered command IDs match his table). Artifacts:
`ace2-fw-analysis/` (slices per function, full objdump, scripts, strings, entropy). Only the
scripts and findings are for publication — the extracted image is Anycubic's.

### 8.1 `FEED_OR_ROLLBACK` (cmd 8) handler `0x0800B578`

- **PROVEN** — validation: `index ≤ 3`, `speed ≤ 100`, `mode ≤ 3`, else PARAM_ERROR (1). Hakimio
  lists the four modes in the enum but not the on-device bounds.
- **PROVEN** — busy check: a context lookup (`0x0800B474`, reads `[0x200012E8+20]`/`[+32]` under
  the command mutex) returning 0 → FORBIDDEN (2): "previous operation still in flight".
- **STRONG** — admission by slot state (read from `0x20001000 + 4·index + 4`):

  | requested mode | admitted when slot state is | otherwise |
  |---|---|---|
  | 0 feed | `0 ready`, `3 assisting`, any error state (≥127) | FORBIDDEN |
  | 1 rollback | `0 ready`, `4 rollback_assisting`, any error state | FORBIDDEN |
  | 2 / 3 assist | `0 ready` or any error state | FORBIDDEN |

- **STRONG** — the four modes collapse to a 2-bit motor op: bit 0 = direction, and the assist modes
  enqueue the same op as their one-shot twin via `0x0800B22C` plus a persistent assist flag
  (`[0x20001BA0 + 64·slot + 60] = 1`). Slot state 4 is produced by the runner at `0x0800A6E6`
  (`movs r1,#2 / it ne / movne r1,#4`) and consumed by STOP and GET_STATUS — mode 3 is a
  first-class state the driver simply never uses.

### 8.2 Buffer gating of the assist modes (runner `0x0800A0B0`, 10 ms cycle)

- **PROVEN** — the runner reads three key sources per tick: `CHN_BUF_FEED` (KeyIndex 16, flag at
  `0x20001A55`), a per-slot **BUF_BACK** index from table `0x08019570 = {3,7,11,15}`, and a per-slot
  **BUF_RST** index from table `0x08019560 = {2,6,10,14}`; active flags live at
  `0x20001A14 + 4·KeyIndex + 1`. Indices are literal in the image and match hakimio's `KeyIndex`
  enum exactly.
- **PROVEN on hardware (11:45) + consistent with the code** — **BUF_RST stops mode 2, BUF_BACK stops
  mode 3**, both mediated by `CHN_BUF_FEED`. Mode 2 feeds until the buffer is driven forward; mode 3
  rewinds until the buffer reaches the spool-side stop; both resume when the buffer returns to rest.
  Hakimio maps the key names and the `LINEAR_KEY_CALIBRATE` targets but never links them to motion.
- **PROVEN** — `GET_SENSOR_STATE`/`GET_KEY_STATE` (cmd 73, handler `0x0800FC4C`) returns a 17-bit
  mask, bit n = KeyIndex n active.

### 8.3 `STOP_FEED_OR_ROLLBACK` (cmd 9) — one opcode, two behaviours, no timer

- **PROVEN** — there is exactly one stop opcode. The driver's "stop feed", "stop unwind" and "stop
  feed-assist" builders are byte-identical `STOP_FEED_OR_ROLLBACK {index}`; the firmware registers
  nothing else for stopping. No mode/direction parameter exists.
- **PROVEN** — STOP core `0x0800B1BE` branches on slot state: `1/2` (feeding/unwinding) and
  `129/130` (their error twins) → `0x0800B200`: clear busy `[node+6]=0` **and call the motor
  vtable stop**; `3/4` (assisting/rollback-assisting) → write state 0 and return, **no motor-stop
  call**; `5/132` (preload) → `0x0800AF84`.
- **PROVEN** — no timestamp or countdown is written anywhere on the stop path; the ~20–30 s reject
  window is not a deliberate lockout.
- **HYPOTHESIS (the agent's, and the best one we have)** — the window is residual motion: stopping an
  *assist* only clears the flag and leaves the runner/motor to finish its current commanded move,
  and the motion-wait loop `0x0800F03C` re-arms a 3000-tick wait as long as the encoder keeps
  changing; meanwhile `0x0800B474` reports in-flight → FORBIDDEN. Prediction: the window after
  stopping a bounded feed/retract is short; after stopping an *actively moving* assist it is long;
  after stopping an assist that is idle (buffer at its stop) there is none — which is why routine
  toolchange disables never showed it. Bench steps B3/B7 in the state-machine doc measure this.

### 8.4 `UPDATE_SPEED` (cmd 10) handler `0x0800B4A0`

**STRONG** — `index ≤ 3`, `speed ≤ 100`; non-zero speed → stored as float into the per-slot
motor object (+32) and the motor is re-kicked (`(obj,6)`,`(obj,1)`); speed 0 → stores 0 and calls
`(obj,5)`,`(obj,2)` (a stop-like path). The driver uses it as `_change_feed_speed` /
`_change_retract_speed`. Whether it re-paces a running assist (state 3/4) is untested.

### 8.5 Command inventory and enums

**PROVEN** — registered IDs: 0–20, 64, 65, 66, 68, 70, 71, 72, 73, 75, 76, 77, 78 (33), all matching
hakimio's handler map (e.g. FEED_OR_ROLLBACK → `0x0800B579`, STOP → `0x0800B645`). Slot states 0–6
and 129–135 and RespCode SUCCESS/PARAM_ERROR(1)/FORBIDDEN(2)/FAILED(3) byte-exact against
`ace2-pro.proto`. `SET_FEED_CHECK` bounds (`check_length ∈ [3,254]`, `error_length ∈ [3,
check_length]`, stored at `0x20000096/97`) confirm hakimio's feed-check gist. Naming: the driver's
`GET_FILAMENT_INFO` (13) / `GET_KEY_STATE` (73) are hakimio's `GET_RFID_CACHE` /
`GET_SENSOR_STATE` — same opcodes; the firmware evidence supports hakimio's names.

### 8.6 The RFID "lock" — reader, gate, and what a foreign tag would need

*(Rewritten after the scoping pass; two earlier claims — a "default template at `0x08018BF8`" and
a "block-type byte 6" gate — were the agent's own misreads and are withdrawn: the block is a NaN
float initialiser plus the version string, and the `cmp #6` is a per-slot poll-state byte.)*

- **PROVEN** — the reader is a generic MFRC522-class ISO 14443A front end. `0x0800DEB6` programs
  classic RC522 registers (TxControl `0x14`, ModWidth `0x24`, RFCfg gain `0x26 ← 0x48`, …) and runs
  the standard anticollision/SELECT cascade (SEL `0x93/0x95/0x97`, NVB `0x20` → `0x0800DC50`, NVB
  `0x70` → `0x0800DBA2`, UID BCC check). It will enumerate any compliant tag.
- **PROVEN** — the only PICC commands the firmware ever emits are anticollision/SELECT, HALT
  (`0x50`) and **READ `0x30`**: the bulk reader `0x0800E18C` reads **pages 4–39 (144 bytes)** in
  16-byte replies into `0x20000704`. This is the NTAG21x/Ultralight path and matches the
  DnG-Crafts/ACE-RFID layout (NTAG213/215, ≥36 pages).
- **PROVEN** — **no MIFARE Classic authentication exists.** The transceive helper `0x0800F32C`
  never writes CommandReg `0x0E` (MFAuthent), there is no 6-byte key load, and a whole-image sweep
  finds no SHA-256 K-table/IV words, no HMAC/HKDF, no `RFID-A`/`RFID-B` labels, and no Bambu master
  key. **This is the 2026-08-20 `READFAILED (6)` on a Bambu spool:** the MIFARE Classic 1K selects
  fine (UID needs no crypto), then the first unauthenticated READ is NAKed.
- **PROVEN** — the accept gate is the **page-4 header**. DnG's `7B 00 65 00` decodes as two
  little-endian u16: **`123` = magic**, **`101` = version** — the same "version 101" observed on a
  genuine tag through the driver. The RFID cache task `0x0800EA14` compares the header against
  `123` and against `456`, where `456` is the firmware's own "already validated" sentinel it writes
  back into the slot record; anything else is cleared. So hakimio's "magic 123/456 validation"
  is half right: `123` is the external gate, `456` is internal state. `GET_FILAMENT_INFO` cmd 13
  serves the cache; **cmd 68 (live read) has no magic gate** — it returns whatever pages 4–39
  hold, with `version = page4[2:4]`.
- **PROVEN** — the internal `FilamentInfoResponse` record built by `0x0800E7A8` (cmd 68) /
  `0x0800E910` (cmd 13): `+0` index · `+4` u16 version (page 4) · `+8` sku (19 B internal, pages
  5–8; proto caps at 16) · `+28` type (pages 15–18) · `+52` colours RGBA (page 20) · `+92/+96`
  extruder temps (page 24) · `+112/+116` hotbed temps, diameter, length (pages 29/30) · **`+140`
  code — `0` is what makes the host show `rfid = identified`.**
- **PROVEN (handler read + live codes 2026-08-28)** — `FILAMENT_IDENTIFY` (cmd 68, `0x0800E7A8`)
  result codes: `1` index ≥ 4 · `3 FAILED` no card answered the select (`0x0800DEB6` ≠ 0) ·
  `4 ANTICOLLISION` (`== 2`) · `6 READFAILED` page read returned < 140 bytes · `0` decoded. The
  handler picks the reader with `(index << 1) & ~2`: **slots 0/1 share reader 0, slots 2/3 share
  reader 1** — the same pairing as the driver's `SET_RFID_ENABLE index 0 / index 2`. Live, with the
  device idle: index 0 → `SUCCESS`, index 3 (spool in bay, no filament) → `FAILED` ×350, but index 1
  → `PARAM_ERROR` and index 2 → `FORBIDDEN` (persisting across a Klipper restart and an explicit
  re-enable) — neither code is produced inside the handler, so a dispatcher-level pre-check exists;
  being traced. Until then, "identify returns FORBIDDEN" must not be read as "reader busy".
- **PROVEN** — `GET_KEY_STATE` mask observed `0x4555` with filament in slots 0–2 and all buffers at
  rest = bits 0/4/8 (INSERT ch1–3) + bits 2/6/10/14 (BUF_RST ch1–4): matches the KeyIndex tables in
  §8.2 (INSERT = 4n, BUF_RST = 4n+2, BUF_BACK = 4n+3, CHN_BUF_FEED = 16).
- **PROVEN** — the identity cache outlives the spool: with slot 3 `empty`, `GET_FILAMENT_INFO` still
  returned the previous spool's record (`SM25`, PETG, version 101). The driver hides it (rfid=False
  on an empty slot) but the firmware does not clear it on eject.
- **PROVEN — the UID is captured and then thrown away.** The cascade stores the 4/7-byte UID at
  scratch `+13..+19` (SAK/ATQA at `+20/+21`, `0x0800E0F2`); inside cmd 68 that is `sp+17..sp+23`.
  The response builder copies only page data from `sp+28` onward. **There is no UID field in the
  protocol and the bytes are never read again** — nothing host-side can recover it.

**Driver bug found 2026-08-28 (PR-worthy):** the ACEPRO driver declares `FILAMENT_IDENTIFY`
(cmd 68) as returning a `GenericResponse`, so it decodes proto field 1 as the result code. The
firmware answers with a `FilamentInfoResponse` where field 1 is the **index** and field 12 is the
code, so the driver reports back the index it sent — index 1 reads as "PARAM_ERROR", 2 as
"FORBIDDEN", 3 as "FAILED", and 0 as "SUCCESS" only because proto3 omits zero scalars. Every
per-slot conclusion drawn from cmd 68 before this fix is void. Fixed locally by declaring the
response type correctly and decoding it like cmd 13 (`protocol_ace2.py`, backup `.bak-cmd68`).

**Bambu tag, measured after the fix (2026-08-28 12:51):** a full `MMU_LOAD` of the Bambu spool in
lane 3 — 2500 mm of feed, so the spool rotated many times past the antenna — produced **no
identification at all**: slot 3 stays `rfid False`, the driver never logs an RFID detect, and cmd 13
returns an empty record (`version 0`, no sku). Stationary identify polls return `FAILED` (nothing
answered the select) on every slot including ones with known-good Anycubic tags, so a static poll
proves nothing — the tag must be sweeping the antenna at the moment of the read. Distinguishing
`FAILED` from `READFAILED` (the 2026-08-20 result, which would prove the tag engages and only the
read is refused) needs identify polled *during* rotation. Open.

**`FILAMENT_IDENTIFY` (cmd 68) cannot be used as a stationary diagnostic — PROVEN 2026-08-28.**
192 live identify calls over 45 s of hand-rotation on a spool with a known-good Anycubic tag
returned `FAILED` every time; so did every call on both readers with `rfid_sync_enabled: True` and
`SET_RFID_ENABLE` explicitly re-sent for reader 0 and reader 1. The tag is only inside the antenna
field while it sweeps past, and at rest it stops wherever it stops. Polling *during* a move does not
work either: Klipper's gcode queue is serial, so identify calls issued while a macro runs are queued
behind it (one sample got through in a 17 s park). This also **retracts the earlier note that
identify "always returns FAILED while the ACE is moving, so reads must happen at a standstill"** —
it fails at a standstill too. What actually decodes tags is the ACE's own background RFID task
during a feed, which then caches the result.

**The firmware's cache is separate from the driver's view, and only a *failed search* clears it.**
The driver reports `rfid False` / `sku None` as soon as a slot reads `empty` (that is what the panel
shows), but the firmware's own record survives: after `MMU_EJECT` on lane 2 completed, cmd 13 still
returned `version 101 sku SM24`. Toggling `SET_RFID_ENABLE` off and on does not clear it either.
What writes the record is the tag search the ACE runs on insertion: a successful search writes the
decoded tag, a failed one writes an **empty** record (`version 0`, `sku ''`). That is why lane 3
held `SM25`/PETG at 12:18 and was empty by 12:41 — the Bambu spool went in, the search ran, and it
found nothing. **An empty firmware record is therefore positive evidence of "searched, decoded
nothing", not merely a missing value.**

**Bambu tag, final measurement 2026-08-28:** with the Bambu spool in lane 3, a 2500 mm load (many
spool revolutions past the antenna) followed by a 723 mm park produced **no decode at all** — slot 3
stayed `rfid False` with an empty cache record throughout. This is what the disassembly predicts:
there is no MIFARE Classic authentication anywhere in the image (§8.6), so a Classic 1K tag can be
selected but never read. **Reader health confound removed 2026-08-28 13:11 (PROVEN):** lane 2 (same reader 1 as
lane 3) was ejected and its Anycubic-tagged spool reinserted by hand; the ACE's background search
decoded it in ~7 s of preload rotation — `Slot 2 RFID detected -> sku=SM24, PLA, RGB(247,217,89)`.
So reader 1 reads tags reliably, and the Bambu spool's failure on that same reader, through far more
rotation, is the tag, not the hardware. Combined with the crypto-absence proof in §8.6: **Bambu
MIFARE Classic tags cannot be read by this firmware by construction.** The UID-passthrough plan is
unaffected either way — the UID is returned by the anticollision cascade before any read or auth,
for any ISO 14443A tag.

**UID-passthrough patch (scoped, not built):**

| item | finding |
|---|---|
| hook | `0x0800E7A8` after the cascade returns success (`0x0800E81A`): hex-format `sp+17..` into `sku` (`r4+8`), write a distinct `version` (`r4+4`, e.g. `0x0201`) so the host can tell UID-tags from Anycubic tags, force `code = 0` (`r4+140`), skip the page decode. Optional one-instruction relax of the `123` gate in `0x0800EA14` for the cache path. |
| size | ~150–400 B; one `bl`/`b.w` branch-patch, no vector-table change |
| free flash | app ends `0x080197A8`, OTA staging base `0x08024000` → **43 096 B free** |
| integrity | OTA is host-computed CRC-16/Kermit, no signature, no anti-rollback → recompute CRC, re-zip with the known password, flash |
| host side | driver already returns `sku` and `identified` when `code == 0`; add "version 0x0201 → look up FilaMan by `rfid_uid`" |
| works on | every ISO 14443A tag: Bambu (UID only), OpenSpool/FilaMan NTAG21x, blank NTAG |

**Full Bambu decode** (HKDF-SHA256 from the UID + MFAuthent per sector + 7 authenticated block
reads + field mapping): ~1.5–2.5 KB, fits trivially, but adds crypto to get right; only worth it
for spool details the host doesn't already hold. Order of preference in a patched parser:
Anycubic layout → (optional) Bambu decode → UID passthrough for everything else.

**Preload runaway on a foreign tag — corrected:** the MCU *does* cap tag-read retries
(`0x0800E244`: `≤49` / `≤200` bounds, then slot → idle). The continuous feed we measured on a Bambu
tag (2026-08-20) is the **host** autoload search that keeps feeding until a tag decodes. Fix is
host-side (bound the search / treat READFAILED as stop), or moot once UID passthrough makes every
tag "decode" at once.

**Brick risk (STRONG):** the 32 KB bootloader at `0x08000000–0x08008000` is not in the image (only
its version string at `0x08007800` is referenced). All RS-485 OTA opcodes are registered by the
*application's* IAP task; chunks stage at `0x08024000` and the bootloader copies them down on
reboot. So: a patched app that boots far enough to run the IAP task is always recoverable by
re-flashing the stock `.swu`; one that faults earlier needs **SWD** (no RDP/option-byte writes seen,
so SWD and the ROM system bootloader via BOOT0 should both be open). Rule for a first flash: stub
behind the existing success branch, off the boot path; SWD probe attached; verify `GET_INFO` /
`IAP_VERSION` answer immediately after boot before trusting RS-485 recovery.

### 8.8 Custom firmware runs on the ACE 2 Pro — PROVEN 2026-08-28

**A modified application image was built, flashed over RS-485, and booted.** The device reported
our own version string (`V1.1.3U` instead of `V1.1.31`) from `GET_INFO`, with `flags 0x81`
(application, not bootloader), `status ready`, temp/humidity live and the per-slot RFID cache
intact. Nothing about the ACE is locked: OTA integrity is CRC-16/Kermit only, and there is no
signature to defeat.

**Hard constraint found empirically: the image must stay exactly 71 592 bytes.** Two attempts at a
71 656-byte image (stock + a 64-byte appended stub) failed to take — the first left the previous
application running, the second left the device parked in the bootloader (`status upgrading`). A
same-size image differing by one character committed and booted first time. The mechanism is not
understood: the bootloader cannot plausibly reject on absolute size (future Anycubic releases will
differ in size), no self-referential size or CRC field was found in the image, and the byte pattern
at the end (`55 AA AA AA AA 00 … 61 A5 63 5A 65 A5 32 5A`) sits inside what looks like a data table
rather than a clean trailer. **Open question**; the useful next probes are (a) an older official
image to compare sizes and tails against, and (b) a dump of the bootloader itself.

**The bootloader implements the protocol — recovery does NOT need SWD.** This overturns the earlier
assessment. In IAP mode the device answers `GET_INFO` with `version` = the string the host announced
in `IAP_UPGRADE`, **`boot_version = V1.0.2`** (the application returns this field empty), and
`status = upgrading`. A stock re-flash from that state restored the application immediately.

**Two bugs in hakimio's `ace2-ota-update.py`, both fixed in our copy:**
1. **The flash took 37 minutes for no reason.** `send_recv` looped until its full timeout even after
   the device had answered, so each of ~1119 chunks burned the whole 2.0 s `T_CHUNK`. Returning on
   the ack cuts a full flash to **26.5 s** measured. (We kept a deliberate 15 ms pause per chunk in
   case the MCU acks before its flash write settles.)
2. **The flasher is deaf to the bootloader.** It filters replies on the `0x80` response bit, but the
   bootloader answers with `flags = 1` — so the recovery tool reports "No response … is the ACE
   connected and powered?" exactly when it is needed for recovery. Matching on the command id fixes
   it. This is the single most dangerous of the two: it makes a recoverable device look bricked.

**Also confirmed:** the flasher's post-flash "waiting for expected version" check compares against
the string passed to `--version`, so any image with a different internal version string reports a
spurious timeout after a successful flash. Harmless, but alarming if you don't expect it.

### 8.10 UID passthrough WORKS — a Bambu tag identified on an ACE 2 Pro (2026-08-28)

**Result:** with the patched firmware live (`V1.1.3M`), polling `FILAMENT_IDENTIFY` (cmd 68) through
a reinsertion of a Bambu spool in lane 3 returned:

```
code 0 SUCCESS   version 513 (0x0201)   sku '899334FCD20000'
```

24 successes in 227 samples (the rest `FAILED`, i.e. the tag not in the antenna field at that
instant). `899334FC` is the 4-byte MIFARE Classic UID, `D2` the BCC, trailing zeros the unused tail
of the 7-byte UID buffer. Genuine Anycubic tags continue to decode normally on the same firmware.

**The patch:** 4 bytes at `0x0800E836` (the `READFAILED` exit of the cmd 68 handler) become
`b.w` to a 64-byte stub that hex-formats the UID from the anticollision scratch buffer (`sp+17`)
into `sku`, writes `version = 0x0201` as a "this sku is a raw tag UID" sentinel, sets `code = 0`
and rejoins the handler epilogue. Anycubic tags never reach that exit, so they are unaffected.

### 8.15 Bambu tags are PERMANENTLY read-only -- writing them is impossible (2026-08-28)

After authenticating successfully, MIFARE `WRITE` (0xA0) to an all-zero data block was **NAKed**
(both phases returned 0x04, not the 0x0A ACK). The sector trailers explain it:

```
trailer (every sector): 00000000 0000 87 87 87 69 000000000000
  data blocks   C1C2C3 = 010  -> read: KeyA|B, WRITE: NEVER
  trailer block C1C2C3 = 101  -> access bits themselves: WRITE: NEVER
```

`010` makes every data block permanently read-only, and because the trailer is *also* write-locked
the access bits can never be restored to a writable state -- not with KeyB, not with any key, not
by Bambu themselves. This is not a key or protocol problem; the tags are factory-sealed.

**Consequence:** rewriting a Bambu tag (e.g. to re-purpose a spool for a refill) is impossible.
**UID-keying is the only viable route** for Bambu spools, which is what the §8.10 passthrough
provides. Writing remains available for tags we own (NTAG, §8.13).

**Full Bambu tag content read (sectors 0-5), for reference:**

```
blk  1  "G00-K00" / "GFG00"          material variant id + filament id
blk  2  "PETG"                        filament type
blk  4  "PETG Basic"                  detailed type
blk  5  000000ff e8030000 0000e03f    colour RGBA, 1000 g spool, 1.75 mm (float)
blk  6  41 00 08 00 ... 04 01 e6 00   temperatures
blk  8  ... cd cc 4c 3e               float 0.2
blk  9  tray/serial                   
blk 12  "2026_05_02_08_50"            production timestamp
blk 13  "26_05_02_08"
blk 14  4a 01 -> 330                  length (m)
blk 16  02 00 01 00
blk 17,18,20,21,22  all zero          unused
```

### 8.16 Operating rules for any tag routine (from Simon)

1. **Never act while `code 4` (ANTICOLLISION) is possible.** Slots 2 and 3 share one antenna that
   can see both bays' tags at once. The sequence must be: rotate the *other* lane's tag clear ->
   confirm the reader sees nothing or sees the intended UID -> park the target tag -> act. Skipping
   this invalidated an entire round of testing (an NTAG was authenticated as if it were the Bambu
   tag, which can never succeed).
2. **A write macro must offer to flip the spool** and repeat, because each face carries a different
   tag with a different UID. Writing identical payload to both faces makes the spool
   orientation-independent -- and avoids needing multi-UID support in the backend.
3. Restore lane positions afterwards, and do all tag work while parked -- restoring the lane moves
   the tag back out of the field.

### 8.14 FULL BAMBU TAG DECRYPTION on an ACE 2 Pro (2026-08-28) -- PROVEN

A genuine Bambu Lab MIFARE Classic 1K spool tag was authenticated and decrypted **by the ACE
itself**, using UID-derived keys, through the RC522 passthrough:

```
UID 899334FC   ->  sector 0 KeyA A62EB420CE32   Status2 = 0x08 (MFCrypto1On)
  block 0: 899334fc d2 08 0400 05e1d653c3c8bd90   UID, BCC, SAK 08, ATQA 0004
  block 1: "G00-K00"  "GFG00"                     material variant id + filament id
                sector 1 KeyA EB22C585C318
  block 4: "PETG Basic"                           detailed filament type
  block 5: 000000ff e8030000 0000e03f             colour RGBA (black), 1000 g spool,
                                                  diameter 1.75 as an IEEE-754 float
```

**Key derivation (the thing that was wrong for hours):** per
`Bambu-Research-Group/RFID-Tag-Guide` `deriveKeys.py`:
`HKDF(uid, 6, master, SHA256, 16, context=b"RFID-A ")`. In PyCryptodome's signature that is
`HKDF(ikm, key_len, salt, hash, num_keys, context)` -- so **the UID is the IKM and the master key
`9a759cf2c4f7caff222cb9769b41bc96` is the SALT.** Having them swapped produces entirely different,
useless keys while failing silently (auth just returns with the crypto bit clear). Verify against
the upstream script rather than reconstructing the scheme from memory.

**Sequence that works**, end to end:
1. Park the tag: rotate the lane in small steps (raw async FEED_OR_ROLLBACK), probe with cmd 68
   after each, stop when it answers. **Slots 2 and 3 share ONE antenna that can see both bays'
   tags at once** (an ANTICOLLISION result, code 4, is the giveaway), so the other lane's tag must
   be rotated clear first or there is no way to know which tag replied.
2. op 6 SELECT (powers the reader: GPIO write from `readerObj+44/+48` + 20 ms settle, then
   `sub_800DEB6`).
3. Set `BitFramingReg(0x0D)=0`, `TxModeReg(0x12) |= 0x80`, `RxModeReg(0x13) |= 0x80` (CRC on).
4. Stage `0x60 | block | key[6] | uid[4]` and transceive with RC522 command **0x0E (MFAuthent)**.
5. Read `Status2Reg (0x08)`: **bit 3 set = authenticated**.
6. Read blocks with a plain `0x30 | block` transceive -- the RC522 handles Crypto1 transparently.

**What this means:** the ACE can now read Anycubic tags (natively), any ISO 14443A tag's UID (§8.10),
raw NTAG pages, and now full Bambu spool data -- material, variant, colour, weight and diameter --
none of which the OEM firmware can do.

### 8.13 Full tag READ and WRITE working from the ACE (2026-08-28) -- PROVEN

Firmware `V1.1.3W` adds, on the cmd 68 `index >= 4` dead path: op 6 SELECT (`sub_800DEB6`),
op 7 bulk page read (`sub_800E18C`), op 8 clear cached record. **The missing piece for op 6 was
powering the reader first** -- the handler does a GPIO write from `readerObj+44/+48` plus a 20 ms
settle (`0x0800E7F2`) before selecting; without it select always returned 1.

**And the reason raw frames never got a reply:** every firmware transceive is preceded by
(from the HALT routine `0x0800E108`) `BitFramingReg(0x0D)=0`, `TxModeReg(0x12) |= 0x80`,
`RxModeReg(0x13) |= 0x80`, `Status2Reg(0x08) &= ~0x08`. CRC generation/checking was **off**, and
frames were being sent with a manually appended CRC -- malformed twice over. With CRC enabled and
no manual CRC bytes, arbitrary frames work. (Also: the helper's `r1` really is the RC522 command --
HALT uses `Transmit` 4, not `Transceive` 12, because no reply is expected.)

**Full read of a real tag (lane 2, Anycubic-format NTAG, 7-byte UID 880434CC744FC9):**

```
pages  4-7   7b 00 65 00 | "SM24"       magic 123, version 101, sku
pages  8-12  "Bambu Lab"                brand
pages 12-15  "PLA"                      material
page  20     ff 59 d9 f7                colour ABGR -> #F7D959
page  24     c8 00 dc 00                extruder 200 / 220 C
pages 29-31  32 00 3c 00 | af 00 4f 01  bed 50 / 60 C, 1.75 mm, 335 m
```

Byte-for-byte agreement with the DnG-Crafts layout and with what the ACE itself reports. Dump kept
at `ace2-fw-analysis/t2_tag_dump.json`.

**WRITE PROVEN.** NTAG `WRITE` (0xA2) through the same path: page 38 (verified all-zero and unused)
`00000000` -> wrote `DEADBEEF` -> read back `deadbeef` -> restored `00000000`. Note that writing
*identical* bytes and reading them back proves nothing; only a changed-and-verified value does.
The helper's return (238) is not a status code -- the write took effect regardless.

**So the ACE can now read and write arbitrary ISO 14443A tags** -- something the OEM firmware
cannot do at all. Remaining for the Bambu decode: `MFAuthent` (RC522 command 0x0E) with the
UID-derived keys, which needs a selected card -- now available via op 6.

**Tag parking** is the enabling primitive: rotate the lane in small steps with raw async
FEED_OR_ROLLBACK, probe with cmd 68 after each, stop when it answers (15-510 mm observed; a 1 kg
spool revolution is ~600 mm of filament). Do ALL tag work while parked -- restoring the lane moves
the tag back out of the field.

### 8.12 RC522 passthrough — register access PROVEN, RF layer still needs the firmware's own init

Firmware `V1.1.3T` carries three stubs' worth of capability, all hooked into dead paths of the
cmd 68 handler so normal identify is untouched:
- `0x0800E836` (READFAILED exit) -> UID passthrough (§8.10).
- `0x0800E7DA` (the `index >= 4` rejection, dead because there are only four slots) -> an RC522
  passthrough. The sub-command is packed into the request's uint32 index:
  `bit31=1 | bit24=reader | op<<16 | arg1<<8 | arg2`, and the result comes back in the response's
  `code` field. ops: 0 read reg, 1 write reg, 2 stage a TX byte, 3 transceive, 4 read an RX byte,
  5 rx bit length. Staging buffer is the firmware's own tag-page buffer at `0x20000704`
  (+0 TX, +64 RX, +128 bit length).

**PROVEN:** full RC522 register read/write from the host. Writes verified by flushing the FIFO,
pushing `A5 5A 3C` and reading back both `FIFOLevelReg = 3` and the exact bytes; a plain register
write/readback (`TReloadRegH`) also round-trips. The register map is **standard MFRC522**
(`ModeReg 0x3D`, `TxASKReg 0x40` Force100ASK, `TModeReg 0x80` TAuto, `TxControlReg 0x83` antenna
on) even though `VersionReg` reads `0x18` rather than a genuine chip's `0x91/0x92` — i.e. a clone
with a standard layout.

**NOT working yet: getting the card to answer.** Every attempt — hand-rolled `REQA`/`WUPA`/`READ`
sequences, and frames pushed through the firmware's own transceive helper `0x0800F32C` — transmits
(`ComIrqReg` shows TxIRq + IdleIRq) but never receives (no RxIRq, `FIFOLevel = 0`, `ErrorReg = 0`).
The RX area only ever holds leftovers from the firmware's own reads.

**Why (the lesson):** `0x0800F32C` is only the transceive *step*. Every successful firmware read
wraps it in `sub_800DEB6`, which performs the reset, antenna cycling and analog configuration
first. Calling the inner helper without that surrounding init is still hand-rolling the hard part.
**Next build should expose the higher-level primitives instead** — `sub_800DEB6` (full select) and
`sub_800E18C` (bulk page read, pages 4-39 into `0x20000704`) — which are proven to work because
they are what makes normal identify succeed. A selected card is also the precondition for
`MFAuthent`, so this is the prerequisite for the Bambu decode as well.

**Cached-tag record located** (for a "clear cache" op, requested because stale cache repeatedly
confused measurements): per-slot record at **`0x20000054 + slot * 164`**, with `version` (u16) at
`+286`, `sku[19]` at `+288`, `type[19]` at `+328` and colours at `+348` — read by the cmd 13
handler `0x0800E910`. Zeroing those makes cmd 13 report an empty record, which is the genuine
"searched and decoded nothing" state.

**Tag parking works** and is the enabling primitive for all of this: rotate the lane in small steps
(raw async `FEED_OR_ROLLBACK` so the gcode queue stays free), probe with cmd 68 after each step,
stop when it answers. Measured on lane 2: the tag came into the field after 15-150 mm. A full spool
revolution is ~600 mm of filament for a 1 kg spool, not the ~250 mm first assumed. Once parked the
spool is stationary and there is no time pressure — the reader also stays powered for at least
7.85 s after a scan.

### 8.11 What the IAP task actually validates — the real reason patched images were rejected

Two failed attempts were blamed on image size. **Wrong.** The IAP task (`0x08013FB8` region) checks,
at `0x080140B4` onward:

1. **A magic signature in the LAST 8 BYTES of the staged image**, byte by byte:
   `61 A5 63 5A 65 A5 32 5A`, read at `staging_base + announced_size - 8 .. -1`. Any mismatch
   branches to the reject path at `0x080141D8`.
2. **A checksum over the whole staged image** (`0x08010464` called with the staging base and the
   announced size) compared against the 16-bit CRC the host announced in `IAP_UPGRADE`.

Appending a stub *after* the image pushed the magic out of the final 8 bytes, so the image was
rejected and never committed. The fix is to insert new code **before** the trailer and keep those
8 bytes last; size is then irrelevant, which is consistent with Anycubic shipping releases of
differing sizes. Layout that works: `stock[:-8] + stub + magic`.

**Two silent-success traps that made this hard to diagnose, both worth publishing:**
- `IAP_UPGRADE` (`0x0800D4E4`) begins `ldrb r2,[r5,#24]; cbnz r2, skip` — **if a previous OTA is
  still pending (state byte non-zero) the whole handler is skipped and the result code stays 0**.
  The host sees `SUCCESS` while the device stored nothing. A failed attempt therefore poisons every
  later attempt until the device is power-cycled.
- `IAP_UPGRADE_FINISH` (`0x0800D5C4`) arms the commit **only if the state byte is exactly 2**, and
  returns 0 either way. So a complete-looking three-step flash can be a total no-op.

**Procedure that reliably works:** power-cycle the ACE (clears the OTA state), flash, and verify by
reading back a version string embedded in the image — never trust the tool's success report. Note
also that the flasher's post-flash check compares against `--version`, so a custom version string
always produces a spurious timeout after a *successful* flash.

### 8.9 The Bambu tag IS selected — only the read is refused (PROVEN 2026-08-28)

Polling `FILAMENT_IDENTIFY` at maximum rate through a manual eject/reinsert of the Bambu spool in
lane 3 produced **14 × `READFAILED` (code 6)** among 259 samples. Code 6 is reached only after the
ISO 14443A select has succeeded, i.e. the anticollision cascade completed and **the tag's UID was
read off the wire** — only the subsequent NTAG page read was refused, exactly as expected for a
MIFARE Classic tag against a firmware with no `MFAuthent` path (§8.6).

This is the positive result the UID-passthrough patch depends on: the UID exists in the device's
scratch buffer at that moment and is simply discarded. It also settles the earlier ambiguity —
the failure is *not* that the tag never enters the antenna field. (Static polling with the spool at
rest is useless: the tag is only in the field while sweeping past, so reads must be sampled during
rotation, and Klipper's serial gcode queue means the rotation must be driven by asynchronous raw ACE
commands rather than a macro.)

**Still to do:** fit the UID stub into the image without growing it. MIFARE Classic 1K UIDs are
4 bytes, so a raw copy into an existing numeric response field needs ~20 bytes rather than the 64
the hex-string version used — and repurposing the body of a cosmetic handler (`FLASH_LED`, cmd 70)
gives room without changing the image length at all.

### 8.7 What a full Ghidra pass would add

Load raw at `0x08008000`, Cortex-M/Thumb, STM32F103 SVD; seed from the dispatch table. The
nanopb request/response descriptors (`0x08018F78`, `0x080190A4`, …) are the high-value target —
typing them recovers every field number without guessing. ~1 day for the filament subsystem, 3–4
for an annotated full pass.

### 8.17 The spool and the feed gears share ONE motor (PROVEN 2026-08-29)

Settles a question the command set alone could not: there is no spool-rotation command anywhere
in the registered set (0-20, 64-66, 68, 70-73, 75-78), yet the ACE plainly winds a spool back in.
It does so because the spool and the feed gears are driven by the SAME motor through a permanent
coupling -- four PWM channels, four encoders, four input captures, four lanes, one motor each.

Proven by commanding a rollback on a lane with NO filament in the gears (tip detached and secured
to the spool by hand, lane reporting `empty`) and watching the spool. It turned.

`GET_FEED_INFO` (cmd 76) separates the two measurements cleanly, which is the useful part:

| condition | `magnitude_mm` | `moved_mm` | `motor_counts` | counts/mm |
|---|---|---|---|---|
| filament in gears, 149 mm commanded | 149 | -151 / -153 / -148 | 1852 | 12.43 |
| NO filament, 299 mm commanded | 299 | **0** | 3699 | 12.37 |

So:

* **`magnitude_mm` and `motor_counts` measure the MOTOR.** They track the commanded distance
  whether or not filament is present, at a constant ~12.4 counts/mm.
* **`moved_mm` measures the FILAMENT**, from a separate sensor, and it is SIGNED (negative for
  rollback). With nothing in the gears it reads exactly 0 while the motor turns 299 mm.

These are the two inputs of the feed-check slip comparator, and this is a direct empirical
confirmation of the pair: the comparator is watching motor-vs-filament divergence, and an empty
lane is the maximal-divergence case. Note the divergence did NOT raise an error here -- the lane
reported `status_code 2` (busy) throughout and returned to 0 -- consistent with the native jam
detection being present but never switched on by the driver (see 8.15).

Practical consequence: a spool can be rotated for drying with the filament detached, using an
ordinary rollback on an empty lane. No filament is consumed and none is at risk, because there is
none in the path. This is what makes 'spin' mode in `klipper/ace_dryroll.cfg` viable, and it is
strictly better than Anycubic's own `auto_roll`, which nudges 5 mm every ~4 minutes and slowly
walks the filament backwards while barely rotating the spool.

It also explains a long-standing symptom: a spool of the wrong size or excessive drag causes
feeding problems, because the feed gears are fighting the spool's inertia and friction through a
fixed mechanical ratio with no clutch. The firmware measuring spool circumference (accepting
180-600 mm) is part of the same design.

### 8.18 The NTAG WRITE path, and why op 7 is a dead end (2026-08-31)

Four findings from **decay71** (author of multiACE), reported in the gist thread
<https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9> after running our RC522
passthrough (`V1.1.3O`). Three were new to us; the fourth sent us back to our own disassembly.

- **PROVEN (decay71)** — **`RxCRCEn` must be cleared for the `0xA2` WRITE transceive.** The tag's
  reply is a **4-bit ACK carrying no CRC**, so with RX CRC enabled the reader raises a protocol
  error and drops it. `TxCRCEn` stays set — the `0xA2` frame still needs its CRC_A. This is an
  exception to §8.13's flat "CRC on" rule, which was derived from the READ path only.
- **PROVEN (decay71)** — **the WRITE ACK is not observable through the passthrough at all.** The
  tag emits it only after ~4 ms of internal programming, past the transceive's receive window.
  `op 5` reports `bits = 0x00` and `op 4` returns a **stale byte from the previous frame**. The
  write nonetheless **succeeds**, verified by read-back. This is the proper explanation for the
  meaningless `238` we recorded on our own `DEADBEEF` write in §8.13. **Rule: verify every page or
  4-page chunk by reading it back; never branch on the ACK.**
- **PROVEN (decay71)** — **a WRITE leaves the tag's read pointer shifted.** A verify-read of page 4
  immediately after a write returned **page 19's** bytes, deterministically, twice. An `op 6`
  re-SELECT between the write and the read resets it. Correct sequence is
  `WRITE → op 6 SELECT → READ`. Failure is silent, not an error.
- **op 7 (bulk page read) returns success but its payload is unreachable — our bug.** decay71
  observed a constant `0x90` for every `arg1`/`arg2`, with `sku`, `tag` and the unparsed fields
  empty. Verdict from disassembling `firmware/patch.json`'s appended code at `0x0801985A`:

  - `op 7` **works**. `0x90` is `144` — the byte count for pages 4–39, i.e. the documented success
    value, not a status or an echo.
  - It takes **no arguments**: there is no `ubfx` on the op-7 path, so the constant return across
    every argument is expected.
  - The payload **never crosses the protobuf boundary.** Every op returns one byte in `code`
    (`str r0,[r4,#140]` in the shared epilogue at `0x0800E904`); nothing in the RC522 stub writes
    `sku` or any other response field. Only the *UID* stub does, on the normal identify path.
  - The real defect: `op 7` writes 144 bytes at `BUF+0`, while `op 4` reads `BUF+64+arg1` with
    `arg1` masked to **6 bits** (`ubfx r1, r5, #8, #6`). So only dump bytes 64…127 — **pages
    20…35** — are addressable. Pages 4…19 (magic, version, sku, brand, material) and 36…39 are not.
  - Side effects of the same overrun: the 144-byte write **destroys the staged TX frame**
    (`BUF+0..63`) and **overwrites the bit-length byte at `BUF+128`**, so an `op 5` after an `op 7`
    returns page 36 byte 0.
  - We never used `op 7` ourselves either — the §8.13 dump and `data/anycubic_ntag_dump.json` were
    taken with per-page `0x30` transceives, which is the only complete path. Our docs implied
    otherwise and have been corrected.
  - **Fix for a future build:** bits 15..14 of the packed word are unused, so add an `op 9` reading
    `BUF + ubfx(r5, 8, 8)` (absolute 0…255 offset). Two instructions, and it leaves `op 4` and its
    `op 3` callers alone.

  Non-invasive confirmation that `op 7` does transfer data: after `op 6` then `op 7`, `op 4` with
  `arg1 = 0..3` returns **page 20** — on an Anycubic tag, the ABGR colour word (`ff 59 d9 f7` on
  ours).

**Version letters are cosmetic.** `V1.1.3O`, `V1.1.3T`, `V1.1.3W` are all the same base image
(`ACE2_V1.1.31_20260306`, md5 `79fb22e7914bae1dc75ac91b30739c19`, 71592 bytes) with one byte poked
at `0x08018C26` in the version string. Hook addresses and helper addresses are identical across
them; only the stub *contents* differ by build, and `V1.1.3O` is the released one (ops 0–8).
`V1.1.3T` predates ops 6–8 and has no `op 7` at all.

## 9. Buffer signals as seen from Klipper

- `GET_KEY_STATE` bits used by the driver: `INSERT`, `EMPTY`, `BUF_RST`, `BUF_BACK`; plus
  `CHN_BUF_FEED`. Within ~1 s of a manual push at the hub inlet the buffer reported `OFF REST`,
  `back=False`, `chn_buf_feed=True`.
- The assist counter in the status payload increments once per second while assist is enabled
  **regardless of feeder motion** — it is a loop cycle count, not a motion count. Do not use it to
  infer that the feeder turned.

## Changelog

- 2026-08-21 — first version (published as `docs/protocol-notes.md` in Simon-CR/ace2-voron).
- 2026-08-28 — §5 corrected (four modes; mode 3 measured, buffer-gated stop confirmed by hand test; there is no STOP_FEED_ASSIST on ACE2 — assist disable is STOP_FEED_OR_ROLLBACK); assist-follows-retract measurement;
  STOP reject window; §8 firmware static analysis (modes, buffer gating, STOP internals, UPDATE_SPEED, command inventory, RFID lock, OTA has no signature); §9 buffer signals.

## Method

Raw payloads were obtained with the driver's own `ACE_DEBUG METHOD=<name>` command, which prints
`raw_fields` before the driver strips them, then decoded with a minimal protobuf varint reader.
Arbitrary `FEED_OR_ROLLBACK` modes were sent with a small Klipper extra (`ACE_RAW_FEED T= MODE=
[SPEED=] [LENGTH=]`, `ACE_RAW_STOP T=`) that builds the request through the driver's own protocol
object.
Movement figures were cross-checked against a hub-mounted optical encoder and against Klipper's
`print_stats.filament_used`.

Where a claim rests on a single observation it is marked as inferred. The counts-per-mm and
mm-per-pulse constants are specific to this hardware revision and worth re-measuring.


## 2026-09-01 — V1.1.3W fails OPEN on foreign tags (undocumented build)

The machine has been running **`V1.1.3W`** and no build record exists for it. Documented builds are
`V1.1.3O`, `V1.1.3U`, `V1.1.3M` (UID passthrough). W is none of them.

**Observed with an OpenSpool NDEF tag, live:**

```
Slot 1 RFID full data -> sku=application/json{", temp=28770°C (min=26719, max=30821),
                         color=RGB(58,34,101), hotbed={'min': 8804, 'max': 8762}, brand=
```

Stock returns `READFAILED` for a tag that fails the page-4 header gate. UID passthrough returns the
UID in `sku`. **W returns `code 0` plus the raw NDEF payload run through the positional Anycubic
parser** — `application/json{"` is the NDEF MIME record header landing in the SKU field.

So W appears to bypass or fail-open the page-4 header gate WITHOUT the UID formatting stub. That is
the worst of the three: a `READFAILED` is honest, a UID is useful, garbage-as-success is neither.

**It reached three consumers before anything caught it:** the pre-toolchange heat target (which
reads the lane's RFID temp — 28770 offered as a setpoint), the gcode parser (embedded double quotes
terminated `RESPOND MSG="..."` → `Malformed command`), and the operator panel.

**Two tells that prove a positional misparse, either alone sufficient:** an inverted hotbed range
(min 8804 > max 8762), and a nozzle temp far outside any hotend's range. Host-side gate added in
`instance.py:_handle_rfid_info_response` — rejects the WHOLE decode (a wrong-offset parse means
every field is meaningless, including ones that happen to look sane) and treats the lane as untagged.

**Open:** dump the running image and diff against stock `V1.1.31` and the `M` build. A device
running an unidentified image is not a base to patch from.

### CORRECTION, same day — W is identified, and the OpenSpool prediction was wrong

The entry above said no build record exists for `V1.1.3W`. It exists in the 2026-08-28 session
history, just not in the research repo:

```
19:32  Built: UID stub preserved at 0x080197A0, new 170-byte RC522 stub at 0x080197E0,
       magic intact, CRC 0x833C
19:50  1.1.3W flashed
```

**`V1.1.3W` = UID stub + RC522 passthrough** (the one that calls the firmware's own transceive
routine at `0x0800F32C`, rather than hand-driving the RF layer as `V1.1.3R` tried). Successor to R.
Tag writing should work on this build.

**So why does an OpenSpool tag still return garbage when the UID stub is present?** Because the
stub hooks exactly ONE exit: `READFAILED` (code 6) — "card selected but the read was refused",
which is what MIFARE crypto produces on a Bambu tag. An OpenSpool tag's pages read fine; it fails
later, at the **page-4 header gate**, a different exit that falls through to the positional parser.

`03-rfid-and-tags.md` asserted "both land on the UID path, which is why UID-keying covers every case".
**That was a prediction, never measured, and hardware disproves it.** Table row corrected.

**Fix:** a second 4-byte branch from the format-gate exit into the same 64-byte stub at
`0x080197A0`. No new stub, no size growth beyond the branch - the same edit shape that committed
first time for the read path.
