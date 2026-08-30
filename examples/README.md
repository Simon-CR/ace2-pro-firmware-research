# Using it: G-code examples

Everything here runs through `ACE_RAW_CMD`, provided by the Klipper extra in
[`klipper/ace_raw_feed.py`](../klipper/ace_raw_feed.py). **Install that first** — none of the
tooling works without it.

## Install

```bash
cp klipper/ace_raw_feed.py ~/klipper/klippy/extras/
```

Add to `printer.cfg`:

```ini
[ace_raw_feed]
```

Then restart the Klipper **service** (not just `RESTART` — Klipper does not reload Python modules
on a soft restart):

```bash
sudo systemctl restart klipper
```

It provides three commands:

| command | purpose |
|---|---|
| `ACE_RAW_CMD T=<tool> CMD=<name> [KEY=value ...]` | send any ACE command; this is the one the patches use |
| `ACE_RAW_FEED T=<tool> MODE=<0..3> [SPEED=] [LENGTH=]` | `FEED_OR_ROLLBACK` with an explicit mode, including the undocumented mode 3 |
| `ACE_RAW_STOP T=<tool>` | `STOP_FEED_OR_ROLLBACK` |

Motion, heater and configuration commands are rejected by `ACE_RAW_CMD` on purpose; use the
driver's own macros for those.

---

## Encoding the RC522 sub-command

The passthrough packs its sub-command into the request's `index` field:

```
bit31 = 1        (forces index >= 4, which is what routes into the stub)
bit24            reader: 0 = slots 0/1, 1 = slots 2/3
bits 23..16      op
bits 13..8       arg1
bits  7..0       arg2
```

So `INDEX = 0x80000000 | reader<<24 | op<<16 | arg1<<8 | arg2`. In decimal, for reader 1:

| what | expression | value |
|---|---|---|
| read register 0x37 | `0x81003700` | 2164272384 |
| SELECT (op 6) | `0x81060000` | 2164654080 |
| bulk page read (op 7) | `0x81070000` | 2164719616 |
| clear cache, slot 2 (op 8) | `0x81080200` | 2164785664 |

`tools/ace_reader.py` builds these for you; the raw form is shown here so the mechanism is clear.

---

## Check the firmware is ACE2-Open

```gcode
ACE_RAW_CMD T=0 CMD=GET_INFO
```

Look for `version: 'V1.1.3O'` in the console. `V1.1.31` means stock firmware is running.

## Read a tag's UID (any tag, including Bambu)

The UID passthrough needs no special command — it is the normal identify:

```gcode
ACE_RAW_CMD T=3 CMD=FILAMENT_IDENTIFY
```

- Anycubic tag → `code 0`, real `sku`, `version 101`
- Any other tag → `code 0`, `sku` = UID hex, **`version 513`** (`0x0201`, the "this is a UID" sentinel)
- Nothing in the field → `code 3`

The tag must be in front of the antenna at that instant — see
[docs/04-tag-operations.md](../docs/04-tag-operations.md) for why a single stationary read usually
fails, and how to park the tag first.

## Clear a lane's cached tag record

```gcode
; op 8, slot 2, reader 1  ->  0x81080200
ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX=2164785664
ACE_RAW_CMD T=2 CMD=GET_FILAMENT_INFO    ; now reports version 0, empty sku
```

Useful after a spool change so the lane reports "nothing decoded yet" instead of the previous
spool's identity. Note it clears the *firmware's* record; the Klipper driver keeps its own copy.

## Select a card and dump its pages

```gcode
; op 6 SELECT, reader 1  ->  0x81060000
ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX=2164654080
; op 7 bulk page read    ->  0x81070000   (returns 144 on success)
ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX=2164719616
```

Reading the 144 bytes back out is done a byte at a time (op 4), which is why the Python tooling
exists — see `tools/ace_reader.py`.

## Rotate a lane to bring its tag into the antenna

Raw async motion, so the gcode queue stays free for probing between steps:

```gcode
ACE_RAW_FEED T=2 MODE=1 LENGTH=15 SPEED=20     ; rollback 15mm (winds onto the spool)
ACE_RAW_CMD  T=2 CMD=FILAMENT_IDENTIFY         ; did the tag answer yet?
```

Repeat until it answers, then **stop** — the tag is parked in the field and you can work without
time pressure. Allow ~600 mm for a full revolution of a 1 kg spool. Restore the lane position
afterwards with the opposite mode.

## Rollback-assist (mode 3) — the undocumented feed mode

```gcode
ACE_RAW_FEED T=0 MODE=3                        ; arm rollback-assist
; ... extruder retracts; the ACE takes up the slack ...
ACE_RAW_STOP T=0                               ; stop
```

Mode 3 rewinds until the strand goes taut, waits, and resumes when slack appears — so the ACE can
only take up, never push, and no pacing between the two motors is needed.

**The `SPEED` argument is ignored for assist modes.** The handler hardcodes 50 mm/s and an
unbounded length (`0x0800A324`). So keep extruder retraction **below 50 mm/s** — faster than that
and the ACE cannot keep up, and slack builds instead of being taken up. Note also an MCU-side
**4-second continuous-assist limit** before the firmware takes an error path.

**Two rules:** enter it only from `ready` (stop any running assist first), and make **no forward
extruder move while it is armed** — pushing creates slack, the ACE immediately rewinds it, and the
feeder fights the nip. See [docs/05-protocol-notes.md](../docs/05-protocol-notes.md).

---

## Safety notes for macros

If you wrap any of this in a macro, two things belong in the sequence rather than in a comment:

1. **Guard against `code 4` (ANTICOLLISION).** Slots 2 and 3 share one antenna that can see both
   bays. Rotate the other lane's tag clear and confirm before acting, or you may read — or
   write — the wrong tag without noticing.
2. **For writes, offer to flip the spool** and repeat, so both faces carry the same payload and
   the spool works in any orientation.

---

## Spool rotation during drying (`ace_dryroll.cfg`)

Anycubic's own `auto_roll` nudges 5 mm every ~4 minutes — it barely rotates the spool and slowly
walks the filament backwards. [`klipper/ace_dryroll.cfg`](../klipper/ace_dryroll.cfg) does it
properly, with a mode chosen per lane:

| mode | requires | behaviour |
|---|---|---|
| `sweep` | lane **threaded** (slot `ready`) | back-and-forth within a calibrated range that lies entirely on the ACE side of the parked position — never toward the hub. Position preserved, nothing to tie. |
| `spin` | lane **not threaded** (slot `empty`, tip secured) | continuous rotation in one direction |
| `off` | | excluded |

**A lane is inert until its parked position is known against a hard datum** — sweeping moves a
spool, so the position it moves from has to be a measured fact. But the roller does not fail on a
lane that lacks one: it **checks whether it is calibrated, and if not, whether it can calibrate
right now** (printer idle, hub clear, shared path free, slot ready). If it can, it calibrates on
the spot via `ACE_LANE_NORMALIZE` — feed to the hub switch, back off `park_offset_mm` — and starts
rolling. If it cannot, it says what it is waiting for and retries. One lane per pass, because the
hub is a single-occupancy shared resource.

`spin` needs no datum: nothing is threaded, so there is no position to know.

```gcode
ACE_DRYROLL_MODE T=0 MODE=sweep
ACE_DRYROLL_MODE T=1 MODE=spin      ; refused unless slot 1 reads 'empty'
ACE_DRYROLL_STATUS
ACE_DRYROLL_START                   ; calibrates whatever needs it, then rolls
ACE_DRYROLL_STOP
ACE_LANE_RANGE T=0                  ; optional: refine the 600mm default to the measured bound
```

Observed on hardware, four loaded lanes from cold, no intervention:

```
[DRYROLL] T2 calibrating now (hub free, printer idle)
[PARK] T2: seeking the hub to establish a datum
[PARK] T2: hub released after 50mm of backoff
[PARK] T2 parked 50mm short of the hub (sensor-defined, repeatable).
       park -> toolhead entry is now 605.9mm
[DRYROLL] T2 calibrated - park is sensor-defined, sweeping 600mm on the ACE side
```

All four serialized on the hub in 349 s, then swept — encoder confirming 149 mm commanded against
148–153 mm measured per leg.

A lane that cannot be parked is **retired after two attempts** (mode set to `off`) rather than
re-running the ~1800 mm probe forever, and the next tick is armed *before* the long calibration
call, so a lane that never finds the hub raises without killing the roller. A lane that leaves its
park has its datum dropped automatically.

**The mode guard is mechanical, not advisory.** `spin` refuses unless the slot reads `empty`, so
continuous winding can never run on a fed lane; `sweep` refuses unless it reads `ready`. Neither
runs on the loaded tool, on a lane positioned at `toolhead`, or (by default) during a print.

**Calibration never crosses the gate sensor.** It steps back 25 mm at a time and stops at the last
step where filament is still reported present. Crossing it and returning produces the 0→1 INSERT
edge that triggers the firmware's autonomous ~1700 mm preload toward the hub — see
[docs/08](../docs/08-motion-and-preload.md). As a second layer, the restoring feed is issued as one
long move, so any crossing lands mid-feed while the slot reads `feeding`; the preload trigger
requires `ready`.

**Why `spin` works (proven on hardware):** the spool and the feed gears share one motor through a
permanent coupling, so a rollback on a lane with nothing in the gears still turns the spool.
`GET_FEED_INFO` separates the two measurements — with no filament, 299 mm commanded gave
`magnitude_mm` 299 (the motor) and `moved_mm` 0 (the filament). Nothing is consumed and nothing is
at risk, because nothing is in the path. See [docs/10](../docs/10-spool-drive-and-feed-telemetry.md).

---

## State audit (`ace_audit.cfg`)

Auditing belongs in every routine, not in a one-off script. [`klipper/ace_audit.cfg`](../klipper/ace_audit.cfg)
is the reusable check.

**Sensors first, flags second.** Flags are written by macros and can lie; switches cannot. The
entry / post-gear pair is a truth table, not two independent booleans:

| entry | post | meaning |
|---|---|---|
| 0 | 0 | EMPTY — nothing in the toolhead |
| 1 | 0 | PARTIAL — at entry, not through the gears |
| 1 | 1 | LOADED — through the gears, melt zone reachable |
| 0 | 1 | **ANOMALY** — past the gears but not at entry: a stub in the nip, or a failed sensor |

```gcode
ACE_AUDIT              ; report, and set .ok / .why / .toolhead
ACE_AUDIT STRICT=1     ; same, but raise so the calling routine aborts
ACE_AUDIT QUIET=1      ; set the variables without printing
```

A routine that moves filament opens with `ACE_AUDIT STRICT=1`. A background loop that must not die
calls plain `ACE_AUDIT` and branches on `printer['gcode_macro ACE_AUDIT'].ok` — as the drying
roller does.

**Calling it and reading the result must be two separate macro invocations.** Klipper renders a
macro completely before executing any of it, so reading `.ok` inside the same render returns the
*previous* pass's value.

What it cross-checks: the truth table itself; `hot` / `parked` against the switches; that filament
anywhere in the path has a recorded owner (`current_index`); that `hub_detect` filament belongs to a
loaded or staged lane; that `ace_filament_pos` agrees with the sensors (`bowden` is the driver's
UNLOADED state — only `splitter`/`toolhead`/`nozzle` assert filament in the path); per-lane
`lane_pos` against slot status; that no lane is off its park datum from drying rotation; and any
open intent that never committed.
