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
