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

## Root causes identified

### 1. `ptfe_postgear_to_nozzle` was wrong — caused the under-purge  **[FIXED]**

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

**FIXED 2026-08-30.** `ptfe_postgear_to_nozzle` restored to the measured `80`. The purge now
computes `entry_to_nozzle = 100.0`, which satisfies the invariant `ace.cfg` states in its own
comments ("THE TWO MUST SUM TO >= 100") — independent confirmation the value is right. Purge goes
from 28.5mm to **57.0mm**. Both figures are now also stored separately so they can never silently
overwrite each other again: `ptfe_postgear_to_nozzle_measured = 80`,
`ptfe_postgear_to_nozzle_crossbow_sum = 51.7`.

### 1b. The two numbers are DIFFERENT QUANTITIES — and the cutter used the wrong one  **[FIXED]**

This is the real insight, found by checking every consumer of the variable before trusting the fix.
There are seven live readers, and one is the cutter:

```
crossbow.cfg:278   {% set postgear = svv.ptfe_postgear_to_nozzle|default(51.7)|float %}
crossbow.cfg:286   {% set park_back = postgear - blade %}      # 51.7 - 46.1 = 5.6
```

`park_back` is a **geometric** blade->postgear distance. But `ptfe_postgear_to_nozzle` is a
**functional** push-to-first-flow distance — the 2026-08-24 measurement ("flow began at ~80")
includes melt-zone fill and compressibility, so it is legitimately ~28mm larger than the geometry.
The two are not the same measurement and never were; the cutter's `default(51.7)` in that line is
almost certainly how the functional variable came to be overwritten with the geometric sum.

Consequence: restoring the correct `80` would have broken the cutter — `park_back` would have
become 33.9mm instead of 5.6mm. The decoupling was therefore REQUIRED before the purge fix could
stand.

**Correction to an earlier claim in this document:** this misderivation did NOT cause the
undersized cut faces. With the old value, `park_back = 51.7 - 46.1 = 5.6`, which is exactly the
correct geometric leg — the wrong route arrived at the right number by coincidence. The real cause
is below. `park_back` also only sets where the tip parks AFTER the cut; the cut POSITION is
`cut_h = blade - stub` and never involved this variable at all.

### 1c. The cut stub was inside the taper, and its guard was defeated  **[FIXED]**

The actual cause of the tapered/undersized faces:

```
cut_tip_stub        = 4.5     <- the stub actually cut
cut_tip_deform_mm   = 4.5     <- the guard's floor, lowered to match
cut_tip_full_dia_mm = 5.4     <- filament only returns to a full 1.75mm here
```

The stub was **0.9mm inside the taper**, so every cut face was undersized by design. The macro's
guard is `stub < cut_tip_deform_mm`, i.e. `4.5 < 4.5` — false, so it never fired. Lowering the
measurement had silently disarmed the check that existed to catch exactly this.

`crossbow.cfg`'s own comment prescribes the remedy: *"STUB=6.0 is therefore the MEASURED MINIMUM,
not a safe default. It leaves nothing for variation between cuts. Raise cut_tip_stub toward 8.0 if
a cut face ever comes back tapered, undersized, or carrying the previous colour."* It came back
tapered, so `cut_tip_stub` is now **8.0** (hot retract 38.1mm; the pushback cap is
`blade - meltzone - margin = 21.1mm`, so 8.0 still fits comfortably).

The guard now tests against the STRICTER of the two measurements
(`max(cut_tip_deform_mm, cut_tip_full_dia_mm)`), so lowering one of them can no longer disarm it.
Backup at `crossbow.cfg.bak-stubguard`.

**An undersized cut face is very plausibly the whole reason the extraction failed** — a tapered tip
is what the ACE gears cannot grip, and gripping is what the 0-encoder-pulse stall was reporting.

`crossbow_postgear_to_blade = 5.6` already existed as a saved variable and was simply not being
used. `crossbow.cfg` now reads it directly, so cutter geometry and purge distance are fully
decoupled. Backup at `crossbow.cfg.bak-geomfix`.

The other six readers (`ace_purge.cfg:386`, `load.cfg:468`, `brush_prime.cfg:187`,
`pause_resume.cfg:68`, `start.cfg:774`, `ace_mmu_shim.py:1071`) all want the FUNCTIONAL distance —
they push filament and wait for it to arrive — so `80` is correct for every one of them.

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

---

# Machine state — 2026-08-31, after the cutter/purge commit

The 08-30 snapshot above is **superseded**: the orphaned strand has been cleared and the state flags
have been reset. Read live, immediately after `RESTART` for commit `a2d6bf3`:

```
toolhead_entry = FALSE (sensor DISABLED)   toolhead_postgear = FALSE
filament_parked = 0     filament_loaded_hot = 0
homed_axes     = ''     extruder 23C, target 0, heaters OFF
print_stats    = standby                   /printer/info = ready
cut_stub_pending = 4.5   cut_tip_stub = 8.0
```

Path clear, `ACE_AUDIT` clean. Nothing was moved, cut or heated to produce this — the session was
static analysis and config edits only.

**Note `toolhead_entry` is DISABLED** (`enabled: false`). Any reasoning that quotes its
`filament_detected` is quoting a stale value. `_CROSSBOW_CUT_COMMIT` used to print it as evidence in
its own error message; it now prints `DISABLED` instead. Anything else that reads `entry` without
checking `enabled` is reading noise — worth a sweep.

**`cut_stub_pending = 4.5` is orphaned.** The path is clear, so there is no stub in the melt zone,
and 4.5 predates the raise of `cut_tip_stub` to 8.0. The 08-30 recovery cleared `parked`, `hot`,
`ace_op`, the target/current index and `lane_pos`, but not this. It will be spent as 4.5mm of
over-purge on the next toolchange — harmless in itself, but it is direct evidence that the stub
credit was never tied to a physical fact. **Set it to 0 by hand.** (Fixed going forward: the credit
is now cleared only where it is actually spent, so it can no longer be burned by a skipped purge —
but it also now persists until something spends it, which is why this stale one should be zeroed.)

## Sensor map — settled

Confirmed from the machine's own saved variables, and the number that matters for the cutter:

```
nozzle 0  <  melt zone 17..22  <  BLADE 46.1  <  post-gear 51.7  <  extruder nip 69.7  <  entry 71.7
```

(`post-gear` = `crossbow_blade_to_nozzle 46.1 + crossbow_postgear_to_blade 5.6`; `entry` =
`post-gear + (ace_park_to_postgear 1510 - ace_park_to_entry 1490)`; nip = `post-gear +
ptfe_gears_to_postgear 18.0`.)

**Both switches are above the blade.** That single fact is why a missed cut cannot be detected on
this toolhead — see the 2026-08-31 section of `FILAMENT_PATH_FIXES.md`. It is a hardware finding, not
a macro one: the severed fragment lives between the melt zone and the blade and no sensor can reach
it. Adding a sensor below the blade is the only fix.

---

# Machine state - 2026-08-31, after the print-boundary pass

## Physical state RIGHT NOW

Unchanged by this session. **No filament was moved** - the work was static verification plus
config edits, and every live command run was chosen because it cannot actuate.

- Filament path CLEAR: `toolhead_entry = False`, `toolhead_postgear = False`
- `ace.current_index = -1` (no lane owns the toolhead)
- `ace_target_index = -1`, `ace_op = ''` - no open intent
- `ACE_AUDIT` -> `ok = 1`, `why = ''` (verified at session end)
- Klipper `ready`, `print_stats.state = standby`
- Suppression flags both 0; watchdog disarmed

## What changed

Config only, committed as `3a0ab31` in `~/printer_data/config` (full rationale in `git notes` on
that commit). Six files: `ace.cfg`, `ace_toolchange.cfg`, `ace_unload.cfg`,
`macros/print/{start,end,cancel}.cfg`. See `FILAMENT_PATH_FIXES.md`, section
"Print boundary - fixes applied 2026-08-31".

`RESTART` was issued four times; `/printer/info` returned `ready` after each.

## What was verified live, and what was not

**Verified by observation on the machine:**
- `_ACE_SWAP_VARS.extract_mm = 48` and `_ACE_UNLOAD_VARS.extract_cap = 48` read back from the
  object model after restart, not just from the file.
- `ACE_RECONCILE_TARGET STRICT=1` executed and took a branch: `ace_target_index` was set to 1 with
  `ACE_DEBUG_SET_TARGET_INDEX` and the macro drove it back to -1 via the empty-toolhead branch.
- `ACE_CLEAR_OP` executed on an empty `ace_op` and reported "no open intent recorded".
- `_ACE_RELEASE_ASSIST_AFTER_PARK` armed, ticked, and terminated. With `current_index = -1` its
  release branch is a no-op, so this was safe to run with filament in the path... there was none.
- **Watchdog ownership, end to end.** Armed with both flags 0; raised `swapping` afterwards
  (foreign to the arm); forced the watchdog to fire - it did **not** clear the flag and re-armed
  with a refreshed snapshot (`armed_swapping` 0 -> 1); forced it again - it then cleared. The
  pre-fix code would have cleared on the first fire. This is the ownership hazard demonstrated,
  not argued.

**NOT verified - needs hardware, i.e. a real print:**
- That `PRINT_START_INIT` actually reaches the two newly-wired clears in a live job. The wiring is
  proven by grep and by the object model; the execution is not, because running it means heating
  and printing.
- The `STRICT=1` raise itself. Reaching it requires filament in the toolhead with
  `ace_target_index >= 0` and `target != current` - a half-done toolchange, which cannot be staged
  without moving filament.
- The deferred assist release doing real work. With `current_index = -1` the release branch is a
  no-op; proving it releases requires a loaded lane and a real park.
- The both-switch gate in `_ACE_PARK_VERIFY`. Reaching it requires the physically impossible
  `entry=0 / post=1`, which means breaking a strand or unplugging a switch.
- The 48 mm cap in anger. Proving the pull terminates well short of it needs a real swap.

## Standing risk this session did not remove

The **first real print after these changes is the test**. Three of the seven fixes only engage on a
path that requires heating, and one of them (`STRICT=1`) can now **refuse to start a print** where
the old code silently continued. That is the intended behaviour, but it is a new way for a job to
stop, and it stops it before the heaters - so the failure mode is a cold refusal with a message,
not a wasted heat-soak.

# Machine state - 2026-08-31, after the audit/dryroll/load/material pass

## Physical state RIGHT NOW

Unchanged by this session. **No filament was moved.** `SET_FILAMENT_SENSOR SENSOR=switch_sensor
ENABLE=0/1` was toggled live to verify C3 and restored to `ENABLE=1` before the final restart - it
is a monitoring toggle, not motion.

- `toolhead_entry = False`, `toolhead_postgear = False`, `hub_detect = False` - path CLEAR
- `ace.current_index = -1`, `hot = 0`, `parked = 0`
- ACE slots = `[empty, ready, ready, empty]`, `lane_pos = ['gate', 'parked', 'parked', 'gate']`
- `ace_preload_guard.staged = {2: 884.1}` (T2, unchanged from the 2026-08-30 snapshot - below its
  ~909mm stage limit, not occupying the shared path)
- `ACE_AUDIT` -> `ok = 1`, `why = ''` ("no contradictions"), read before the first edit and again
  after the last restart
- Klipper `ready`, `print_stats.state = standby`

## What changed

Config only, committed as `8e2cf24` in `~/printer_data/config`. Four files: `ace_audit.cfg`,
`ace_dryroll.cfg`, `macros/filament/load.cfg`, `macros/helpers/material_temp.cfg`. See
`FILAMENT_PATH_FIXES.md`, section "Audit/dryroll/load/material fixes — 2026-08-31".

`RESTART` was issued three times; `/printer/info` returned `ready` after each.

## What was verified live, and what was not

**Verified by observation on the machine (see FILAMENT_PATH_FIXES.md for full detail):**
- `ACE_AUDIT` clean before and after.
- `_MATERIAL_PURGE_TEMP` read back live for all three required cases (`PC-ABS`->`PLA` = 280,
  `PLA+`->`PLA+ FLOOR=220` = 220 with no flush, `ABS`->`PLA` = 280), reproduced after the final
  restart.
- `sfs_live` read back live through a temporary diagnostic macro across an actual
  `SET_FILAMENT_SENSOR ENABLE=0/1` toggle on the real (dead) `switch_sensor` pin, and against a
  deliberately nonexistent config section (no crash).

**NOT verified - needs hardware:**
- C1's orphan-strand branch firing against a real unowned strand in the hub (simulated only -
  reaching it for real means staging exactly that state).
- C2's retry actually recovering: a real overnight dry-roll followed by a real `PRINT_START`
  reaching the hub-occupied retry, waiting out the initial toolchange, and re-parking the reserved
  lanes before they're used mid-print. This is the real regression test for that defect.
- C3's `.enabled`-independence against a genuinely live `switch_sensor` pin - simulated, since the
  pin has been dead since the RDM cutover (2026-08-21).
