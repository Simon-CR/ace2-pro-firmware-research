# Dryer safety, actuators, NVM, and the rest of the command set

This chapter covers the subsystems outside filament handling and RFID. It leads with the dryer
because that is hardware which runs unattended for hours with filament inside, and because two
commands in the protocol will defeat every protection it has.

---

## 1. Dryer safety — the verdict

**The normal drying path (`DRYING`, cmd 11) is genuinely well protected. It is safe to run
unattended.** It has a hard temperature ceiling, dual redundant thermistors with a cross-check, a
heater-failure timeout, a hardware fault input, and a duration timer that always ends in a full
shutdown.

> ## WARNING — two commands bypass all of it: `SET_DRY_POWER` (65) and `SET_PTC_TEMP` (75)
>
> Both are marked "debug" in the ACEPRO driver and nothing in normal operation sends them. If you
> are hand-poking the protocol, **do not send either.** They can leave the heater running with no
> temperature reference, no timer, and no code path that will ever switch it off.

### What the sanctioned path enforces (all PROVEN)

| Protection | Detail |
|---|---|
| **Accepted temperature** | **15-65 C inclusive, nothing else** (`sub_800D8AC`). Exactly 51 accepted values; anything outside sets the PID target to NaN, which disables the heater. |
| **Maximum actually driven** | setpoint + 8 C, i.e. **73 C worst case** (`0x0800D948`, clamped at `0x0800D3DE`). The command limit and the drive limit agree. |
| **Control law** | Cascade, not bang-bang. Inner PID (Kp 1.0, Ki 0.01, Kd 5.0) on the heater-surface NTCs every 2 s; outer loop trims +/-2 C at most once per 10 min from the AHT20 chamber sensor, and only when the chamber is stable to 0.8 C. PID output creeps toward a **90 % duty cap** - it never commands 100 %. |
| **Redundant sensing** | Two thermistors; **the hotter one governs** (`max(NTC0, NTC1)`). |
| **Sensor faults** | ADC valid window 100-3200 mV (`sub_800D978`), roughly -37 C to +131 C. A short (~0 mV) and an open (~3300 mV) both read NaN, as does anything implausible. Held for up to 5 bad samples, then latched, giving `ntc_error` and shutdown. The window doubles as a **~131 C over-temperature backstop**. |
| **Thermal runaway** | Two independent mechanisms: a **cross-thermistor slope check** (immediate fault at 0.7 C/s disagreement, or 10 consecutive cycles above 0.2 C/s), and a **5-minute no-rise timeout** while below 30 C. |
| **Hardware fault input** | GPIOD15 polled while heating; asserted means PC9 is pulsed 200 ms as a latch reset, 3 events per cycle allowed, then forced stop. (Mechanism PROVEN; that it is a thermal cut-out is inference.) |
| **End of cycle** | State 3 **always** runs the full shutdown: duty 0, target NaN, both fans off, flaps cycled. |

### Known gaps in the sanctioned path

* **No hard maximum duration.** `duration` is `minutes x 60` with no cap. The heater does always
  stop at the end of whatever duration you set.
* **No comms-loss timeout.** If the host disappears mid-cycle the dryer runs to its own timer.
  Nothing in the UART task touches the dryer state. This is defensible - the cycle still
  terminates - but it is worth knowing.
* **No watchdog on the dry task.** The IWDG is never enabled anywhere in the image; the single
  `0xAAAA` reload sits inline in the Command Processing task, so even a hardware-started watchdog
  would be fed by the comms task while the dry task was hung.
* **No fan/heater interlock.** `SET_FAN` writes the fan channels with no dryer-state check, so a
  host can switch the fans off mid-heat and the heater will not notice. There is no tach feedback.
  The only backstop is thermal: no airflow means the NTCs read hotter, the PID backs off, and past
  ~131 C the sensor reads NaN and (setpoint permitting) shuts down.
* **`ptc_error` and `ntc_error` latch permanently.** Once in state 4 or 5, a new `DRYING` is
  **acknowledged as success and silently does nothing**. Only a power cycle clears it.
* **Re-sending `DRYING` mid-cycle** changes temperature and duration but **not** the start
  timestamp - so `DRYING(60, 240)` sent three hours in expires immediately.
* **Neither cmd 11 nor cmd 12 reports rejection**: both always return `code = 0`. A rejected
  temperature is only visible via `GET_STATUS` / `GET_TEMP`.

### Why the two debug commands are dangerous

`SET_DRY_POWER` (`sub_800BE9C`) clamps its argument to 0-100 and writes it straight to the heater
triac duty at `0x20001CA0`, which the zero-cross ISR at `0x08008BA0` acts on directly. There is no
state check, no dryer gate, no temperature reference. The dry task only overwrites that duty when
its PID runs, and the PID is skipped whenever the target is NaN - which is exactly the state a
*stopped* dryer sits in. So `SET_DRY_POWER(100)` with the dryer stopped energises the heater at
full duty, fans off, no timer, and nothing will ever turn it off.

`SET_PTC_TEMP` (`sub_800BE0A`) installs a PID target from a raw uint32 with **no upper bound** -
its only check is an inf/NaN test that cannot fail - and sets the setpoint to 0. That matters
because the fault-shutdown body is guarded by `setpoint > 0` (`0x0800C910`): on the `setpoint <= 0`
path, states 4 and 5 fall through **without shutting down**. So `DRYING(...)` followed by
`SET_PTC_TEMP(x)` leaves the unit heating with the thermistor protections disarmed.

### The undocumented `auto_roll` field

`DryingRequest` field 3 is `auto_roll`. With it set, the ACE **rotates the spools by itself every
~4 minutes** during drying once a sensor is within 20 C of target - it internally calls the cmd 8
handler with `{slot, length 5, speed 18, mode 1}`. **Important if a lane is threaded through to the
toolhead.** The ACEPRO driver sets it to `False`, so this is off in normal use.

The dry cycle also **vents once per cycle** (flaps cycled), triggered when chamber humidity stops
falling or after 2 hours, whichever comes first.

---

## 2. NVM layout, and a flash-wear hazard

```
0x08006800 - 0x08006FFF   settings, BACKUP   (1064 bytes + CRC16 at 0x08006FFC)
0x08007000 - 0x080077FF   settings, PRIMARY  (1064 bytes + CRC16 at 0x080077FC)
0x08007800 - 0x08007FFF   bootloader info (boot_version string)
0x08008000 - 0x080197A8   application
```

The record is a byte-for-byte image of SRAM `0x20000054 .. 0x2000047B`. **So `0x20000054` is the
persistent settings block** - not "RFID cached data" as previously labelled. It holds the four
analog key calibration pairs, `check_length`/`error_length`, the printer-status byte, per-slot
filament state, the per-slot 164-byte RFID/material records, and material names for indices 0 and 1.

> ### WARNING - do not poll `SET_SLOT_STATUS` or `SET_MATERIAL_NAME`
>
> A commit is **two full 2 KB page erases plus two 1064-byte programs**, with interrupts masked for
> ~40 ms. There is no journal, no wear levelling and no compare-before-write, and **both copies are
> erased in the same commit**, so the redundancy does not help - they wear together.
>
> Worse, these two commands set the debounce counter to 200 while the flush threshold is 151 - the
> "debounce" is pre-loaded past its own trigger, so **every call commits within ~10 ms, even when
> the value is unchanged.** STM32F1 endurance is ~10 000 cycles per page. A macro that re-asserts
> slot status on every toolchange will destroy the settings pages.
>
> `SET_PRINTER_STATUS` is change-gated and is the only one of the three safe to call repeatedly.
>
> The ACEPRO driver does not send either command, so this is a hazard for hand-written tooling.

---

## 3. `SET_PRINTER_STATUS` (20) — what it actually does

Effectively a boolean (the boot normaliser clamps anything > 1 to 1). It does **not** change feed
behaviour, assist, motor speeds, error handling or any timeout - exhaustive enumeration found one
writer, one boot normaliser and five readers.

What it gates is **RFID re-read policy and length reporting**: with status non-zero, filament
movement events queue an RFID re-read and a length report to the host, and the remaining-length
recompute runs. With status 0 those are suppressed.

Practical consequence of never sending it: the ACE stops re-reading tags on filament motion, so
spool identification and remaining-length tracking degrade. Nothing safety-related changes.

---

## 4. `LINEAR_KEY_CALIBRATE` (15) — narrower than documented, and permanent

**Only `id` in {0, 4, 8, 12} is accepted** - the other 13 `KeyIndex` values return `PARAM_ERROR`,
because they are plain digital inputs with nothing to calibrate. So this calibrates the **four
per-channel analog filament-insert detectors** only.

One command captures **one instantaneous ADC sample** and installs it as one of two endpoints
(defaults 600 mV and 1100 mV). No averaging, no settling, no state gate - it is accepted mid-print.

> **WARNING - it persists across reboot, power cycle and OTA.** A wrong-but-not-degenerate
> calibration (say 2400 mV and 3000 mV, taught with no filament present) passes the boot sanity
> check (`|A-B| > 99`) and that channel will misreport filament presence **forever**. There is no
> reset-to-defaults command. Recovery is either a correct re-calibration, or deliberately
> collapsing the pair to `|A-B| <= 99` and rebooting, which restores the defaults.

Read them back with cmd 78 - which is **not** motor status: it dumps 16 `{u32,u32}` pairs straight
from the settings image, and is the only getter for these thresholds.

---

## 5. Actuator map

`sub_800F0CC(chan_bit, value, use_cache)` takes a **single bit**, not a mask:

| bit | GPIO | function |
|---|---|---|
| 0x01 / 0x02 / 0x04 / 0x08 | PE13 / PE9 / PC15 / PE5 (active low) | slot 0-3 LEDs |
| 0x10 / 0x20 | PE12 / PE8 (active high) | **dryer fan 1 / fan 2** |
| 0x40 / 0x80 | PD3+PD4 / PD5+PD6 (2-bit) | **motorised flap H-bridges** (0 coast, 1 dir A, 2 dir B, 3 brake) |

* **`SET_FAN` (71)** - `{speed, fan1, fan2}`. 0 = off, 100 = hard on, else software PWM in 20 %
  steps. **Bug: in PWM mode both channels are driven unconditionally - `fan1`/`fan2` are ignored.**
  Second bug: `speed = 0` does not stop a running PWM timer, so the fans return on the next tick.
* **`SET_VALVE` (66)** - drives the flap H-bridges with a **150 ms auto-stop watchdog**.
* **`SET_OUTPUT` (72)** - a raw `(channel_mask, value)` GPIO write with no bounds, and it **never
  arms that watchdog**, so `SET_OUTPUT(0xC0, 1)` stalls both flap motors indefinitely. It cannot
  reach any filament motor.
* **`FLASH_LED` (70)** - a self-deleting task, no bounds. `components` may include 0x40/0x80,
  giving a second uninterlocked path to the flap motors; and while it runs it holds a suppression
  mask that makes `SET_FAN` and `SET_VALVE` on those channels **silent no-ops**, with no cancel.

---

## 6. Multi-unit addressing — the "SEQ" byte is a bus address

Frame byte 3 is a **static device address**, compared for equality and never incremented. The CRC16
covers `ADDR + TYPE + CMD + LEN + PAYLOAD`; replies OR in bit 7. The 2-byte TYPE field is an opaque
request id, echoed verbatim and never inspected.

* **`DISCOVER` (0)** returns the three STM32 UID words from `0x1FFFF7E8`.
* **`ASSIGN_DEVICE_ID` (1)** takes `{uid1, uid2, uid3, device_id}`; on UID mismatch it returns code
  1 **and transmits nothing at all**. Only the low byte is used, and bit 7 must stay clear.
* **Daisy-chaining is deliberate**: DISCOVER replies get a **random 0-40 unit back-off** (seeded
  from the UID) while every other command replies at a fixed 5 - a collision back-off that only
  makes sense with multiple unassigned units answering address 0.
* **Nothing is persisted.** The address is 0 after every power cycle and enumeration must be
  repeated.

---

## 7. Corrections and cautions for the command set

* **`SET_FEED_CHECK` (19) bounds are `check_length` in [3, 254], `error_length` in [3, 254] and
  `error_length <= check_length`.** The widely-quoted tuning suggestion of `check=80, error=90`
  **would be rejected**, because 90 > 80.
* **`MOTOR_TEST` (77) moves real filament motors with no print interlock.** It validates index,
  speed and mode but leaves `length` unbounded, never reads printer status, and permits every error
  state >= 0x80 - so it will drive a lane the firmware has already declared jammed or tangled.
  During a print the three idle lanes are all movable. Bench command only.
* **`GET_TEMP` (64) has seven float fields, not six** - fields 1 and 2 are hard-coded zero, so
  existing published names are shifted by one. Field 7 silently substitutes sensor 1's value when
  sensor 2 reads NaN, masking a sensor fault at the reporting layer.
* **`GET_STATUS` (6)** has nine fields; the dryer submessage is **omitted entirely** when the dryer
  is idle. `cont_assist_time` is in **milliseconds**. The driver ignores fields 5 and 6
  (`rfid1_enable` / `rfid2_enable`). There is no odometer and no motor data.
* **`InfoResponse.first_request` (cmd 7) is hard-coded to 0** - a host can never observe the latch.
* **Material names are character-filtered** (`if ((c - 0x30) >= 0x4B) c = 0`): space, `-`, `+` and
  `_` are silently zeroed. And **read-after-write is stale** - `GET_MATERIAL_INFO` reads the flash
  copy while `SET_MATERIAL_NAME` writes SRAM.
* **`SET_MATERIAL_NAME` supports only indices 0 and 1; `SET_SLOT_STATUS` only 0, 1 and 2.**
* Commands **67 `DRY_TEST`, 69 `RFID_TEST` and 74 `SET_KEY_LOG_ENABLE` are not implemented** - they
  return `code 400`.
