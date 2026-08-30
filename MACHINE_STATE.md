# Machine state — 2026-08-30, left safe overnight

## Physical state RIGHT NOW

```
toolhead_entry = TRUE      toolhead_postgear = FALSE     hub_detect = TRUE
ACE slots      = [empty, ready, ready, empty]   T0 insert=False
hub encoder    = 19 pulses (frozen)
lanes          = T1, T2 parked at datum (884.1mm staged, sensor-defined)
heaters        = OFF (nozzle 36C and falling)     print = cancelled
toolhead       = parked X290 Y290, Z+10 clear of the part
drying roller  = STOPPED, allow_printing forced to 0
```

## What needs hands before anything else

**There is a free-floating segment of filament in the path.** It runs from past the ACE lane-0
gate, through the hub, up the shared bowden, to the toolhead entry. **Neither end can drive it:**
the ACE has released it (slot 0 reads `empty`, `insert=False`), and the extruder gears sit at or
above its tip so they have nothing to grip.

It has to come out by hand — at the toolhead (open the idler, or pull the bowden at the toolhead
end) or at the hub. Nothing in software can move it.

**While the idler is open, look at two things:**
1. **The cut face.** Was it undersized/tapered? `CROSSBOW_CUT_TIP` warned so itself, unprompted:
   `PROBE: stub 4.5mm is inside the taper (full 1.75mm returns at 5.4mm). Expect the face to be
   undersized — inspect it.` If the face is bad, the cutter geometry is wrong (see below).
2. **A grind notch.** Claude pulled the extruder 60mm against a strand that was not moving, past
   the driver's own 16mm stall threshold. If there is a flat spot, that is where it came from.
   These two look different and the difference decides which fix is real.

## How it got here, in order

1. Print started. `ACE_DRYROLL_PREPARE` correctly re-parked reserved lanes. First load T-1 -> T0
   ran, but purged only 28.5mm against the driver's own request of 67.0 — under-primed tower.
2. First toolchange T0 -> T1. `CROSSBOW_CUT_TIP` cut the tip and warned the face was undersized.
3. `_ACE_TANDEM_EXTRACT` reported `strand NOT moving - hub encoder saw 0 pulse(s) over 16mm`.
   Toolchange failed, print paused, head left sitting on the part at 220C.
4. Claude moved the head clear (correct), then ran `ACE_LANE_EJECT T=0 FORCE=1` — also 0 pulses.
5. Claude then hand-pulled the extruder 60mm with no movement — **this was the mistake**, it
   ignored the 16mm stall threshold that exists precisely to stop that.
6. Reverse assist (mode 3) was tried: the spool visibly rewound, so the ACE end works, but the
   strand at the toolhead never moved — the ACE was taking up slack / binding a buckle.
7. A later mode-3 run did move it: postgear re-engaged, then 5mm of extruder retract with assist
   active cleared postgear instantly. That proves the mode-3 + extruder combination works.
8. 350mm of ACE-alone retract then pulled the strand clear out of lane 0 and released it,
   leaving the free segment described above.

## Root causes identified (both still unfixed)

### 1. `ptfe_postgear_to_nozzle` is wrong — caused the under-purge

```
ptfe_postgear_to_nozzle      = 51.7
crossbow_postgear_to_blade   = 5.6  +  crossbow_blade_to_nozzle = 46.1  =  51.7   <- exactly
```

`ace.cfg` records the measured value: *"ptfe_postgear_to_nozzle (saved variable) corrected 40 -> 80
the same day"* (2026-08-24), from a real bracketing test — *"70mm of push produced NOTHING at the
brush and flow began at ~80"*. The CROSSBOW calibration has since overwritten that purge-critical
variable with the cutter's own two-leg geometry.

The arithmetic reproduces the log exactly. With 51.7:
`fill = (1510-1490) + 51.7 - 20 = 51.7`, so `net_already = 90 - 51.7 = 38.3` — the number in the
log. With the measured 80: `net_already = 10.0`, and the purge would have been **~57mm instead of
28.5mm**. Roughly half the prime that was needed.

Owner's instruction: the value was already measured; it does not need re-measuring. It needs the
**with-cutter and without-cutter values stored separately**, with the purge told which applies —
one variable currently holds two different geometries and they silently disagree.

### 2. Tandem extract should no longer exist

Mode 3 (rollback assist) was proven on hardware earlier in this session and was supposed to replace
the hand-synchronised tandem pull, which is fragile precisely because both ends must stay in sync.
That replacement was never implemented. The toolchange still runs the old tandem.

## Segment ownership — the rule to build everything on

Path order: `ACE gate -> ACE gears -> lane tube -> hub -> shared bowden -> toolhead_entry ->
extruder gears -> toolhead_postgear -> CROSSBOW blade -> melt zone -> nozzle`

Different segments MUST be driven by different actuators. Using the wrong one either does nothing
or grinds:

| where the tip is | sensors | who can actually move it |
|---|---|---|
| ACE side of entry | entry=0 post=0 | **ACE alone** — the extruder has nothing to grip |
| between entry and the gears | entry=1 post=0 | **ACE pushes**; extruder can only help once it bites |
| through the gears, before postgear | entry=1 post=0 | **both** — extruder grips |
| past postgear | entry=1 post=1 | **extruder drives, ACE assists (mode 3 on retract)** |

**`entry=1 post=0` is ambiguous** — it covers both "not yet bitten" and "gripped but tip not yet at
postgear". Sensors alone cannot tell those apart, so the state machine needs the DIRECTION of
travel, not just the switch states. That ambiguity is what tonight's failure fell into.

**Caveat on tonight's evidence:** Phase B (extruder retract with postgear clear) moved nothing, but
that is NOT evidence against the rule — by then the filament had already been ground. On an
undamaged strand the gears still hold filament when postgear clears, so tandem there should work.
