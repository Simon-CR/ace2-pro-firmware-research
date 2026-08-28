# Other protocol findings

Findings about the ACE 2 Pro protocol that are not about RFID. All measured on firmware V1.1.31
with the [Kobra-S1/ACEPRO](https://github.com/Kobra-S1/ACEPRO) driver.

## `FEED_OR_ROLLBACK` (cmd 8) has four modes, not two

| mode | slot `status_detail` | code | sent by | behaviour |
|---|---|---|---|---|
| 0 | `feeding` | 1 | `ACE_FEED` | `length` mm forward at `speed` |
| 1 | `unwinding` | 2 | `ACE_RETRACT` | `length` mm backward |
| 2 | `assisting` | 3 | `ACE_ENABLE_FEED_ASSIST` | open-ended; feeds when the buffer is pulled |
| 3 | **`rollback_assisting`** | 4 | **nobody** | open-ended; rewinds until the strand goes taut |

Mode 3 is a first-class device state that the driver never uses — the decoder string exists but
has no caller. Confirmed on hardware: the device answers `SUCCESS` and the slot reports
`rollback_assisting`.

**Handler validation** (`0x0800B578`): `index ≤ 3`, `speed ≤ 100`, `mode ≤ 3`, else PARAM_ERROR.
So speeds above 100 are impossible and modes 4+ do not exist.

**Admission by slot state** — a request is refused with `FORBIDDEN` unless the slot is in a
compatible state:

| requested | admitted when the slot is |
|---|---|
| 0 feed | `ready`, `assisting`, or any error state (≥127) |
| 1 rollback | `ready`, `rollback_assisting`, or any error state |
| 2 / 3 assist | `ready` or any error state |

Reading: mode 3 is the exact mirror of mode 2. Under rollback-assist an explicit rollback is
admitted and a feed refused, and vice versa. Neither assist can be entered while the other is
running — stop first. Error states admit everything, which is how recovery moves get through.

## Buffer gating: the in-line buffer is a *stop* condition

The runner (`0x0800A0B0`, 10 ms cycle) reads three key sources per tick: `CHN_BUF_FEED`
(KeyIndex 16), a per-slot **BUF_BACK** index from the table at `0x08019570 = {3,7,11,15}`, and a
per-slot **BUF_RST** index from `0x08019560 = {2,6,10,14}`. Active flags live at
`0x20001A14 + 4·KeyIndex + 1`.

Measured behaviour, confirmed by hand on the buffer:

- **BUF_RST stops mode 2**, **BUF_BACK stops mode 3**.
- Mode 2 = "feed until the buffer is compressed forward, wait, resume when the extruder takes
  filament". Mode 3 = "rewind until the strand goes taut, wait, resume when slack appears".

**Both assist modes are motion commands on a free strand.** With nothing holding the strand
(bowden disconnected, or the extruder idler open) neither mode ever sees its stop condition, so
mode 2 feeds continuously and mode 3 rewinds continuously. Treat "enable assist" as "run this
direction unless the buffer resists".

### Why mode 3 is useful

Under mode 3 the ACE can only *take up* slack, never push. With the strand held in the extruder
nip it follows every extruder reverse move roughly 1:1, so a retraction needs no pacing between
the two motors at all — the extruder sets the rate.

Demonstrated on a stuck lane: mode 3 armed (speed argument irrelevant, see below), then plain
5 mm extruder retracts at 20 mm/s cleared
the toolhead entry sensor in 30 mm with no tandem synchronisation, no accept-gating and no
grinding.

**The `speed` and `length` you pass to an assist mode are ignored.** The ASSISTING handler at
`0x0800A324` issues its move as `sub_800ECE8(slot, feed, 0xFFFFFFFF, 50)` — length hardcoded to
"unbounded" and **speed hardcoded to 50 mm/s** — so an assist is always "run until a sensor or a
stop command ends it, at 50 mm/s". (An earlier version of this document advised setting the speed
above your extruder's retract rate. That advice was wrong: the parameter never reaches the motor.
The observed behaviour is unchanged, because our extruder pulls were slower than 50 mm/s anyway.)

The practical consequence is real, though: **if the extruder retracts faster than 50 mm/s the ACE
cannot keep up**, and slack will build rather than being taken up. Keep extruder retraction below
that.

There is also an **MCU-side continuous-assist limit of 4000 ms** (`0x0800A40C`, comparing
`cont_assist_time` at `0x200014CC`), after which the firmware takes an error path. That is tighter
than, and independent of, any host-side tangle timer.

Conversely, **no forward extruder move while mode 3 is armed** — pushing creates slack, the ACE
immediately rewinds it, and the feeder fights the nip.

## The hub encoder is not proof of movement — the switches are

During that same extraction the hub encoder registered **0 pulses across 30 mm of confirmed
travel**, because a freshly loaded lane has slack in its ~1500 mm bowden and the first
centimetres of extraction take up slack without turning the hub wheel.

A switch that changes state is proof of travel. The encoder reading zero is evidence only when no
switch contradicts it. We had a guard that trusted the encoder alone and it aborted a perfectly
healthy swap.

## `STOP_FEED_OR_ROLLBACK` (cmd 9)

There is exactly **one** stop opcode. The driver's "stop feed", "stop unwind" and "stop feed
assist" all emit the identical `STOP_FEED_OR_ROLLBACK {index}` — there is no separate
`STOP_FEED_ASSIST` on ACE2, and no mode or direction parameter.

The core (`0x0800B1BE`) branches on slot state: `1/2` (feeding/unwinding) and their error twins
`129/130` clear the busy flag **and call the motor stop**; `3/4` (the assist states) merely write
state 0 and return, **without** a motor-stop call; `5/132` (preload) divert elsewhere.

No timestamp or countdown is written anywhere on the stop path. **There is no timer at all** — the
longest `vTaskDelay` in the whole image is 3000 ticks. The real explanation is that **the STOP is
lost**, and there are two provable races:

* **Pre-dequeue.** Enqueueing sets `status = mode + 1` immediately, so a STOP arriving before the
  filament task dequeues the job finds status 1/2, "stops" a motor that is not yet running, and
  returns success. The move then executes its full commanded length — and the window in which
  retries are refused is exactly the remaining move time.
* **Setup race.** `sub_800ECE8` contains a **200 ms `vTaskDelay`** at `0x0800ED10` and only sets the
  running flag afterwards at `0x0800ED4A`. A STOP landing inside that 200 ms is silently
  overwritten.

In both cases it is the *status* gate that rejects the retry, not a lockout. A third contributor:
feed assist permanently occupies one of the **two** op channels, so with assist active only one
channel remains and a second concurrent request is refused immediately.

**Actionable:** after `STOP_FEED_OR_ROLLBACK`, poll `GET_STATUS` until the slot status returns to 0
rather than assuming the stop took effect — and do **not** issue a STOP within ~250 ms of the
`FEED_OR_ROLLBACK` that started the move, because the firmware will drop it.

(An earlier version of this document attributed the window to residual motion keeping the busy
check asserted. That was wrong: once STOP clears the running flag the remaining-step counter
freezes and the control tick forces completion within milliseconds.)

## Command inventory

Registered command IDs: 0–20, 64, 65, 66, 68, 70–73, 75–78. Note that **69 (`RFID_TEST`) is not
registered at all** — it exists in the driver's catalog (inherited from the gen-1 ACE Pro) but the
ACE 2 firmware has no handler, which is why calling it returns nothing. 67 and 74 are likewise
absent.

`GET_SENSOR_STATE` / `GET_KEY_STATE` (cmd 73, handler `0x0800FC4C`) returns a 17-bit mask, bit *n*
= KeyIndex *n* active.

## A driver bug worth fixing upstream

`FILAMENT_IDENTIFY` (cmd 68) is declared in the ACEPRO driver as returning a `GenericResponse`,
whose field 1 is the result code. The firmware actually returns a `FilamentInfoResponse`, whose
field 1 is the **index** and whose field 12 is the code. So the driver reports back the index it
sent: index 1 reads as "PARAM_ERROR", 2 as "FORBIDDEN", 3 as "FAILED", and 0 as "SUCCESS" only
because proto3 omits zero scalars.

Every per-slot conclusion drawn from cmd 68 before fixing this is void — including several of
ours. Declare the response type correctly and decode it like cmd 13.
