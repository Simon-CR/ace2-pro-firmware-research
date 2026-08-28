# Motion, preload, and length accounting

The mechanical layer: what the motors actually are, how a move is executed, what preload does on
its own, and where the length numbers come from. Several items here correct earlier public
analysis, including some of our own.

---

## 1. Units — everything else depends on these

Two independent motion sensors per lane, with **different** scale constants:

| Path | Hardware | Scale |
|---|---|---|
| Motor "steps" | TIM4 CH1-4 in **input capture**, falling edge | `18 / 1.457661` = **12.34855 steps/mm** |
| Filament encoder | TIM1 / TIM8 / TIM3 / TIM5 in **x4 quadrature** | **1.2342 mm/count** |

TIM4's absurd-looking `PSC = 0xFFFF / ARR = 119` is irrelevant precisely because the channels are
input capture, not output compare — only the edges matter.

**Speed is in mm/s and the control loop runs at 100 ms**, proven by algebra rather than assumption:
`UPDATE_SPEED` sets the setpoint to `speed / 1.457661`, and the control tick computes the measured
value as `Δsteps × 10/18`. Equating the two gives `Δsteps = 1.2348 × speed` per tick, i.e. exactly
`0.1 × speed` mm per tick. Valid range 0-100, so **100 mm/s is the maximum commandable speed**.

**These are DC gearmotors under closed-loop PID, not steppers.** TIM2 provides 18 kHz PWM
(`PSC=3, ARR=999`); `sub_8008F9C` is a PID whose output goes to `TIM2_CCRx`, with gains 3.5/10.5
below 11 mm/s and 1.0/1.0 above. The "stop" value written to `CCRx` is `1000` — full scale — so the
PWM is inverted.

---

## 2. Preload is fully autonomous, and its bounds are hardcoded

Only **one** sensor callback exists in the entire firmware: `sub_800F698`, registered for mask
`0x1111` — the four **INSERT** channels (0, 4, 8, 12). Everything else is polled.

On a clean 0→1 INSERT edge the firmware sets the slot to `PRELOADING` and queues the job. Guards
before it will: the paired lane must be READY/PRELOADING/error, the own lane must be READY (unless
already in error), and the lane's RFID reader must not be in state 8.

The preload job runs a 12-state machine with a 50 ms poll, and these constants (FP literals loaded
at `0x0800A0C2`-`0x0800A0D6`):

| value | role |
|---|---|
| **1700 mm at 50 mm/s** | the feed budget — **34 seconds of continuous feed** |
| **400 mm** | park datum |
| **750 mm** | ceiling when printer status is 0 → stop immediately |
| **1100 mm** | ceiling in state 8 → delay 2 s, stop |

On completion, if printer status is non-zero, the firmware **retracts back to the 400 mm datum**:
`rollback(|distance| - 400)`.

> ### This explains a real incident
>
> A preload that never receives a terminal RFID state runs its full 1700 mm budget (34 s) and then
> retracts 1300 mm (26 s) — **about 60 seconds of continuous motion**, ending far past the park
> datum. That matches an observed "~64 seconds past the hub" event that caused a lane collision.
> Bounds PROVEN; the causal attribution is strong inference.

**`SET_PRINTER_STATUS` gates this**: at 0 the preload is capped at 750 mm and does *not* retract to
the datum; non-zero enables the 1100 mm ceiling and the retract step. That effect is not documented
anywhere else.

### Why a tagless spool behaves differently from an unreadable one

There are **two RFID readers, not four** — `reader = lane >> 1`, so lanes 0/1 share reader 0 and
lanes 2/3 share reader 1. (This independently confirms the shared-antenna behaviour we measured
from the outside: two tags in range produce ANTICOLLISION.)

Two different retry caps exist in `sub_800E244`:

- **Cap 200** — a *blocking inner loop* in the tag-search phase. Each pass attempts a select; on
  **success the counter resets to 0**, and the loop only exits once 1000 ticks have elapsed.
- **Cap 49** — one attempt per RFID-task iteration in the later states. Past 49 it stops
  incrementing and returns **without changing state**.

So with *no tag*, selects fail fast, the 200-cap loop drains quickly, and the reader reaches a
state the preload machine treats as terminal — the feed ends early. With an *undecodable* tag the
card answers, so the counter keeps resetting, the loop only leaves on the 1000-tick timer, the
decode then fails repeatedly, and **the reader parks in a state the preload machine never accepts
as terminal** — so the feed runs its full budget.

Honest caveat: the "gives up fast" half of that rests on select *timing* (a fast NAK versus a full
anticollision cycle) rather than a distinct code path. No explicit "no tag → give up" transition
was found.

---

## 3. There is no odometer

**Every length register is per-operation.** `sub_8009D2E` runs at the start of every move and zeroes
both the extension counter and `TIMx->CNT`; the stop method disables the update interrupt, so wraps
are not even counted while idle.

`dword[0x20001E68 + 4*lane]` is **only** a 16-bit overflow extension, maintained by the four
`TIMx_UP` ISRs. Signed position = `TIMx->CNT + ext[lane]`.

> **Correction to earlier public analysis:** "EXTI0 (PA0) = encoder pulse interrupt" is wrong. The
> EXTI0 vector is a soft-PWM/ramp handler driving `GPIOC->BSRR` and never touches the encoder
> extension. PA0 is `TIM5_CH1` — one of the encoder inputs.

### `GET_FEED_INFO` (cmd 76)

Returns 4 lanes × `{steps, length, decoder}`, where `steps` and `length` are the motor-side
distance for the current or last move and `decoder` is the filament-encoder distance in mm. Two
defects, both read directly from the code:

- **`steps` over-reports by a constant +6** — the helper adds 0.5 mm before returning and the
  handler multiplies straight back by 12.34855.
- Wrap accounting is off by one (timers period 65536, ISRs add ±65535) — a ~0.0015 % under-read per
  wrap, irrelevant for a single move.
- `CNT` and `ext` are read non-atomically at six sites, so a wrap landing between the two loads can
  produce a one-shot ±65535-count glitch.

### Spool remaining length is *measured*, not decremented

Each spool revolution the tag passes the reader, so the **encoder distance between two tag passes
is the winding circumference at the current radius**. Accepted only in the range 180-600 mm. From
that the firmware derives a layer count and sums an arithmetic series, floored at 10 m and clamped
by the tag's stored total.

So the remaining length is a **geometric estimate from a measured circumference**, not a starting
value minus consumption. Unresolved: whether that estimate is written back where
`GET_FILAMENT_INFO` field 11 would report it.

---

## 4. The feed check is a slip comparator

This is the most consequential correction in this document, because tuning advice built on the
older reading is actively wrong.

`sub_8009D74`:

- it evaluates when the **motor-side** distance has advanced by `check_length × 1.2342` mm;
- the test is **`|Δmotor − Δencoder| > error_length × 1.2342`** — a disagreement between the two
  sensors, not an absolute distance threshold;
- the result is **`0x85 STUCK`**, or **`0x86 TANGLED`** if `CHN_BUF_FEED` reads 1 — *not*
  `0x81 FEED_ERROR`.

With the defaults (`check_length = 100`, `error_length = 90`): evaluate every **123.4 mm**, allow
**111.1 mm** of slip.

> **So lowering `check_length` tightens the evaluation interval — it does not widen the tolerance.
> To widen tolerance, raise `error_length`.** Both are u8, validated `check ≥ 3`,
> `3 ≤ error ≤ check`, `error ≠ 255`, so the maximum expressible slip tolerance is **313.5 mm**.

The check is **skipped entirely** while the rollback flag is set, and during the first 300 mm of a
preload.

> **Correction to our own earlier notes:** we described `[0x20001BA0 + 64*lane + 60]` as a
> "persistent assist flag". It is not. `sub_800B22C` sets it only when its sixth argument is
> non-zero, and the caller computes that as `(mode == 1)` — i.e. **rollback**. Its only consumer is
> the slip checker, which returns immediately when it is set. It is a **"suppress slip check during
> rollback"** flag, and neither assist mode sets it.

---

## 5. Assist ignores the length and speed you send

The ASSISTING handler issues `sub_800ECE8(slot, feed, 0xFFFFFFFF, 50)` — **length hardcoded to
unbounded, speed hardcoded to 50 mm/s**. An assist is always "run until a sensor or a stop command
ends it, at 50 mm/s".

Practical consequence: **if your extruder retracts faster than 50 mm/s, the ACE cannot keep up** and
slack accumulates instead of being taken up.

There is also an **MCU-side continuous-assist limit of 4000 ms**, after which the firmware takes an
error path — tighter than, and independent of, any host-side tangle timer.

A genuine `length == 0` on a *feed* or *rollback* is different: it yields zero steps and the move
terminates on the first capture pulse — a no-op that still emits the completion event.

---

## 6. Key and sensor event handling

- One task polls all 17 channels every **10 ticks**; debounce is **2 consecutive identical
  samples**, so 10-20 ms latency.
- The four **INSERT** channels are **ADC with hysteresis** (thresholds from
  `LINEAR_KEY_CALIBRATE`, defaults 600/1100 mV, trip points at 30 %/70 % of the band, one-pole IIR
  with α = 0.2). The buffer channels are plain active-low GPIO.
- ⚠️ **The four EMPTY channels (1, 5, 9, 13) are stubbed to constant 0** — the level function
  returns 0 and the jump table sends them to a bare `bx lr`. **They can never generate an event in
  this build, so the ACE MCU does not detect per-lane runout at all.** If you rely on runout
  detection, it has to come from a toolhead sensor.
- **Exactly one callback is registered** (INSERT). BUF_RST, BUF_BACK and CHN_BUF_FEED are consumed
  by *polling*, never by events.

**Two paths cause motion with no host command:**

1. **INSERT rising** → preload (up to 1700 mm at 50 mm/s, as above).
2. **INSERT falling** → stop, when the lane is FEEDING/ROLLBACK or in error 0x81/0x82.

The filament task also polls the buffer sensors: `CHN_BUF_FEED == 1 && BUF_BACK == 1` runs a motion
helper and then sets the slot to **0x83 (ASSIST_ERROR)**.

---

## 7. Other corrections

- **Slot-state array base is `0x20001004`**, not `0x20001000`; the latter is a separate aggregate.
- **Timer assignment**: TIM2 = 18 kHz motor PWM ×4, TIM4 = ×4 input capture for step feedback,
  TIM1/TIM8/TIM3/TIM5 = ×4 quadrature encoders for lanes 0-3. Not "TIM7 software PWM".
- **`GET_STATUS` carries no odometer** — its two length-looking fields are `feed_assist_count` and
  `cont_assist_time` (ms).
- **`FEED_OR_ROLLBACK` state gating** (undocumented elsewhere): feed is admitted from READY,
  **ASSISTING** or any error ≥ 0x7F; rollback from READY, **ROLLBACK_ASSISTING** or any error;
  the assists only from READY or error. Anything else returns busy.

## Not proven

- The SysTick rate is inferred as 1 ms, not read from a `LOAD` literal. **Every millisecond figure
  in this document scales with it** — worth confirming independently by timing a known
  `FEED_OR_ROLLBACK` against a wall clock.
- What writes RFID reader state 8 (the value that vetoes preload) — it is never written inside the
  state machine, so it must arrive via the reader queue.
- The physical wheel geometry behind 1.2342 mm per quadrature count.
