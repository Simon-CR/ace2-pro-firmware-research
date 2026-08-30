# 10. Spool drive and feed telemetry

## The spool and the feed gears share one motor

There is no spool-rotation command anywhere in the ACE 2 Pro's registered command set
(0-20, 64-66, 68, 70-73, 75-78), yet the device plainly winds a spool back in when it retracts.

It does so because **the spool and the feed gears are driven by the same motor through a permanent
coupling**. The hardware is consistent with this: four PWM channels on TIM2, four quadrature
encoders (TIM1/TIM8/TIM3/TIM5), four input captures on TIM4, four lanes -- one motor each, with no
fifth channel anywhere for a separate spool drive. `MOTOR_TEST` (77) reuses `FeedOrRollbackRequest`
and the same worker as an ordinary feed.

**Proven on hardware, 2026-08-29.** A lane was ejected until it reported `empty`, its filament tip
detached from the gears and secured to the spool by hand, and a 300 mm rollback commanded on that
lane. The spool turned.

## `magnitude_mm` measures the motor, `moved_mm` measures the filament

`GET_FEED_INFO` (cmd 76) returns both, and the empty-lane case separates them cleanly:

| condition | commanded | `magnitude_mm` | `moved_mm` | `motor_counts` | counts/mm |
|---|---|---|---|---|---|
| filament in gears | 149 mm | 149 | -151 / -153 / -148 | 1852 | 12.43 |
| **no filament** | 299 mm | 299 | **0** | 3699 | 12.37 |

* **`magnitude_mm` and `motor_counts` track the MOTOR.** They follow the commanded distance
  whether or not filament is present, at a constant ~12.4 counts/mm.
* **`moved_mm` tracks the FILAMENT**, from a separate sensor, and it is **signed** -- negative for
  rollback. With nothing in the gears it reads exactly `0` while the motor turns 299 mm.

This is a direct empirical confirmation of the two inputs to the feed-check slip comparator
described in [09](09-error-states-and-jam-detection.md): the comparator watches motor-versus-
filament divergence, and an empty lane is the maximal-divergence case.

Notably **the divergence raised no error**. The lane reported `status_code 2` (busy) throughout and
returned to `0`, which is consistent with the native jam detection being present in firmware but
never enabled by the host driver. A host that switches it on should expect an empty lane under
rollback to trip it.

## Consequences

**Spool drying without touching the filament.** A spool can be rotated indefinitely for drying by
commanding an ordinary rollback on an *empty* lane, with the tip detached and secured. No filament
is consumed and none is at risk, because none is in the path. This is what makes `spin` mode in
[`klipper/ace_dryroll.cfg`](../klipper/ace_dryroll.cfg) viable, and it is strictly better than
Anycubic's own `auto_roll` (`DryingRequest` field 3), which nudges 5 mm at mode 1 every ~4 minutes:
that barely rotates the spool and slowly walks the filament backwards.

**Why spool size and drag matter.** The feed gears fight the spool's inertia and friction through a
fixed mechanical ratio with no clutch, so an oversized spool, a warped one, or one binding in its
cradle loads the feed motor directly and shows up as feeding problems. The firmware measuring spool
circumference -- accepting 180-600 mm -- is part of the same design.
