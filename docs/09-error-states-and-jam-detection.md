# Error states, jam detection, and why it protects less than you would hope

The ACE has native jam detection. This chapter documents exactly what it measures, when it is
switched off, and why two of its seven error states can never reach the host. The short version:
**it protects loads and preloads only, and it is inert at the fallback defaults.**

---

## 1. The seven error states

The driver's map is confirmed for 129/130/131/132/135 and **contradicted in practice** for
133/134.

| state | name | set where | what actually triggers it |
|---|---|---|---|
| **129** | `feed_error` | `0x0800A28E` | After a **commanded feed** completes, the encoder-deficit check had tripped. |
| **130** | `rollback_error` | `0x0800A31E` | Same test after a rollback — but **unreachable**, see below. |
| **131** | `assist_error` | `0x0800A4EC`, `0x0800A62A` | **Both buffer limit switches asserted at once.** Nothing to do with the encoder. |
| **132** | `preload_error` | `0x0800AB94`, `0x0800ACCC` | The same deficit trip as 129, observed during auto-preload. |
| **133** | `stuck_error` | `0x08009E88` | Deficit tripped while `CHN_BUF_FEED` was **inactive**. |
| **134** | `tangled_error` | `0x08009E8E` | Deficit tripped while `CHN_BUF_FEED` was **active**. |
| **135** | `motor_error` | `0x0800AE1C` | The motion-done event never arrived **and** the remaining-step counter did not change across a full 3 s window. |

### 133 and 134 never reach the wire

`0x08009E88` writes 133 (or 134) into the **feed-check object's** error field at `obj+0x30` — not
into the slot status word. The operation handlers then read that field and store **129** (feed) or
**132** (preload) into the status instead.

Verified exhaustively: every `#129`…`#135` immediate in the image was enumerated, every caller of
the arbitrated status setter checked, and the only direct status writes are the task tails.

> **So in V1.1.31 a slot status can never be 133 or 134.** The STUCK-versus-TANGLED distinction is
> computed and then discarded. Any host-side logic branching on those codes is dead.

### 130 is unreachable too

`FEED_OR_ROLLBACK` passes `bypass = (mode == 1)`, which sets the feed-check bypass flag for the
whole rollback. The check returns immediately while that flag is set, so a rollback cannot produce
a deficit trip, so 130 cannot fire.

---

## 2. What the check actually measures

It lives in a per-slot object at `0x20001BA0`. A FreeRTOS timer name string `"OdometerTimer"`
exists in the image at `0x08015930`, and our analysis pass attributed the check to a 20-tick
software timer — but **we could not independently locate the reference linking that timer to this
function**, so treat the sampling period as unverified. What follows does not depend on it.

```
if (!enabled)  return
if (bypass)    return                                   # set for the whole of any rollback
cmd = commanded_mm      enc = encoder_mm
if (cmd - checkpoint_cmd  <=  check_length * 1.2342)  return    # window not reached
if (status == PRELOADING && cmd < 300.0)              return    # preload warm-up
if (cmd < 30.0)                                       return
if ( |Δcommanded - Δencoder|  >  error_length * 1.2342 ) {
    error = (CHN_BUF_FEED ? 0x86 : 0x85)
    stop the motor, disable the check
}
checkpoint_cmd = cmd;  checkpoint_enc = enc            # sliding window
```

Key points, all read from the code:

- The sampled quantity is the **slot's own quadrature encoder** (TIM1/TIM8/TIM3/TIM5), not a step
  count — so it is genuine filament motion.
- **`check_length` and `error_length` are in encoder counts**, and 1 count = 1.2342 mm.
- The comparison is **absolute**: the encoder reading *more* than commanded trips it exactly as
  readily as reading less.
- There is **no retry counter, no back-off, no stall detection, no current sensing.** On a trip the
  firmware clears the running flag, de-asserts the driver pin, and disables the check. That is all.
- The 300 mm warm-up guard keys on **PRELOADING**, not assisting.

### When it is switched off

| situation | checked? |
|---|---|
| commanded **feed** (mode 0) | **yes** |
| auto **preload** | **yes**, after the first 300 mm |
| **rollback** (mode 1) | **no** — bypass set for the whole move |
| **feed assist** (modes 2/3) | **no** — bypass set at `0x0800A438` |

> **This is the important limitation.** The check gives **no protection during a print**, because
> printing runs on assist — which is exactly where a tangle bites. Enabling it correctly protects
> loads and preloads only.

---

## 3. The defaults, and a correction

`check_length` is a u8 at `0x20000096`, `error_length` at `0x20000097`, both inside the
flash-backed settings blob. There are only two writers in the entire image: `SET_FEED_CHECK` and
the boot clamp. **There is no factory-defaults writer and no getter — you cannot read the current
values back over the protocol.**

The boot clamp. Note the second branch: the published description says it resets `error_length`
to 10, but the branch reuses the *first* branch's store instruction after reloading `r1` with
**255** (`0x8015818: mov.w r1,#255` sits immediately before `0x801581C: bcc 0x80157F6`), so a bad
`error_length` becomes the disabling sentinel. Both tests also catch `255` on entry, because of the
`adds #1 ; uxtb ; cmp #4` idiom:

```
if (check_length in {0,1,2,255}) { check_length = 3;  error_length = 10; }
else if (error_length in {0,1,2,255}) error_length = 255;    <- 255, not 10
else if (error_length > check_length) error_length = 255;
```

> **A unit whose settings page is erased or zeroed boots with `check = 3, error = 10`: a 3.70 mm
> window with a 12.34 mm trip threshold. Since the deficit can never exceed the window, the check
> is completely inert.** A unit that has been paired with an Anycubic host instead holds 100/90.

---

## 4. Choosing values

**Measured basis:** window = `check_length × 1.2342 mm`; trip = `|Δcommanded − Δencoder| >
error_length × 1.2342 mm`; encoder quantisation 1.2342 mm; nothing checked below 30 mm commanded,
or for moves shorter than one window.

| check / error | window | tolerance | fires when |
|---|---|---|---|
| 3 / 10 (fallback) | 3.70 mm | 12.34 mm | **never** |
| 100 / 90 (Anycubic host) | 123.4 mm | 111.1 mm | encoder sees < 12.3 mm per window |
| 60 / 30 | 74.1 mm | 37.0 mm | encoder disagrees by > 50 % per window |
| 254 / 254 | 313.5 mm | 313.5 mm | never (deficit ≤ window by construction) |

**The following is inference, not measurement.** 100/90 is a *total-jam* detector, not a slip
detector: up to a full window of filament is ground before it reacts. Catching a jam earlier means
**lowering `check_length`** and setting `error_length` to roughly half of it — around 60/30 gives a
74 mm window with ±37 mm tolerance. Below `check_length ≈ 25` the ±1-count quantisation and buffer
take-up become a meaningful fraction of the window, and short moves stop being checked at all.

**The dominant false-positive risk** for a long bowden with a downstream extruder is the two-sided
comparison: during any coordinated ACE-feed-plus-extruder-pull move the encoder can *lead* the
commanded position, and `|Δcmd − Δenc|` grows just as fast as it would for a jam.

**Measure before choosing.** The device exposes exactly the quantity it tests: `GET_FEED_INFO`
(cmd 76) returns `{steps, length, decoder}` per lane — commanded steps, commanded mm, encoder mm.
Run your real load sequence at real speed with the extruder engaged, log `|length − decoder|` drift
per 100 mm, then set `error_length ≈ 2 × worst_drift_mm / 1.2342` and `check_length ≈ 2 ×
error_length`. The same reading confirms `steps ≈ 12.349 × length`, validating the unit assumption
on your hardware.

Set them with `SET_FEED_CHECK` (cmd 19): `check ∈ [3, 254]`, `error ∈ [3, check_length]`,
`error ≠ 255`. **They persist to flash, and cannot be read back — so record what you set.**

---

## 5. Recovery: STOP does not clear errors

| status | what `STOP_FEED_OR_ROLLBACK` does |
|---|---|
| 1, 2 feeding/rollback | motor stop only; **status left as-is** |
| 3, 4 assisting | status → 0, **no motor stop** |
| 5 preloading | clears only if it was 5 |
| 129, 130 | motor stop only; **status stays 129/130** |
| **131, 133, 134, 135** | **nothing at all** |
| 132 | not cleared |

The **only** recovery is to start a new operation on that slot: `FEED_OR_ROLLBACK` explicitly
permits every mode when `status >= 127`, and the status setter stores unconditionally over any
status with bit 7 set. A power cycle also clears it. There is no dedicated clear-error command.

---

## 6. The post-STOP window — solved, and there is no timer

A long-standing puzzle: after a stop, motion requests are silently refused for what looked like
20–30 seconds. **There is no timer.** The longest `vTaskDelay` in the image is 3000 ticks.

`STOP_FEED_OR_ROLLBACK` writes the stop pin, clears the running flag and disables the check. It
does **not** clear the channel-busy flag, does **not** flush the queue, and does **not** signal the
motion-done event — and it returns success regardless, including for the statuses where it does
nothing at all.

The window comes from the **STOP being lost**, and there are two provable races:

1. **Pre-dequeue.** Enqueueing sets `status = mode + 1` immediately, so a STOP arriving before the
   filament task dequeues finds status 1/2, stops a motor that is not running, and returns success.
   The move then runs its **full commanded length** — and that is precisely how long retries are
   refused.
2. **Setup race.** `sub_800ECE8` contains a **200 ms `vTaskDelay`** and only sets the running flag
   afterwards. A STOP landing inside that window is silently overwritten.

A third contributor: there are **two op channels**, and feed assist holds one indefinitely (a 10 ms
polling loop that exits only when the slot leaves the assist state, or after 4000 ms with no
movement). With assist active, only one channel remains and a concurrent request is refused at once
— which is why the driver already notes empirically that "feed assist causes busy".

> **Actionable for any host:** after a STOP, **poll `GET_STATUS` until the slot status returns to
> 0** instead of assuming it took effect. And do **not** issue a STOP within ~250 ms of the
> `FEED_OR_ROLLBACK` that started the move — the firmware will drop it.

---

## 7. Timing baseline

`SysTick LOAD = 0x0001D4BF` (119999) with `CTRL = 7`, so the tick is `SYSCLK / 120000` — **1 kHz if
SYSCLK is 120 MHz**, which is the expected clock for this GD32F303-class part. Every millisecond
figure in these documents rests on that.
