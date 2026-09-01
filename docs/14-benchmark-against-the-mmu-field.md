# 14. Benchmark against the MMU field

What Happy Hare, AFC/Box Turtle, Tradrack and the ERCF lineage actually do when two actuators hold
one strand and one of them retracts — read out of their source, not their READMEs — and an honest
scoring of this project against them.

Written 2026-08-31, the day a CROSSBOW cut sequence ran its backward legs with ACE feed assist
(`FEED_OR_ROLLBACK` mode 2, **forward**) still enabled, ground the strand twice and lost a print.

---

## 1. The verdict, first

**The field validates mode 3, and states it more strongly than we did.**

Every mature project in this space drives the upstream actuator **backward, in sympathy, for the
whole of a backward move**, and every one of them derives that direction **from the sign of the
move** rather than from a decision taken at the call site. Two of them go further and make it
non-optional for exactly the class of hardware the ACE 2 Pro belongs to.

Nobody in the field free-wheels. Nobody relies on a buffer to absorb a toolchange-scale retract.
The only projects that *do* release the upstream drive are the ones whose hardware has a servo that
can physically declutch it — and the ACE has no such thing.

**And mode 3 is not a leap of faith — it is already in production.** The brief's assumption that
nobody calls it is wrong. `decay71/multiACE` (154 stars, active this week) and
`DnG-Crafts/U1-Ace` (from hakimio's ACE2 branch) both command it, and both do so the same way. See
§6.

Four corrections to the plan as stated, in descending order of importance:

1. **Do not add mode 3 "for every backward leg" as a hand-placed call.** HH, AFC, multiACE and
   U1-Ace all bind assist direction to the sign of motion and assist *lifetime* to the scope of the
   move — HH and AFC with a context manager whose `finally` always stops it, multiACE and U1-Ace
   with a velocity-following loop on `live_extruder_velocity`. Hand-placed enables are precisely
   the defect that produced today's failure: the enable was in one macro, the retract in another,
   and nothing owned the pairing.
2. **A physical buffer is not the answer, and believing it is would be a wrong turn.** Happy Hare's
   buffer parameters are hard-limited to 30 mm and its own Box Turtle defaults are 8 / 12 mm. Even
   the field's best buffer would have absorbed a tenth of today's retract. The buffer is a
   print-time slack regulator for retraction-scale moves; it was never the mechanism that handles a
   toolchange. Our ~3 mm is behind and worth improving, but it is a second-order fix and it does
   not substitute for the sympathy drive.
3. **There is a hard speed ceiling nobody has accounted for.** The driver's own docstring for the
   mode-3 builder records that *"the firmware clamps the assist to ~50 mm/s internally regardless of
   what is sent here, so any extruder retraction MUST stay below that."* multiACE independently caps
   its quantized rev speed at 50. `_TIP_SHAPING`'s snap retreat is `G1 E-28 F2700` = **45 mm/s**,
   inside the margin by 10%. Any future increase there silently outruns the assist and reproduces
   today's failure with mode 3 switched on.
4. **On FW 1.1.31 you cannot switch mode 2 → 3 in place** — the device returns `error_2` unless the
   slot is back in `ready` first, with a 5-30 ms settling window after the stop. Our helper already
   pre-stops, so this is a latent trap rather than a live one, but any shortcut that dispatches
   mode 3 over a live mode 2 will fail intermittently. §6.3.

---

## 2. What the field actually does

### 2.1 Happy Hare: bind the gear stepper to the extruder's motion queue

HH's answer is not an "assist". It is Klipper-level synchronisation — the gear stepper is attached
to the extruder's motion queue, so it follows every extruder move in **both** directions, 1:1, with
no coordination logic at all.

`extras/mmu/mmu_filament_movement.py:3707`:

```python
def _sync_gear_to_extruder(self, sync):
    ...
    if sync:
        self.drive().sync_mode(DRIVE_GEAR_SYNCED_TO_EXTRUDER)
    else:
        self.drive().sync_mode(DRIVE_UNSYNCED)

    if sync:
        self._adjust_gear_current(percent=u.p.sync_gear_current, reason="for extruder syncing")
    else:
        self._restore_gear_current()  # 100%
```

Two things fall out of those ten lines.

**Direction is not a decision.** A synced stepper cannot be pointed the wrong way; there is no
enum, no mode, no call site that could get it backwards. Today's bug is not expressible in HH's
architecture.

**Torque is deliberately reduced while synced.** `config/base/mmu_parameters.cfg:371`:

```
sync_gear_current : ...   # % of gear_stepper current (10%-100%) to use when syncing with extruder during print
```

with the header above it reading *"If you normally run with maxed out gear stepper current consider
reducing it with 'sync_gear_current'."* HH runs the upstream motor **weaker** than the extruder
while both hold the strand, so that when they disagree the upstream motor slips rather than
buckling the filament. The ACE cannot do this — its current is set by its own firmware — which is a
capability gap, not a design choice we made.

### 2.2 Happy Hare: sympathy is *mandatory* for hardware like ours

`extras/mmu/mmu_unit.py:107-120` sets `filament_always_gripped=True` for
`VENDOR_BOX_TURTLE`, `VENDOR_NIGHT_OWL`, `VENDOR_3MS`, `VENDOR_QUATTRO_BOX`, `VENDOR_KMS`,
`VENDOR_EMU`, `VENDOR_QIDI`, `VENDOR_ANGRY_BEAVER`, `VENDOR_VVD` — every design whose gear cannot
let go of the filament. `mmu_hardware.cfg:249` defines it as *"1 = Filament is always trapped by
MMU (most type-B designs), 0 = MMU can release filament."*

For those units, sync is not a preference. `mmu_filament_movement.py:3762`:

```python
must_sync = (
    extruder_check_enabled
    and filament_in_extruder
    and u.filament_always_gripped
)
...
elif must_sync:
    sync = True
```

and at line 3792, even the print-time setting is overridden:

```python
sync = bool(u.p.sync_to_extruder or u.filament_always_gripped)
```

**The ACE 2 Pro is exactly a `filament_always_gripped` unit.** It has no servo, no declutch, no
free-wheel. HH's rule for that class is: from the moment filament is past the extruder entry until
it leaves, the upstream drive follows the extruder, unconditionally, in both directions. That is
the field's position, and it is stronger than "use mode 3 on backward legs" — it is "the upstream
drive is never idle while the strand is shared."

Our `_ACE_REQUIRE_ASSIST` guard is the same instinct: refuse to move the extruder unless the ACE is
observed following. It is the right idea, correctly derived, and it was defeated by having only one
direction of "following" available to it.

### 2.3 Happy Hare: the espooler proves the pattern for a discrete assist

The espooler is HH's closest analogue to the ACE's assist modes — a dumb motor that is turned on
and off rather than stepped. `mmu_filament_movement.py:3126`:

```python
if abs(dist) >= u.p.espooler_min_distance and speed > u.p.espooler_min_stepper_speed:
    if dist > 0 and ESPOOLER_ASSIST in u.p.espooler_operations:
        espooler_operation = ESPOOLER_ASSIST
    elif dist < 0 and ESPOOLER_REWIND in u.p.espooler_operations:
        espooler_operation = ESPOOLER_REWIND
...
try:
    yield self
finally:
    self._wait_for_espooler = False
    if espooler_operation != ESPOOLER_OFF:
        self.espooler().set_operation(self.gate_selected, 0, ESPOOLER_OFF)
```

`_wrap_espooler` is a context manager. **Direction comes from `sign(dist)`. Lifetime comes from the
`with` block.** Assist cannot outlive its move, and cannot point the wrong way, because neither is
a human decision.

This is the shape our fix should take. `ACE_ENABLE_FEED_ASSIST` in `crossbow.cfg:379` and the
`G1 E-38.1` at line 400 are 21 lines apart with a guard between them, and the guard checks
*whether* the ACE is assisting, not *which way*.

### 2.4 AFC: same two mechanisms, and it syncs before the cut specifically

AFC does both of HH's things. The lane stepper is synced to the extruder
(`extras/AFC_stepper.py:302`, `self.extruder_stepper.sync_to_extruder(th_extruder_name)`) with a
current change on each transition (`set_print_current()` / `set_load_current()`), and the espooler
runs direction-aware inside a context manager (`extras/AFC_lane.py:770`):

```python
@contextmanager
def assist_move(self, speed, rewind, assist_active=True):
    if assist_active:
        if rewind:
            value = self.calculate_pwm_value(speed, True) * -1
        else:
            value = self.calculate_pwm_value(speed)
        ...
        self.espooler.assist(value)
    try:
        yield
    finally:
        if assist_active:
            self.espooler.assist(0)
```

And the unload path, `extras/AFC.py:2064`, is a line-for-line answer to today's failure:

```python
# Disable the buffer if it's active.
cur_lane.disable_buffer()
# Synchronize the extruder stepper with the lane.
cur_lane.sync_to_extruder()
cur_lane.select_lane()
self.do_tool_cut_tip_form(cur_lane, cur_extruder)
```

**AFC syncs the lane to the extruder immediately before the cut/tip-form**, so every retract inside
`do_tool_cut_tip_form` — including the big one — is followed backward by the lane motor. Later, at
line 2109, the move off the extruder gears is wrapped:

```python
with cur_lane.assist_move(cur_extruder.tool_unload_speed, True, cur_lane.assisted_unload):
    self.move_e_pos(cur_extruder.tool_stn_unload * -1, cur_extruder.tool_unload_speed, "Buffer Move")
```

`rewind=True`, a negative `move_e_pos`. Sympathy drive plus rewind assist, scoped to the move.

### 2.5 The declutchers: ERCF, Tradrack, and why they are not a counter-example

The ERCF lineage and Tradrack put a servo on the gear that physically lifts it off the filament.
HH models this as `filament_always_gripped: 0`, and `reset_sync_gear_to_extruder` then calls
`self.selector().filament_release()` when it is not synced (`mmu_filament_movement.py:3813`).

This looks like a third option — "free-wheel it" — and it is worth being precise about why it is
not available to us. It is not that we chose not to; **the ACE has no clutch, no servo, and no
neutral. An idle ACE lane is a clamped lane.** That is a hardware fact, and it puts us in the
`filament_always_gripped` class where HH forces sync. Divergence here is forced, not chosen.

The useful corollary: because a declutch is impossible, mode 3 is not one of several acceptable
answers on this machine. It is the only one.

### 2.6 The buffer is a print-time regulator, not a toolchange mechanism

The brief asked whether the field's consensus is "solve this mechanically." It is not, and the
numbers are unambiguous.

| project | buffer travel | source |
|---|---|---|
| HH generic sync-feedback buffer | `range` 0–30, default 10; `maxrange` 0–30, default 12 | `installer/Kconfig.sync_feedback_buffer:84-98` |
| HH Box Turtle / TurtleNeck v1+v2 | `range` **8**, `maxrange` **12** | `installer/mmu_types/Kconfig.box_turtle:65-68` |
| this machine | **~3** | `tip_shaping.cfg:163` comment |

HH's parameter range is capped at 30 mm. Its own recommended buffer is 8–12 mm. Today's retract was
65.8 mm. **No buffer in the field would have saved it.**

What the buffer is actually for is stated by the parameter that reads it,
`mmu_parameters.cfg:390`:

```
sync_feedback_extrude_threshold : ...  # Extruder movement (mm) for updates (keep small but set > retract distance)
```

"> retract distance" means *print* retracts — a few mm. The buffer damps the running mismatch
between two motors that are already turning together; it is a trim, downstream of sync. HH and AFC
both use it that way: HH shifts the gear stepper's rotation distance from tension/compression
switches (`sync_feedback_speed_multiplier`, recommended 5%), and AFC's TurtleNeck adjusts a
rotation-distance multiplier from its advance/trailing switches (`AFC_buffer.py:350`,
`set_multiplier`, *">1 advances, <1 trails"*). Neither treats it as slack storage.

Note also `AFC.py:2065`: AFC **disables** the buffer before the unload. The buffer is not in the
loop during a toolchange at all.

**Finding that outranks the code fix: no. The mechanical answer is second-order.** Going from 3 mm
to a TurtleNeck-class 8–12 mm buys margin on print-time mismatch and would make a future
sync-feedback sensor possible, both of which are real. It does not address a 65.8 mm retract, and
treating it as the fix would leave the actual defect in place.

---

## 3. Does anyone in the ACE ecosystem call mode 3?

**In this project: no. `_start_rollback_assist_verified` exists at `instance.py:822` and has zero
callers, and no G-code command reaches it.** Every assist call site in the live config is mode 2:

```
crossbow.cfg:379          ACE_ENABLE_FEED_ASSIST T={cur}      then G1 E-38.1 at :400
park.cfg:90               ACE_ENABLE_FEED_ASSIST T={cur}
tip_shaping.cfg:125       ACE_ENABLE_FEED_ASSIST T={ace_tool} then net -48 mm
brush_prime.cfg:196       ACE_ENABLE_FEED_ASSIST T={_cur}
pause_resume.cfg:80       ACE_ENABLE_FEED_ASSIST T={cur}
start.cfg:760             ACE_ENABLE_FEED_ASSIST T={cur}
ace_purge.cfg:542         _ACE_REQUIRE_ASSIST T={_pcur} WHO=PURGE
```

Mode 3 is reachable only through the diagnostic `ACE_RAW_FEED T= MODE=3`, added 2026-08-28.

The builder's own docstring already records the hardware proof
(`protocol_ace2.py:687`): *"With postgear engaged, mode 3 plus 5mm of extruder retract cleared the
switch instantly where 350mm of plain mode-1 unwind had done nothing."*

**Elsewhere in the ecosystem: yes, two projects.** See §6 — this is where the assumption behind the
brief breaks, and where the useful firmware detail lives.

---

## 4. The failure, in the field's terms

Three separately-authored backward legs run under one forward assist:

| leg | `crossbow.cfg` | distance | extruder direction | ACE commanded |
|---|---|---|---|---|
| hot retract to cut position | `:400` `G1 E-{cut_h} F1500` | 38.1 (blade 46.1 − stub 8.0) | back, 25 mm/s | **forward** (mode 2) |
| pushback return | `:436` `G1 E-{push}` | 21.1 | back | **forward** (mode 2) |
| park blade face → post-gear | `:445` `FORCE_MOVE DISTANCE=-5.6` | 5.6 | back, 5 mm/s | **forward** (mode 2) |

Sum of backward legs: **64.8 mm**, against a ~3 mm buffer and an ACE actively feeding into it.

The guard between the enable and the retract, `_ACE_REQUIRE_ASSIST`, **passed** — it tests
`feed_assist_slot == t` and `s.status in ('assisting', 'shifting')`, neither of which carries a
direction, and then prints *"feed assist observed on T{t} — ACE will follow the extruder."* It was
following, forward, into a 3 mm buffer.

The instructive part is that **no single leg looks wrong**. Each was authored for its own reason,
each is individually modest, and the assist enable is 21 lines above the first of them with a guard
in between. This is what HH's and AFC's context managers exist to prevent: the pairing of "assist
on" with "this move" is not something a reader can hold across a 60-line macro.

`_TIP_SHAPING` has the same shape and one extra wrinkle. It enables mode 2 (`:125`), runs a net
−48 mm (`:145-158`), and then *also* winds the ACE in by 48 mm (`:167`, `ACE_RETRACT LENGTH=48`) —
the "wind-in" the plan rejected. Given the C25 bench result (assisted retracts move the strand
roughly 1:1 after ~7 mm of slack take-up), that wind-in is **double-counting**: the assist has
already taken up most of the 48 mm, and the explicit retract pulls it again. The macro's own
comment flags it as unresolved (*"C24 ... left in place until the C25 assist-rollback test decides
whether it is harmful"*). C25 has since answered; the wind-in should go when mode 3 lands, not
survive alongside it.

---

## 5. Scorecard

| dimension | verdict | evidence |
|---|---|---|
| **Retract coordination** | **behind — including behind our own upstream** | HH/AFC sync the upstream drive bidirectionally by construction; multiACE/U1-Ace flip mode 2↔3 off live extruder velocity; ACEPRO upstream disables assist entirely before any retract and overspeeds the extruder 10%. All four have *a* coherent answer. We enable forward assist by hand and retract 64.8 mm underneath it. `mmu_filament_movement.py:3126`, `AFC_lane.py:770`, `multiace ace.py:5245`, ACEPRO `manager.py:1308` vs `crossbow.cfg:379`. |
| **Following the upstream driver's own guards** | **behind** | Upstream ships `_ensure_feed_assist_off_for_motion(slot, "retract")` in every motion primitive and a comment naming this exact hazard. Our macro layer bypasses it. §6.5. |
| **Upstream torque limiting** | **behind — hardware-forced** | `sync_gear_current` (HH) and `set_print_current`/`set_load_current` (AFC) run the upstream motor weaker than the extruder so it slips instead of buckling. The ACE's current is firmware-owned; we have no equivalent and cannot build one. |
| **Buffer** | **behind, second-order** | ~3 mm vs TurtleNeck 8/12 mm (`Kconfig.box_turtle:65`). Real, worth fixing, would not have prevented today. |
| **Tip forming and cutting** | **level** | Cut geometry is fully derived (blade 46.1, stub 8.0, melt 22.0) with a face-cap and fragment-floor on the pushback. AFC ships `retract_length: 20 / pushback_length: 15` as flat constants (`AFC_Macro_Vars.cfg:86,101`); HH uses `blade_pos − 5` (`Kconfig.cut_tip:35`) but then subtracts the tracked residual so its *effective* retract shrinks by the fragment already in the bore — a refinement we do not have. Our `cut_tip_validated_min` / `cut_tip_full_dia_mm` taper guard has no counterpart in either. Our 38.1 mm cut is longer than AFC's 20 only because the blade sits at 46.1; geometry, not tuning. |
| **State tracking** | **behind on maturity, ahead on doctrine** | HH has a mature `filament_pos` state machine with `MMU_RECOVER`, per-gate persistence, soak tests, and a `mmu_dev_test` harness. Ours has better *rules* — commit-after-verified-success, an explicit intent journal, "known at all times includes knowing that you don't know" — but far less code and no automated recovery. HH's `mmu_print_state_machine.py` + 30 command modules vs our macro-level flags. Honest gap. |
| **Sensor coverage** | **level to ahead** | entry + post-gear + hub switch + hub encoder + four ACE lane sensors. HH's full build assumes gate + gear + extruder + toolhead sensors plus an encoder; AFC assumes lane + hub + toolhead pre/post. Comparable. Our `ACE_AUDIT` truth table (`ace_audit.cfg`, EMPTY/PARTIAL/LOADED/ANOMALY, sensors before flags) is a cleaner statement of the invariant than anything in either project's docs. |
| **Purge strategy** | **behind — I got this wrong first time** | I assumed the stub credit was ours. It is not. HH tracks the severed fragment as a first-class status field, `printer.mmu.extruder_filament_remaining`, and spends it in **three** places: subtracted from the next cut retract (`mmu_cut_tip.cfg:80`), from the pushback (`:100`), and added to the purge (`mmu_purge.cfg:62`). It also has our prime-tower skip — `force_purge_standalone: 0 # 0 = Slicer wipetower in print else standalone`. And it goes further than we do by reading the slicer's per-pair purge matrix (`printer.mmu.slicer_tool_map.purge_volumes`) and computing `toolchange_purge_volume` from colour distance, where we use a single scalar. Our `cut_stub_pending` is a save_variable read by one macro. Level on intent, behind on depth. |
| **Guards against damage** | **mixed, honestly behind on execution-verification** | The 48 mm extraction cap with a fault-report error message (`ace_toolchange.cfg:299-303`) is genuinely good and explicitly refuses to be raised. But `_ACE_REQUIRE_ASSIST` verifies *that* assist is on, not *which way*; several guards measure commanded rather than executed motion; and `_CROSSBOW_CUT_COMMIT` admits its switch sits above the blade and "agreed with every failure mode except over-travel." HH homes moves against endstops and measures with an encoder — executed motion, by construction. |

---

## 6. Ecosystem survey — mode 3 is already in production elsewhere

The brief assumed nobody in the ACE ecosystem calls mode 3. **That is wrong, and the projects that
do call it have converged on an architecture better than the one we were about to build.**

### 6.1 Who calls it

| project | mode 3? | how |
|---|---|---|
| `decay71/multiACE` (`multiace/klipper/extras/ace.py`, HEAD `9a92a3b`) | **yes** | velocity-following loop; `want_mode = 2 if direction == 'fwd' else 3` |
| `DnG-Crafts/U1-Ace` (`src/ace2.py`), from **hakimio**'s `U1-Ace@ace2` | **yes** | `_FEED_MODE_UNWIND_ASSIST = 3`; 4 Hz loop on `live_extruder_velocity` |
| **ACEPRO lineage — our upstream** | **no** | modes 0/1/2 only; decoder knows `4: "rollback_assisting"`, nothing sends it |
| `Jupsi/ACEPRO`, `szkrisz/ACEPROSV08`, `swilsonnc/ACEPROK1Max` | no | same as upstream |
| `ANYCUBIC-3D/Klipper-go` — **Anycubic's own** | n/a (ACE1) | disable assist, then *alternating sequential chunks* |
| `utkabobr/DuckACE`, `agrloki/ValgACE`, `BunnyACE`, `SnapAce`, `cosmoace` | no | ACE1 JSON; ValgACE caps `MODE` at `maxval=1` — structurally unreachable |
| `paulredmond79/ace2ace2` | no | ACE2 protobuf but incomplete; state enum stops at `0x03` |
| `printers-for-people/ACEResearch` | n/a | RE notes, no driver |

There are only four ACE2-protobuf implementations in existence: ACEPRO, U1-Ace, ace2ace2, multiACE.
Two of the four command mode 3.

`ace2-protocol-findings.md` line 177 says mode 3 has no caller anywhere. **That is true only of the
ACEPRO lineage and should be corrected.**

### 6.2 The shape they converged on: a velocity follower, not a scripted call

Both projects independently arrived at the same thing, and it is not "call mode 3 on the backward
legs". It is a **polling loop on `motion_report.live_extruder_velocity` that flips the ACE between
mode 2 and mode 3 to match the sign of the extruder's live velocity**, with a debounce and a
print-time suppression.

multiACE, ~line 5245: `want_mode = 2 if direction == 'fwd' else 3`. U1-Ace: `target_mode = 'feed'
if velocity > 0 else 'unwind'`, evaluated at 4 Hz, with `assist_mode_confirm_time` (default 1.0 s)
so a slicer retract blip never flips the mode — a reversal that reverts inside the window is
explicitly abandoned.

**Both suppress mode 3 while printing.** U1-Ace: `if target_mode == 'unwind' and self._printing:
target_mode = 'feed'`. multiACE computes `want_mode=3` during a print and deliberately does not
dispatch it (`"mode->%d (no dispatch, not in unload)"`), arming reverse assist only via
`_v2_arm_fa_for_unload`, whose docstring is the clearest statement of our problem anyone has
written:

> *"enabled during unload so V2 actively rev-assists the ~10s tip-form retract **instead of braking
> the filament**"*

This is the same conclusion as HH's `_wrap_espooler` reached from a different direction: direction
follows the move, and it is machinery, not a call site.

### 6.3 Three hardware facts we did not have

**a. FW 1.1.31 rejects an in-place 2→3 transition with `error_2`.** multiACE
`_v2_dispatch_mode_switch`, line 4912:

> *"V2 FW 1.1.31 rejects in-place mode transitions with error_2. The slot must be in `ready` before
> the new mode dispatch is accepted. ... pre-stop has a ~5-30 ms post-stop settling window; if the
> FIFO gap between SEND stop and SEND mode-set falls inside that window, V2 still returns error_2.
> Retry after 50 ms."*

That is our exact firmware version. `_start_rollback_assist_verified` calls
`_ensure_feed_assist_off_for_motion` first so it is structurally safe, but its retry pause is 1.0 s
where 50 ms is enough.

**b. The assist speed should track demand, not be a constant.** multiACE quantizes:
`_v2_quantize_velocity(v, 'rev')` = `max(1, min(50, ceil(|v|/5)*5))` — floor 1, step 5, **cap 50**.
Their stated reason is that a constant speed trips the firmware's internal motor-stall detection
during slow tip-form retracts. We send a fixed 40. Their independent 50 cap corroborates the
~50 mm/s clamp recorded in our own builder docstring.

**c. Returning to forward uses `start_feed_assist`, not a raw mode-2 dispatch** — because that puts
the device in a "passive armed" state that does not expect continuous encoder motion, avoiding an
`assist_error` trip while idle after the reverse phase.

### 6.4 The wind-in is partly vindicated — as a *post-stop* step

`DnG-Crafts/U1-Ace` issue #45, DnG-Crafts describing stock ACE behaviour:

> *"the way the ace is supposed to work is **the assist stops and then the filament winds back**,
> then the next filament rolls forward for the next assist, so the previous buffer lane is pulled
> back removing the possibility of this issue. the way we are using it for the u1 is we are just
> swapping assist lanes **without pulling the filament back** ... the buffer sometimes jams"* —
> ~2 in 2000 swaps.

**Stock ACE firmware always winds filament back after stopping assist.** DnG reproduces it: their
`_stop_feed_assist` fires a **12 mm mode-1 rollback immediately after every STOP**. Anycubic's own
wiki names the failure — *"the materials are blocked in the buffer mechanism ... if the buffer
mechanism does not retract properly during feeding"* — with Allen-key teardown instructions.

This is a correction to the plan and to my §7 as first drafted. The wind-in was rejected as the
*alternative* to mode 3, and rightly: 48 mm of buckle-then-relieve is not a substitute for sympathy
drive. But a **short wind-back after STOP** is a different thing, it is what the device expects, and
we do not do it. `_ACE_RELEASE_ASSIST_AFTER_PARK` currently just stops.

### 6.5 We are behind our own upstream on this

ACEPRO upstream `manager.py:1308-1326`:

```python
# Disable feed assist BEFORE any motion — feed assist pushes filament forward
# and would fight both the extruder retract and the ACE retract that follow.
instance._disable_feed_assist(local_slot)
# Start extruder retraction (10% faster for slack)
self._extruder_move(-abs(retract_length), retract_speed * 1.10, wait_for_move_end=False)
# Start ACE retraction ...  (mode 1)
```

plus `_ensure_feed_assist_off_for_motion(slot, "retract")` as a hard guard inside every motion
primitive (`instance.py:647`, `:769`).

**Upstream identified this exact hazard, wrote the comment, and installed a guard. Our config
overrides it** — `crossbow.cfg` enables mode 2 and hands the retract straight through. Upstream's
open-loop answer (extruder deliberately overspeeding the ACE by 10% so slack is pulled out rather
than pushed in) is inferior to mode 3, but it is coherent, and it would not have ground the strand
today. That is the least comfortable finding in this document.

---

## 7. What to change, in order

1. **Make direction a property of the move, not of the call site.** A single wrapper — the ACE
   analogue of `_wrap_espooler` — that takes a distance, picks mode 2 or mode 3 from its sign,
   starts the assist, runs the move, and stops the assist in a `finally`. Every present
   `ACE_ENABLE_FEED_ASSIST` + `G1 E-x` pair becomes one call. This is the fix; the rest are
   refinements.
2. **`_ACE_REQUIRE_ASSIST` will BLOCK mode 3 — fix it before wiring anything.** It accepts only
   `s.status in ('assisting', 'shifting')` (`ace_toolchange.cfg:142`). `rollback_assisting` is not
   in that tuple, so the guard raises on a correctly-assisted reverse move — and it passed the
   sequence that ground the strand today, printing *"ACE will follow the extruder"* while the ACE
   was pushing the other way. It needs to accept `rollback_assisting` **and** take the intended
   direction as a parameter, refusing `assisting` on a backward move and `rollback_assisting` on a
   forward one. This is the first edit, not the second.
3. **Add a speed assertion at 50 mm/s.** The firmware clamp is undocumented outside one docstring
   and `_TIP_SHAPING` sits at 45 mm/s. A guard that refuses any assisted retract faster than the
   clamp costs nothing and closes a trap that will otherwise be sprung by a future tuning change.
4. **Cut the `tip_shaping.cfg` wind-in from 48 mm to a ~12 mm post-STOP wind-back**, and move it
   after the stop rather than treating it as slack recovery. At 48 mm it double-counts against the
   assist (C25: assisted retracts move the strand ~1:1 after ~7 mm of take-up). But deleting it
   outright would be wrong: stock ACE firmware always winds back after stopping assist, and
   `DnG-Crafts/U1-Ace` reproduces that with a 12 mm mode-1 rollback after every STOP specifically
   because omitting it jams the internal buffer (§6.4). `_ACE_RELEASE_ASSIST_AFTER_PARK` should
   gain the same step.
5. **Match the mode-3 speed to demand** rather than sending a fixed 40 — multiACE's
   `max(1, min(50, ceil(|v|/5)*5))`, to avoid tripping the firmware's stall detector on slow
   tip-form retracts. And drop the retry pause from 1.0 s to 50 ms.
6. **Buffer travel 3 mm → 8–12 mm** when the mechanics are next open. Field-standard, and a
   precondition for ever fitting a sync-feedback sensor. Not urgent.

---

## Sources

* Happy Hare — `moggieuk/Happy-Hare`: `extras/mmu/mmu_filament_movement.py`
  (`_sync_gear_to_extruder` :3707, `reset_sync_gear_to_extruder` :3741, `_wrap_espooler` :3105),
  `extras/mmu/mmu_unit.py` :98-120, `config/base/mmu_parameters.cfg` :364-397,
  `config/base/mmu_hardware.cfg` :249, :940-954, `installer/Kconfig.sync_feedback_buffer` :84-98,
  `installer/mmu_types/Kconfig.box_turtle` :55-70.
* AFC — `ArmoredTurtle/AFC_Klipper_Add_On`: `extras/AFC_stepper.py` :302-326,
  `extras/AFC_lane.py` :770-796, :832, `extras/AFC.py` :2060-2115, `extras/AFC_buffer.py` :350-443,
  `config/AFC_Macro_Vars.cfg` :86-102, `config/macros/Cut.cfg` :104-238.
* ACE ecosystem — `decay71/multiACE` `multiace/klipper/extras/ace.py` (HEAD `9a92a3b`:
  `_v2_arm_fa_for_unload` :4552, `_v2_quantize_velocity` :4882, `_v2_dispatch_mode_switch` :4891
  and its docstring :4912, velocity tracker :5245-5300, `A_FEED` :13088);
  `DnG-Crafts/U1-Ace` `src/ace2.py` (from hakimio, commit `e55e7bc` 2026-06-07) and
  `hakimio/U1-Ace@ace2` `src/ace2_protocol.py`, `src/ace_device.py`;
  [U1-Ace issue #45](https://github.com/DnG-Crafts/U1-Ace/issues/45);
  hakimio `ace2-pro-shell.py` gist `551915aa02b7e248721bed672ad46e0b`;
  ACEPRO upstream `manager.py` :1308-1326, `instance.py` :647, :769;
  `ANYCUBIC-3D/Klipper-go` `project/extras_ace.go` ~:760;
  [Anycubic ACE Pro blocking wiki](https://wiki.anycubic.com/en/fdm-3d-printer/kobra-s1/ace-pro-blocking).
* This project: `~/printer_data/config/macros/filament/crossbow.cfg`, `.../tip_shaping.cfg`,
  `.../park.cfg`, `ace_toolchange.cfg`, `ace_purge.cfg`, `ace_audit.cfg`, `variables.cfg`;
  ACEPRO driver `protocol_ace2.py` :687-725, `instance.py` :822-875.
