# Filament path — fixes applied 2026-08-30

All changes are on the printer (`simon@10.49.9.130`). Both trees now have git history:
`~/ACEPRO` (driver) and `~/printer_data/config` (macros, newly initialised — it had none,
which is why the `ptfe_postgear_to_nozzle` overwrite could never be dated or attributed).
Every in-place patch also left a `.bak-*` beside the file.

---

## The headline: the toolchange probably never jammed

`_tandem_extract`'s stall detector measured **commanded** motion, not executed motion.

`run_script_from_command("FORCE_MOVE ...")` does not block — Klipper's `force_move.manual_move`
calls `toolhead.dwell()`, which advances `print_time` and returns. `pulled` therefore counted
millimetres *queued*. The loop reached its 16 mm threshold in a few tens of milliseconds of wall
time, while the extruder had physically moved **zero**, then read the hub encoder, correctly saw
0 pulses, and declared `strand NOT moving`.

The log shows exactly four `FORCE_MOVE` lines inside a single one-second stats interval, then the
raise. Runs that passed earlier passed only because the ACE's own unwind happened to have a head
start from `_retract_async_verified`'s 0.1 s accept-polling — **the guard was measuring ACE
acknowledgement latency, not filament movement.**

Then the abort message compounded it:

> `[SWAP] T0 still at toolhead_entry after 140mm of extraction`

140 is `_ACE_SWAP_VARS.extract_mm`, the **cap**. 16 mm was commanded; ~0 mm executed. That false
reading is what justified the destructive recovery that ended with a free-floating strand.

---

## Fixes

### Motion measurement

| Fix | File | What was wrong |
|---|---|---|
| Stall check drains the motion queue before every measurement | `instance.py` `_tandem_extract` | measured queued, not executed motion |
| Stall check is continuous with an incremental baseline | same | `stall_check_mm = 1e9` disabled the check after one pass, so a stall starting after the first window ran to the cap — the shape of the 08-27 120 mm and 140 mm grinds |
| Drain is unconditional, not gated on having an encoder | same | with no hub encoder the loop never drained, so the entry-switch read was arbitrarily stale |
| Extruder finishes before the ACE is stopped | same | every *successful* extraction ended with the extruder pulling alone against a stopped lane, ~20 mm of queued retraction after `_stop_retract` |
| Both load crossing loops drain per chunk | `instance.py` | same race mirrored: could drive the tip past post-gear toward the melt zone, or false-fail a healthy load at the 60 mm cap |
| Abort message no longer reports the cap as the distance pulled | `ace_toolchange.cfg` | the lie that justified the destructive recovery |

### Wrong actuator / grind paths

| Fix | File | What was wrong |
|---|---|---|
| `smart_unload` clears the toolhead via `_tandem_extract` | `manager.py` | disabled assist, then raced an unguarded extruder move against an unverified ACE retract — the original grind pattern, still reachable via `ACE_CHANGE_TOOL TOOL=-1` (which the driver's own errors recommend), `ACE_SMART_UNLOAD`, `_ACE_HANDLE_PRINT_END`, and plausibility-mismatch recovery |
| ACE assist guard hoisted out of `{% if post %}` | `park.cfg` | with post clear the guard was skipped entirely, and the forward seek then pushed up to 200 mm extruder-only into a clamped lane — whose only open volume is the hub |
| Purge re-verifies assist before extruding | `ace_purge.cfg` | tens of mm of extrusion on an assist enabled back during the load that nothing re-checked |
| `ACE_COLD_RETRACT` uses the guarded tandem | `ace.cfg` | last caller of the fire-and-forget retract whose FORBIDDEN is invisible to the caller; also drove the extruder 10% faster than the ACE |

### Protocol

| Fix | File | What was wrong |
|---|---|---|
| Mode-3 rollback assist added | `protocol_ace2.py`, `instance.py` | **it had no builder at all.** The driver could not do reverse assist, which is why the tandem was still hand-syncing both ends |
| Verified starters retry `STOP_SETTLE_ATTEMPTS = 35` | `instance.py` | 8 attempts (~8 s) could not ride out the ~20-30 s post-STOP reject window |
| The crossbow comment corrected in place | `crossbow.cfg` | **all three "stop" builders emit an identical opcode-9 `STOP_FEED_OR_ROLLBACK` frame.** `STOP_FEED_ASSIST` is not in the command spec table at all — only in a docstring. The 08-28 fix changed which macro was called but not one byte on the wire; the reject window was renamed, not removed |

### Geometry and the cut

| Fix | Value | What was wrong |
|---|---|---|
| `ptfe_postgear_to_nozzle` | 51.7 → **80** | held the CROSSBOW geometry sum (`5.6 + 46.1`) instead of the measured push-to-first-flow distance. `entry_to_nozzle` now computes to exactly 100.0, satisfying the invariant `ace.cfg` states in its own comments. Purge 28.5 mm → 57.0 mm |
| `crossbow.cfg` reads `crossbow_postgear_to_blade` directly | — | derived a geometric quantity from a functional one. Restoring the correct 80 would otherwise have made `park_back` 33.9 mm instead of 5.6 mm |
| `cut_tip_stub` | 4.5 → **8.0** | 0.9 mm inside the taper (`cut_tip_full_dia_mm = 5.4`), so every face was undersized by design |
| Stub guard tests the stricter measurement | — | the guard was `stub < cut_tip_deform_mm` = `4.5 < 4.5`, disarmed because the measurement had been lowered to match |
| `CROSSBOW_CUT_TIP` commits parked/hot only after a fresh post-gear read | `_CROSSBOW_CUT_COMMIT` | wrote `parked=1 hot=0` unconditionally with nothing confirming the face landed — the PARKED_UNKNOWN trap the rest of the config defends against |

### Detection

| Fix | What it catches |
|---|---|
| `ACE_AUDIT` detects **ORPHANED STRAND** | the owning lane's ACE slot reads `empty` while filament still fills the path — the ACE has released a strand the driver still believes it owns, so neither actuator can move it. Nothing detected this: `reconcile_stale_current_index` deliberately keeps `current_index` in that state (correct for printing out a tail), and the preload guard's invariant check only ever compared toolhead switches against `current_index`, never a lane's own slot status. Distinguished from the legitimate tail-consumption case by print state |
| Drying roller never runs during a print | an `ACE_RAW_FEED` mid-print blocks the Klipper motion queue for the RS-485 round trip; a 50 mm leg at 15 mm/s visibly stalled the toolhead on layer 2. The default is now persisted in the config, not just set at runtime |

---

## Corrections to earlier claims in this session

- The geometric/functional variable mix-up did **not** cause the undersized cut faces.
  `park_back` computed to 5.6 mm both before and after — the wrong derivation reached the right
  number by coincidence, and `park_back` only sets where the tip parks *after* the cut. It caused
  the purge shortfall only. The undersized face was `cut_tip_stub = 4.5`.
- The extraction failure was very likely **not** a physical jam. See the headline above.

---

# Cutter commit and stub credit — 2026-08-31

Static analysis and edits only; the machine stayed idle with the path clear throughout. Nothing
was moved, cut or heated. Committed as `a2d6bf3` on `fix/qa-2026-08-30`, touching only
`ace_purge.cfg` and `macros/filament/crossbow.cfg`. Backups: `*.bak-cutfix` beside each.

## The headline: a missed cut is NOT detectable on this toolhead

This supersedes the 2026-08-30 row claiming `_CROSSBOW_CUT_COMMIT` fixed the PARKED_UNKNOWN trap
by committing "only after a fresh post-gear read". The read was fresh. It was also **uninformative**.

Heights above the nozzle, all from the machine's own saved variables:

```
nozzle 0  <  melt zone 17..22  <  BLADE 46.1  <  post-gear 51.7  <  extruder nip 69.7  <  entry 71.7
```

**Both switches sit above the blade.** The severed fragment never rises past 46.1, so no sensor on
this toolhead can be reached by it. Severing is not observable, full stop — this is a sensor
limitation, not a macro defect, and no predicate written in any macro can lift it.

The retained face is no help either. Its whole trajectory through `CROSSBOW_CUT_TIP` is

```
0 -> cut_h (blade-stub, 38.1) -> cut at blade 46.1 -> pushback +N/-N net 0 -> park at 51.7
```

— at or below post-gear the entire way, with the strand continuous above it. So `post` reads TRUE
by construction, and `CROSSBOW_CUT_TIP` already refuses to start unless it does. The commit was
confirming its own entry condition. It fired identically on a blade that never bit, a retract that
ground, and a skipped pushback.

**Corroboration:** Happy Hare has no missed-cut detector either — issue #1009, still open — and its
unload only *warns* if the toolhead sensor stays triggered. The most mature MMU firmware in this
space reached the same conclusion on the same sensor set.

**The one real bit:** `post` FALSE means the strand ended up above 51.7, which the geometry does not
allow — over-travel or pull-out. That is a genuine fault signal and is now acted on. Its weakness is
the other end: a nominal park lands the face *exactly on* the trigger point, so marginal over-travel
reads FALSE on a good cut. Both readings are now treated as POSITION, never as proof about the blade.

**What would actually detect a missed cut** (none present, all needs-hardware):

| Method | Why it would work | Cost |
|---|---|---|
| A switch or optical gate BELOW the blade, ~30-44mm above the nozzle | Sees the fragment directly — the one thing no current sensor can reach | New sensor in a tight space on the EBB36 |
| Filament-width / presence sensor at the blade | Detects the gap the cut opens | Same |
| Extruder load/current signature during the pushback | A severed strand pushes a free body; an unsevered one pushes into the melt zone | TMC `SG_RESULT` on the extruder; noisy, needs calibration |
| Hub encoder differential across the cut | Indirect, and the fragment is not encoder-coupled | Probably not resolvable |

Until one exists, the honest position — now stated in the macro itself on every cut — is that the
commit is **on position, not on verification**.

## Fixes

| Fix | File | What was wrong |
|---|---|---|
| Commit says what it actually knows | `_CROSSBOW_CUT_COMMIT` | claimed to verify the cut; the geometry above shows it could not. Now labelled commit-on-position, with the proof in the macro header so the predicate is not "fixed" back |
| Failure branch INVALIDATES instead of preserving | same | not committing is not clearing. A previous `parked=1` survived alongside `post=false` — the exact PARKED_UNKNOWN the machine was found in on 08-30, and worse than a fresh lie because nothing wrote it. Now writes `parked=0` (which restores the heat check in `_ACE_PREPARE_FOR_RETRACTION`, the one consumer that acts on `parked==1` **alone**) and `hot=1` (never under-states what is in the toolhead) |
| Disabled sensors no longer quoted as evidence | same | printed `entry=False` while `toolhead_entry.enabled` is **false** — a stale value presented as a reading. Now prints `DISABLED` |
| Stub guard names the binding variable | `CROSSBOW_CUT_TIP` | tested `max(deform 4.5, full_dia 5.4)` but interpolated only `deform`. An operator raising the 4.5 the message named would never clear a guard binding on 5.4 |
| Stub credit cleared where it is SPENT | `ace_purge.cfg` | `cut_stub_pending` was zeroed ~80 lines before the purge that consumes it, so every skip — and the `_ACE_REQUIRE_ASSIST` raise — burned the credit without moving the stub, leaving the retry one stub short. First extrusion after it is the previous colour, into the part. Now cleared after the extrusion that displaces it, plus the two other sites that genuinely spend it (tower, load-already-sufficient) |
| Assist guard hoisted out of the unrestorable window | `ace_purge.cfg` | it sat between `SAVE_GCODE_STATE NAME=_ace_purge` and its only restore. Klipper has no `finally`, so a raise left the head hopped over the brush with an orphaned state and the waste ledger already charged. It reads only driver state, so moving it above the save costs nothing |

## Correction: the pushback cap defect does not hold

Reported as "raising the stub to 8.0 seats 5mm of the fragment inside the melt zone, weld risk on
the next load; `push_cap` has no stub term". The arithmetic is right and the insertion really did go
1.5mm -> 5.0mm. **The mechanism is not.** Two independent disproofs:

1. **The contact cannot move.** Through the push, the fragment's TOP and the retained face are the
   *same point* — the blade severed them there and the strand is what pushes the fragment. The only
   place the two can re-weld is that contact, and `face_cap` pins it at **25.0mm, 3.0mm clear of the
   melt zone top, identically at stub 4.5, 6.0 and 8.0**. Raising the stub moves the fragment's foot
   deeper but does not move the contact at all. A stub term would protect nothing.
2. **Deeper is the reference behaviour.** AFC's shipped profile for this exact toolhead (WWBMG dual
   sensor + CrossBow + Rapido 2HF) is retract 32 / pushback 27: fragment foot at **5.0mm**, the whole
   fragment inside the melt zone, and the retained face at 19.1 — *below* the melt top, which this
   config would refuse outright. Happy Hare does the same deliberately, to keep the fragment
   semi-liquid with its nail head past the PTFE/metal junction so the next load displaces it rather
   than ramming a cold plug into a cold-to-hot step (HH #163). At stub 8.0 this machine seats the
   foot at 17.0mm — the *bottom* of the melt zone, 12mm shallower than AFC.

The 2026-08-27 hotend clog, whose pieces Simon found on dismantling, happened with **no pushback at
all** and a 4.5mm fragment left cold above the blade — the shallow end of this spectrum, not the
deep end. Reducing insertion would have moved the machine further from the only configuration with a
track record.

**So pushback behaviour is unchanged at every stub in use** (push stays 21.10mm at stub 6, 8 and 10).
What was added is a *floor* — the fragment foot may not be driven below 10mm above the nozzle — which
does not bind today and exists so a large `STUB` cannot walk the fragment onto the tip; it binds at
stub 20 with a loud error. Plus reporting of where the fragment and the contact actually land.

**Standing caveat: none of this has ever run.** The pushback was written 2026-08-27 and recorded "not
yet run"; no fragment has been seated by this machine at any depth. Both 21.1 and the 10.0 floor are
untested. AFC's 27mm is the obvious candidate to try, and would need hardware.

## Verification

The shipped templates were rendered in klippy's own `jinja2` against mock printer state — not
re-implemented — before deployment:

- 24/24 templates compile (7 crossbow, 17 purge).
- Pushback: push 21.10 at stub 6/8/10 (unchanged); floor binds at stub 20 (16.1mm, error raised);
  contact/face constant at 25.0mm across all stubs, which is disproof (1) above, executed.
- Purge emission order, before: `CLEAR-CREDIT -> SAVE_STATE -> ASSIST-GUARD(raises) -> EXTRUDE -> RESTORE`.
  After: `ASSIST-GUARD(raises) -> SAVE_STATE -> EXTRUDE -> CLEAR-CREDIT -> RESTORE`.
- Cold-nozzle and unhomed skip paths, before: credit cleared with no purge. After: credit retained,
  retry length quoted net of it so the operator is not charged twice.
- Post-deploy: `/printer/info` = `ready`; `filament_parked=0`, `filament_loaded_hot=0`,
  post-gear false, entry false/disabled, unhomed — identical to pre-deploy.

## Open, needs hardware

- Every behavioural claim above about the *cut* is needs-hardware: nothing here was run with
  filament. The commit-on-position rewrite is observable on the next real cut as two new console
  lines; the invalidation branch only shows up on a genuine over-travel.
- `cut_stub_pending = 4.5` is **live and orphaned right now** — the path is clear, so no stub exists,
  and 4.5 predates the stub raise to 8.0. The 2026-08-30 recovery cleared `parked`/`hot`/lane state
  but missed this one. It will be spent on the next purge as 4.5mm of over-purge. Harmless, but it is
  proof the credit was never tied to a physical fact. Set it to 0 by hand.
- A sensor below the blade is the only thing that turns the cut from assumed into verified.

---

# Print boundary - fixes applied 2026-08-31

Scope: `macros/print/{start,end,cancel}.cfg`, `ace_toolchange.cfg`, `ace_unload.cfg`, `ace.cfg`.
Committed as `3a0ab31` in `~/printer_data/config`; the full rationale is a `git notes` entry on
that commit (the message body was truncated by a shell quoting fault at write time).

## The headline: two boundary fixes had been wired to a macro nothing calls

`_ACE_SUPPRESSION_DISARM` and `ACE_RECONCILE_TARGET` were added to `PRINT_START`. The slicer does
not call `PRINT_START`. `start.cfg` says so itself at line 833 - the monolithic macro is "left
intact and unused by this path" - and every sliced file in `~/printer_data/gcodes` calls
`PRINT_START_INIT` (checked, 4 of 4). So on **every real print**, the stale suppression flags were
never dropped and the stale toolchange marker was never reconciled.

That is the same class as the `ACE_PREFLIGHT` gap of 2026-08-26 (defined, but called from nowhere),
and the same class as `_ACE_BILL_FOR_JOB`, which the file's own comment records having to add to
both entry points. Three times now, in the same file. **A print-boundary action that exists in only
one of the two entry points is dead code, and nothing in the config makes that visible.**

## Fixes

### Cap and geometry

| Fix | What it catches |
|---|---|
| `_ACE_SWAP_VARS.extract_mm` and `_ACE_UNLOAD_VARS.extract_cap` 140 -> 48 | the driver default was re-derived from geometry to `AceInstance.TANDEM_CAP_MM = 48.0`, but both live callers still passed 140. 140 exceeds the driver's `cap_mm > 2 * TANDEM_CAP_MM` sanity bound, so every swap printed the "far above the geometry" warning **instead of** using the safe value. Worse, `_retract_async_verified` commands the ACE to unwind the *whole cap* under its own power before the extruder moves - 140 unwound ~92 mm of slack the pull never needed |
| `_ACE_PARK_VERIFY` no longer advises raising the cap | the old text told the operator to raise `extract_mm` if the pull reached the cap. At a geometry-derived 48 against a ~28 mm healthy pull, reaching the cap is a **fault report**, not an undersized cap - and raising it is scripting around a stall guard, which is a standing prohibition |

### Ordering and lifetime

| Fix | What it catches |
|---|---|
| `RELEASE_ASSIST=1` moved out of the top of `PRINT_END` / `CANCEL_PRINT` | it sat **before** the `FILAMENT_PARK` that re-enables assist and hard-requires it via `_ACE_REQUIRE_ASSIST`. So it never fixed the leak it was added for - the macro still ended with assist running - and it put a `STOP_FEED_OR_ROLLBACK` refuse-motion window directly in front of a park that cannot proceed without the device, so the park could raise and take the macro down **before its own `TURN_OFF_HEATERS`** |
| `_ACE_RELEASE_ASSIST_AFTER_PARK` | "after the park" cannot be expressed as a later line in `PRINT_END`: `_PARK_FILAMENT_POSTGEAR` raises `parking`, arms `_PARK_POSTGEAR_RETRACT_LOOP` and **returns**. The real end of the park is `parking` going back to 0, in `_SEEK_POSTGEAR_FOUND`/`_FAILED`. A delayed_gcode polls it - each tick a fresh render, so the read is a genuinely new observation. On timeout it deliberately does **not** release: pulling the ACE out from under a moving strand is the documented buckle mechanism |
| `_ACE_SWAP_WATCHDOG` ownership | it cleared both suppression flags unconditionally, with no record of what it was armed for. Reachable failure: a toolchange arms at T=0 and strands `swapping` at T=30; the operator starts a recovery park at T=280; the watchdog fires at T=300 on the *original* arm and clears `parking` **mid 35 mm retract**, re-arming the clog guard during a deliberate backwards move - the exact false CLOG of 2026-08-09, caused by the mechanism meant to prevent it. It now snapshots which flags were raised at arm time and clears only those, re-arming once with a refreshed snapshot for anything foreign |
| Watchdog armed for the boundary park | `_ACE_SUPPRESSION_ARM`'s only call site was the toolchange path (`ace.cfg:555`). A standalone park - what **every** `PRINT_END` and `CANCEL_PRINT` ends on - raised `parking` with no watchdog at all, and those macros disarm it a few lines earlier. If the park raised, `parking` stuck at 1 and the runout and clog guards stayed suppressed until the next print boundary |

### State

| Fix | What it catches |
|---|---|
| `ACE_CLEAR_OP` | `ace_op` had one writer, one reader, and **no clearer anywhere in the tree**. `ACE_AUDIT` folds it into its verdict and `_ACE_DRYROLL_TICK` holds whenever the audit is not ok, so one interrupted swap stopped the drying roller permanently. `MMU_UNLOCK` - which the macro's own operator text named - clears `ace_target_index` but has never touched `ace_op` |
| `ACE_RECONCILE_TARGET STRICT=1` at the start boundaries | the unresolvable branch only did `RESPOND TYPE=error`, which does not abort. `PRINT_START` proceeded into a print with `ace_target_index` pinned - which returns `ace_preload_guard._check_invariant` early, skips `_validate_startup_tool_state` and `reconcile_stale_current_index`, and refuses assist re-arm for any tool but the target. **Deliberately not strict at END/CANCEL**: a raise there would abort before their own heater-off, recreating the bug above |

### Detection

| Fix | What it catches |
|---|---|
| `_ACE_PARK_VERIFY` gated on **both** toolhead switches | it declared "toolhead free" from `toolhead_entry` alone, then committed a ~950 mm hub pull and wrote `parked=0 / hot=0 / state=bowden`. `entry=0` with `post=1` is impossible on a continuous strand - post-gear sits 20 mm *below* entry - so it means a severed strand or a dead entry switch. Under either reading that pull is wrong and the state write is a lie. Same single-switch mistake as the driver confirming `filament_pos='nozzle'` from entry alone on 2026-08-28 |
| `_ACE_COLD_RETRACT_RESULT` | `ACE_COLD_RETRACT`'s result line read the sensors in the **same render** as the moves it was reporting, so a calibration command whose entire output is that line printed its *pre-retract* state. `M400` does not help - the Jinja was already substituted. Same trap and same fix as `_ACE_PARK_VERIFY` |

## Corrections to earlier claims

- **`ACE_COLD_RETRACT` cannot raise.** It was believed to have gained the ability to abort. It has
  not: `cmd_ACE_TANDEM_EXTRACT` and `cmd_ACE_RETRACT` (ACEPRO `extras/ace/commands.py`) both wrap
  their whole body in `except Exception as e: gcmd.respond_info(...)`. A refused retract, a slot
  fault, and `_tandem_extract`'s own "entry never cleared" `ValueError` all arrive as an **info**
  line, and the macro carries on regardless. The inline comment claiming "a refusal is raised" was
  wrong and is corrected in place.
- **`LENGTH` means a cap only on one branch.** With `toolhead_entry` triggered it is passed as
  `CAP` to a sensor-terminated pull. With entry clear it is still a plain fixed distance to an
  ACE-alone `ACE_RETRACT`. The semantics changed *conditionally*, which is harder to notice than a
  clean break.
- **Nothing was scripted against `ACE_COLD_RETRACT`.** It has zero callers in the entire config
  tree - it is a hand-typed diagnostic. The 2026-08-20 semantics change broke no automation. That
  is luck, not design, and the macro now says so at the top.
- **`_ACE_POST_TOOLCHANGE` *does* disarm the watchdog** (`ace_purge.cfg:137`). An early hypothesis
  that a completed toolchange left a stale timer armed is wrong; the ownership hazard is the
  narrower abandoned-toolchange case documented above.

## Still open - not this session's files

- **`macros/filament/park.cfg:58`** raises `parking` with no `_ACE_SUPPRESSION_ARM` beside it. The
  boundary parks are now covered from `PRINT_END`/`CANCEL_PRINT`, but a park started anywhere else
  (manual `FILAMENT_PARK`, runout recovery, the load path) still has no watchdog. The general fix
  is an arm alongside the raise, in that file.
- **`ace_mmu_commands.cfg:140` `MMU_UNLOCK`** should call `ACE_CLEAR_OP`. It is the command the
  operator is told to run to resolve a stuck toolchange, and it currently leaves the open intent
  set, so `ACE_AUDIT` stays not-ok and the drying roller stays held after a "successful" unlock.
- **`ace_op` is a flat string, not the structured record the design calls for.** The state-journal
  design specifies op / lane / started. The live slot holds one human-readable sentence, so nothing
  can reason about *which* lane the open intent concerns.

# Audit/dryroll/load/material fixes — 2026-08-31

Scope: `ace_audit.cfg`, `ace_dryroll.cfg`, `macros/filament/load.cfg`,
`macros/helpers/material_temp.cfg`. Committed as `8e2cf24` in `~/printer_data/config`. All four
were mechanical defects handed down with the fix already diagnosed; the work here was verifying
each diagnosis against source before touching anything, then proving the fix engages.

## The headline: a "fixed" dead-code check and a comment that predicted its own bug class

The unowned-hub check in `ACE_AUDIT` had already been patched once, from `staged|length == 0` to
`not path_busy`, with a comment explaining exactly why the first version was wrong. The second
version was *also* dead code, for a reason that comment could not see: `ace.cfg`'s
`[ace_preload_guard] occupancy_sensors` was extended on 2026-08-21 to include `hub_detect` itself,
so `path_busy` is now true for the same reason `hub` is true. `hub and not busy` cannot both hold -
confirmed by rendering the real expression through `jinja2.Environment('{%','%}','{','}')` (the
exact delimiters `gcode_macro.py` uses), not by reading the Python and assuming.

Separately, `macros/filament/load.cfg` still had `ptfe_postgear_to_nozzle|default(40.0)` in its
purge-distance calculation - the pre-2026-08-24 value. [[ace2-geometric-vs-functional-distance]]
recorded that correction (40 -> 80) three files away from this one; this session is the first time
anyone checked whether the *fallback* used elsewhere had been updated to match. It had not. The
live saved variable is 80, so this had zero effect today, but a missing/corrupted save_variables
would have silently under-purged by exactly 40mm - the same shape of bug the geometric/functional
mix-up already was.

## Fixes

| Fix | What it catches |
|---|---|
| `ACE_AUDIT` unowned-hub check now tests `entry`/`post`/`staged` directly, not `path_busy` | `path_busy` folds in `hub_detect` itself (see above), so testing it inside a `hub and ...` condition is checking hub against hub. Direct sensor/staged tests can't fold back on themselves the same way |
| `_ACE_DRYROLL_PREPARE_PARK` excludes the currently-loaded lane (`cur`) from needing re-park | a lane loaded and parked at post-gear reads `pos != 0` / `dat == 0` on every restart - it was never sweep-parked in the first place, it's parked at the *toolhead*. That is not off-datum, it's exactly where a reload needs it |
| `_ACE_DRYROLL_PREPARE_PARK` retries instead of `action_raise_error` when the hub is occupied | the hub is occupied by the previous job's own strand on **every** normal restart (parked-at-postgear spans the whole path, hub included) until the first toolchange retracts it. Raising here aborted `PRINT_START` before homing or heat on the ordinary case, not an edge case |
| `sfs_live` tests `switch_sensor` pin identity only, not `.enabled` | six macros (`KOMB`, pause/resume, cancel, print end, change, unload) legitimately `SET_FILAMENT_SENSOR SENSOR=switch_sensor ENABLE=0` for the length of an operation. `.enabled` was never a proxy for "is this pin real" - it downgraded a genuinely live sensor to "no opinion" for the whole window |
| `printer["configfile"].config` chain now uses `.get(...)` at every level | a missing intermediate key returns `Undefined` from Jinja's own `getitem` (a caught `KeyError`), but `Undefined.__getitem__` is one of the dunders Jinja's base `Undefined` disables - it raises `UndefinedError`, which `environment.getitem`'s `except (AttributeError, TypeError, LookupError)` does not catch. `\|default("")` on the end never gets a chance to run. `printer.cfg`'s own comment on that section invites deleting it later |
| `ptfe_postgear_to_nozzle` fallback `40.0` -> `80.0` in the purge-distance calc | see headline. Matches the corrected saved variable so a missing/corrupted save_variables degrades to the right distance instead of the pre-correction one |
| Material family match is genuinely longest-match, not list-order-first | `_fams \| select('in', ...) \| first` returns whichever family sits earliest in the list, not the longest string. It happened to agree with longest-match for every real name tried (see Verification) because in `['petg','pla','abs','asa','tpu','pc']` no shorter family precedes a longer one that also matches - so the bug was latent, not yet symptomatic, and would break silently the day the list gained an entry like `'pet'` before `'petg'` |

## Corrections to earlier claims

- **The `PC-ABS` example in the defect report does not change output.** Framed as "gets purged at
  ABS temperature" as if that were the bug: it isn't, for this specific case. `'abs'` (3 chars) is
  the genuinely longer match against `'pc'` (2 chars), so longest-match picks the same family
  first-match did, by coincidence of the current list contents (see Fixes table). The defect was
  real - the *mechanism* was list-order, not length - but it does not manifest as a wrong number
  today. Said so rather than reporting a temperature change that didn't happen.

## Verification

Machine stayed `standby`/idle throughout; no filament moved; `RESTART` issued three times,
`/printer/info` returned `ready` each time; `ACE_AUDIT` reported `no contradictions` before the
first edit and again after the last restart.

- **C1** (`ACE_AUDIT` hub check): rendered both the pre-fix and fixed expressions through a real
  `jinja2.Environment` with Klipper's macro delimiters across four scenarios. Pre-fix: `0` in all
  four, including the orphan-strand case it exists to catch. Fixed: `1` only for that case, `0`
  when a staged lane explains the hub reading and `0` when entry/post already cover it (avoiding
  double-reporting against the ANOMALY/`cur<0` rules above it).
- **C2** (`_ACE_DRYROLL_PREPARE_PARK`): same technique, using the real `ace_dryroll_datum` values
  read live off the machine (`[1, 1, 1, 0]`) as the T1-T3 baseline. Pre-fix `ns.need` included the
  currently-loaded lane; fixed `ns.need` excluded it. Both pre-fix and fixed take the hub-occupied
  branch given the remaining lanes - pre-fix raises, fixed retries.
- **C3** (`sfs_live`): added a temporary diagnostic macro (`_TEST_SFS_LIVE_C3`, removed before the
  commit), restarted, and read it back live. With the sensor's real (dead, `^!PC15`) pin:
  `enabled=True -> sfs_live=False`, then `SET_FILAMENT_SENSOR SENSOR=switch_sensor ENABLE=0` ->
  `enabled=False -> sfs_live=False` (unchanged - no longer gated on `.enabled`), and a probe against
  a deliberately nonexistent config section returned the safe default instead of crashing
  (`missing_section_probe=SAFE_DEFAULT_NO_CRASH`). Re-enabled the sensor afterward - no net change
  to machine state. The counterfactual (a *live* pin disabled mid-operation) was additionally run
  through the same `jinja2.Environment` technique, since the real pin cannot be swapped without
  editing `printer.cfg`: old formula gives `0` (wrongly downgraded), fixed gives `1`.
- **C4** (`_MATERIAL_PURGE_TEMP`): called live, read back `printer['gcode_macro
  _MATERIAL_PURGE_TEMP'].temp`. `OLD=PC-ABS NEW=PLA` -> `280.0`; `OLD=PLA+ NEW=PLA+ FLOOR=220` ->
  `220.0` (no flush); `OLD=ABS NEW=PLA` -> `280.0`. All three reproduced identically after the
  final restart.

## Open, needs hardware

- **C1, the orphan-strand branch, on a real orphan.** Verified by Jinja simulation against the real
  expression; never observed against an actual unowned strand sitting in the hub, because staging
  one means moving filament into that state.
- **C2, the retry actually clearing.** Verified that the branch no longer raises; not verified that
  a real overnight dry-roll followed by a real `PRINT_START` reaches the retry, waits out the
  previous tool's toolchange, and successfully re-parks the reserved lanes before they're used.
  That is the actual regression test for this defect and it requires a real print.
- **C3, a genuinely live `switch_sensor` pin.** The counterfactual is simulated, not observed - the
  pin has been dead since 2026-08-21 and stays that way until the RDM cutover is undone.
