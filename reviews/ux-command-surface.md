# Operator command surface — Voron Trident 300 + ACE 2 Pro

Read directly against `L:\config` (every file cited below was opened, not guessed from its name).
The seed list at `L:\overnight\_macro_list.txt` names 79 macros; verifying it against the config
turned up **14 more** it missed entirely (keyword filter, not human read) — `MMU_EJECT`,
`PAUSE`/`RESUME`/`CANCEL_PRINT`/`M600` (the stock-command overrides), `PRINTER_STATE`,
`SET_PRINT_STATS_INFO`, `MAINTENANCE_NOZZLE`, `CLEAN_NOZZLE`, `PRINT_ABORT_START`,
`TEST_UNPARK_DISTANCE`, `STEP_UNPARK`, `CALIBRATION_PTFE_LENGTH`, `PTFE_SFS_LENGTH_CALIBRATE`.
**Real count: 93 operator-facing macros** that touch filament, the ACE, the cutter, the brush, the
belt, or the print lifecycle around them.

That number is itself the finding, and the undercount is structural, not a one-off — a second
independent read of the same files kept finding more. Nobody holds 93 names and their pairwise
differences in their head mid-print with a strand in hand. Everything below is in service of
getting that number down to a small set of things a person can actually choose between.

---

## 1. Inventory, by domain

Every operator-facing macro (non-underscore `[gcode_macro]`), grouped by what an operator would be
trying to do. **Note** flags only what's actionable: no `description:`, exact duplicate, should be
internal, or a live hazard. Blank means it's fine as-is.

### Cutter (CROSSBOW) — `macros/filament/crossbow.cfg`
| Macro | Does | Note |
|---|---|---|
| `CROSSBOW_CUT_TIP` | Full sequence: retract to cut height, cut, pushback, park, commit state. **The one to press.** | |
| `CROSSBOW_CUT` | **Motion only** — actuates the blade wherever the filament happens to be. No retract, no pushback. Own description says "position the filament first." | The incident macro — see §2 row 1. Rename + internalize, §3. |
| `CROSSBOW_RESET` | Push the lever home from behind the arm. Idempotent, self-guarded. | |
| `CROSSBOW_ESCAPE` | Get the toolhead out of the X<0 exclusion after an aborted cut. No-ops harmlessly if not needed. | Should be visible always, urgent-styled only when actually needed (§5). |
| `CROSSBOW_DRYRUN` | Approach/retreat with no cut, to prove the path is clear. Calibration only. | Move to Advanced group. |

### ACE lane control (native) — `ace_toolchange.cfg`, `ace_unload.cfg`, `ace_ui.cfg`, `ace_lane_state.cfg`, `ace_preflight.cfg`, `ace_toolchange`
| Macro | Does | Note |
|---|---|---|
| `ACE_LANE_EJECT` | **Spool change.** Pulls a lane fully out; slot reads empty; needs hand reinsertion. | |
| `ACE_LANE_PARK` | **Colour swap.** Frees toolhead, retracts lane clear of the hub; stays gripped, reloads with no hands. | Duplicate implementation of `FILAMENT_PARK`'s ACE branch — see appendix item 3. |
| `ACE_LANE_UNLOAD` | Deprecated stub. Its entire body is an error telling you to use EJECT or PARK. | Already fixed the right way — candidate for deletion, not a live problem. |
| `ACE_LANE_UNLOAD_ABORT` | Stops a running EJECT. | |
| `ACE_LANE_STATE` | Console ASCII bar of every lane's position (gate/bowden/hub/entry/gears/nozzle). Read-only. | One of 5 overlapping status commands, §2 row 4. |
| `ACE_LANE_RANGE` | Calibration probe of a lane's true ACE-side travel. | Move to Advanced. |
| `ACE_LANES` / `ACE_LANES_TEXT` | Interactive lane dialog (buttons) / console dump of the same. | `ACE_LANES` has a live KlipperScreen prompt-collapse bug — §4. One of 5 status commands. |
| `ACE_DECLARE_TIP` | Record tip state after a hand operation. `STATE=cut\|nozzle\|shaped` typed by hand. | Should be 3 buttons, not a typed param — §3. |
| `ACE_CLEAR_OP` | Close the durable "open intent" marker after resolving a stuck path by hand. | One of 4 overlapping recovery commands, §2 row 5. |
| `ACE_RECONCILE_TARGET` | Resolve a stale in-flight-toolchange marker from sensors; `STRICT=1` aborts if unresolvable. | Same group. |
| `ACE_PLAN_SWAP` | Arm a mid-print swap for the next layer boundary. `TOOL=` `NOW=1` `CANCEL=1`. | Armed state is RAM-only — appendix item 4. |
| `ACE_PREFLIGHT` | Verify every tool the sliced file needs is available before printing. | |
| `ACE_REMAP_AND_PRINT` | Redirect a missing tool to another lane and relaunch the file. | Only reachable from the preflight prompt — fine as internal-feeling but is a live button. |
| `ACE_SLICER_MAP` | Show what the sliced file asked for. Read-only. | |
| `ACE_COLD_RETRACT` | Calibration retract. Own description: "Does not raise on failure." | Swallows errors — appendix item 5. Move to Advanced. |
| `ACE_INFO` | Read-only status dump (box temp/humidity, dryer, per-slot). | **No `description:` field** — blank in Mainsail's list. |
| `ACE_AUDIT` | The real coherence checker — ~14 checks, `STRICT=1` aborts caller. | This is the one status command that should survive consolidation, §4. |
| `ACE_BILL_SPOOL_NOW` | Force the billing backend onto the currently loaded lane. Manual-fix escape hatch. | |
| `ACE_MEASURE_BOWDEN` / `ACE_MEASURE_ABORT` | Calibration: feed until entry trips, save the distance / abort it. | Move to Advanced. |
| `ACE_BENCH_POSTGEAR` / `ACE_BENCH_SENSORS` / `ACE_BENCH_ENCODER_START` / `ACE_BENCH_ENCODER_END` | Bench-only diagnostics (hysteresis mapping, live sensor dump, encoder baselining). | Move to Advanced. |

### MMU compatibility layer — `ace_mmu_commands.cfg`
Every one of these is a thin wrapper that calls a native ACE command above. Kept for Happy-Hare-panel
compatibility (the port-82 Mainsail fork expects these exact names).
| Macro | Wraps | Note |
|---|---|---|
| `MMU_LOAD` | `ACE_CHANGE_TOOL` (or `FILAMENT_LOAD_START` for a hand-fed lane) | Exact duplicate of a `T<n>` press. |
| `MMU_UNLOAD` | `ACE_LANE_PARK` | Exact duplicate. |
| `MMU_EJECT` | `ACE_LANE_EJECT` | Exact duplicate. Missing from the seed list entirely. |
| `MMU_PRELOAD` | Nothing — informational only, preload is autonomous hardware behaviour. | |
| `MMU_CHECK_GATE` | `ACE_LANES_TEXT` | |
| `MMU_RECOVER` | Re-derives state from sensors, always sets `hot=0` ("safer to reheat unnecessarily"). | One of 4 recovery commands, §2 row 5. |
| `MMU_UNLOCK` | Clears the in-flight-toolchange marker and re-arms the path guard. | **Does not clear `ace_op`** — a stuck swap can still fail `ACE_AUDIT` after this runs. |
| `MMU_SOFTWARE_VARS` | Nothing — accepted and ignored, no Happy-Hare tuning exists here. | |

### Generic (hand-fed / bypass) filament flow — `macros/filament/{load,unload,resume,park}.cfg`
This is a **separate, parallel implementation** for filament fed by hand rather than through an ACE
lane (Prusa-XL-style cold-grab → warm-load → colour-check → complete). Several of its commands have
**no ACE-lane guard at all** — see appendix items 2 and 3.
| Macro | Does | Note |
|---|---|---|
| `FILAMENT_LOAD` | Cold load, then offers warm load/purge via prompt. | No ACE-lane check before its cold-bite `FORCE_MOVE` — appendix item 2. |
| `LOAD_FILAMENT` | "Alias for FILAMENT_LOAD." Literally one line. | Pure duplicate — delete, §3. |
| `FILAMENT_LOAD_COLD` / `FILAMENT_LOAD_WARM` / `FILAMENT_LOAD_COMPLETE` | Each is a 1-line pass-through to an underscore-prefixed macro of nearly the same name. | Own `description:` says "Public command for prompt buttons" — they're not meant to be pressed cold. Delete, point the prompt buttons at the `_` originals directly, §3. |
| `FILAMENT_UNLOAD` | `MODE=FULL\|PARK`. Redirects ACE lanes to `ACE_LANE_PARK` first, unconditionally. | Good pattern — the model for the load side. |
| `UNLOAD_FILAMENT` | "Alias for FILAMENT_UNLOAD." | Pure duplicate — delete. |
| `FILAMENT_UNLOAD_FULL` | 110mm extruder-only retract. **Hard-refuses** with `action_raise_error` if an ACE lane is loaded, names the two correct commands. | Best example in the codebase of the rule this whole document is arguing for. |
| `FILAMENT_PARK` | Park at post-gear — the hand-fed-path parking primitive. Has its own inline ACE-lane guard (mode-3 rollback assist). | **Independent implementation of what `ACE_LANE_PARK` also does** — called by `PRINT_END` and `CANCEL_PRINT`. Appendix item 3. |
| `FILAMENT_RESUME` | "Resume loading (Heat & Purge) if already grabbed." Calls `_FILAMENT_LOAD_WARM`. | **Name collision with `RESUME`** (resumes a print). Its target macro has no ACE guard — appendix item 2. §2 row 3. |
| `FILAMENT_PURGE_MORE` | Extrude 20mm more, loop back to the colour-check prompt. | Flagged by the repo's own `assist_lint.py`. |
| `FILAMENT_CHANGE` / `M600` | Prusa-style filament change. **Hard-refuses on ACE lanes**, names `T<n>`/`ACE_PLAN_SWAP`/`ACE_LANE_EJECT`. | Second-best example of the refuse-with-guidance pattern. |
| `CANCEL_FILAMENT_PROMPT` | Close the active filament prompt (Cancel/Finish/No button target). | |
| `CALIBRATION_PTFE_LENGTH` | Extrude 1mm/step cold until post-gear trips, save the bowden length. `macros/filament/helpers.cfg`. | **No distance cap** (its sibling `_SEEK_POSTGEAR` caps at 200mm) — if the switch never trips, this runs forever, 1mm every 0.2s, no operator feedback. Appendix item 9. |
| `PTFE_SFS_LENGTH_CALIBRATE` | "Calibrate PTFE length from SFS encoder..." `macros/filament/helpers.cfg`. | **Textbook dead control** — its entire body is an unconditional error, it can never succeed. Delete or implement, §3. |

### Purge — `goose_purge.cfg`, `ace_purge.cfg`, `KAMP_Settings.cfg`
| Macro | Does | Note |
|---|---|---|
| `GOOSE_PURGE` | Purge onto the moving belt purger; full lead-in/dwell/wipe sequence. Live in the print path since 2026-09-01. | |
| `GOOSE_BELT_MOVE` / `GOOSE_BELT_OFF` | Jog the belt / de-energise it. | Move to Advanced. |
| `ACE_PURGE_NOW` | Run the toolchange purge by hand, `LENGTH=`. For tuning and for "colour's still not clean." | Needs a length field on a button, not console typing — §3. |
| `ACE_PURGE_MODE` | Declare whether the slicer owns the purge (prime tower). `TOWER=0\|1`. | |
| `ACE_WASTE_REPORT` / `ACE_WASTE_RESET` | Report / zero the per-print purge waste ledger. | |
| `LINE_PURGE` | KAMP's stock bed-based purge line. **No ACE-lane guard, no assist check** — raw `G1 E` moves. | Live grind risk if ever reached with an ACE lane loaded and unassisted — verify it is actually unreachable from `PRINT_START` before trusting it; not confirmed dead. |

### Wipe / brush — `macros/maintenance/KOMB.cfg`, `macros/filament/load.cfg`
Two real actions (wipe now; toggle wiping on/off) behind **seven names**:
| Macro | Does | Note |
|---|---|---|
| `KOMB` | Run the full zigzag scrub now. | **No `description:` field.** |
| `CLEAN_NOZZLE` | "Immediately wipe the nozzle on the brush (alias for KOMB)." | Duplicate — delete. Missing from the seed list. |
| `ENABLE_KOMB` / `DISABLE_KOMB` | Turn wiping on/off. | |
| `FILAMENT_WIPE_ENABLE` / `FILAMENT_WIPE_DISABLE` / `FILAMENT_WIPE_TOGGLE` | Same on/off switch, different name, same variable (`_KOMB_Variables.enable_komb`). | Three more duplicates of the pair above — delete two, keep one name, §3. |
| *(all six of the above)* | — | **All six write with `SET_GCODE_VARIABLE`, not `SAVE_VARIABLE`** — "disable wiping, the brush is broken" does not survive a Klipper restart. A picture-that-lies case: the control reports success and silently reverts. Appendix item 8. |

### Dryer — `ace.cfg`
| Macro | Does | Note |
|---|---|---|
| `ACE_DRY` | Start the dryer. `MATERIAL=` or explicit `TEMP=`/`MINUTES=`. | |
| `ACE_AUTODRY` | Hold humidity below a target, restart-survival persisted. | |
| `ACE_AUTODRY_STATUS` | Read-only. | |
| `ACE_DRY_OFF` | Stop dryer and cancel auto-dry. | |
| `ACE_DRY_PRESETS` | List the per-material presets. Read-only. | |

### Dry-roll (rotisserie) — `ace_dryroll.cfg`
| Macro | Does | Note |
|---|---|---|
| `ACE_DRYROLL_MODE` | Set a lane's roll mode. `T=` `MODE=off\|sweep\|spin`. | |
| `ACE_DRYROLL_START` / `ACE_DRYROLL_STOP` | Begin/stop periodic rotation. | `START` has no restart-survival re-arm, unlike `ACE_AUTODRY`. |
| `ACE_DRYROLL_PREPARE` / `ACE_DRYROLL_RELEASE` | Print-start/end hooks — reserve lanes a job needs / release them. | Print-lifecycle hooks, not really operator clicks. |
| `ACE_DRYROLL_STATUS` | Read-only. | |

### Spool & billing — `spool_guard.cfg`
| Macro | Does | Note |
|---|---|---|
| `SPOOL_GUARD` | Detached shell check: does the loaded spool have enough for this job? `notify` mode only — never blocks. | |
| `SPOOL_GUARD_CONTINUE` / `SPOOL_GUARD_STOP` | Answer the spool-guard dialog. | **Dead controls.** The file's own header states `--mode ask` "DOES NOT CURRENTLY WORK ON THIS MACHINE" (`PAUSE` is ignored during `PRINT_START`'s startup window) — the dialog these buttons answer cannot currently fire. §2 row 10. |
| `SPOOL_GUARD_CLOSE` | Dismiss the dialog. Also reused by an unrelated prompt (`ace_preflight.cfg`'s remap offer). | |
| `SPOOL_FITS` | Advisory-only "would GRAMS fit" query. Never stops a print. | |
| `SPOOL_REASSIGN` | Move this print's usage to a different Spoolman spool. Detached deliberately — inline execution once froze the toolhead on a live layer. | |
| `SPOOL_STATE` | Read-only: what was recorded at this print's start. | |

### Print lifecycle (stock-command overrides and status) — `macros/print/*.cfg`, `macros/status/printer_state.cfg`, `ace.cfg`
| Macro | Does | Note |
|---|---|---|
| `PAUSE` / `RESUME` | Real overrides (`rename_existing: _MAINSAIL_PAUSE`/`_MAINSAIL_RESUME`). Sensor-disable on pause, un-park purge on resume with an `is_paused` guard. | Bound to Mainsail's dedicated buttons regardless of the macro list. |
| `CANCEL_PRINT` | Real override (`rename_existing: BASE_CANCEL_PRINT`). Full shape/park/wipe via `FILAMENT_PARK`, heater safety net, MMU_TTG_MAP reset. | |
| `PRINT_ABORT_START` | Violent variant used when `PRINT_START` itself fails — kills heaters immediately, **no shape/park/wipe at all**. | Not just a rare manual button — it's the automatic exit from *every* `_START_ABORT_IF_REQUESTED` checkpoint and every `ABORT_ON_FAIL=1` seek failure, and it calls the renamed stock cancel directly, bypassing `CANCEL_PRINT`'s own cleanup (`ACE_DRYROLL_RELEASE`, `ACE_RECONCILE_TARGET`, the MMU_TTG reset, the heater-safety fuse). No ACE assist release either — if it fires while feed/rollback assist is armed, nothing disarms it. Appendix item 7. |
| `M600` | One line, forwards to `FILAMENT_CHANGE`. | |
| `PRINTER_STATE` | Console + Mainsail-info-panel status: material, plate, filament status, driver-vs-flag mismatch warnings. | One of 5 overlapping status commands, §2 row 4. |
| `SET_PRINT_STATS_INFO` | Real override, fires an armed `ACE_PLAN_SWAP` at layer boundaries. Not a human click target. | |
| `MAINTENANCE_NOZZLE` | Park + heat to 250C + full unload, for a hardware nozzle swap. Correctly redirects ACE lanes through `FILAMENT_UNLOAD`'s own guard. | Name is easy to reach for when the real intent is "the nozzle is blocked" — see §3, no such control exists today. |
| `TEST_UNPARK_DISTANCE` / `STEP_UNPARK` | Calibration: heat, park, advance a set distance, then hand-step 1mm at a time to find the nozzle tip. | Move to Advanced. |

---

## 2. Confusability, ranked by (frequency × cost)

| # | Pair / group | Pick wrong, and… | Cost | Freq. |
|---|---|---|---|---|
| 1 | `CROSSBOW_CUT` vs `CROSSBOW_CUT_TIP` | The raw primitive cuts wherever the strand is, doesn't retract, doesn't shape. Followed by any normal unload/swap, the tip gets cut **again** — the exact incident: real filament lost, a second stub, risk of a tapered face that jams the next load. | High | Medium — reached for any time the operator wants to "just cut it" |
| 2 | `ACE_LANE_EJECT` vs `ACE_LANE_PARK` (+ `ACE_LANE_UNLOAD`, `MMU_EJECT`/`MMU_UNLOAD`) | EJECT when you meant PARK: spool physically comes out, needs hand-reinsertion, wastes a re-thread. PARK when you meant EJECT: can't pull the spool, need a second command. | Medium | **High** — every spool swap and colour change touches this |
| 3 | `RESUME` vs `FILAMENT_RESUME` | Same verb, unrelated scope: one resumes the print, the other resumes a hand-load sequence. `FILAMENT_RESUME`'s target (`_FILAMENT_LOAD_WARM` → `_FILAMENT_HEAT_LOAD`) has **no ACE-lane guard at all** and will extrude against a clamped lane if postgear/entry happen to read true. | High (real grind path, not just wrong-button) | Low-medium |
| 4 | 5 status commands: `ACE_AUDIT`, `ACE_LANES`, `ACE_LANES_TEXT`, `ACE_LANE_STATE`, `PRINTER_STATE` | All read-only, so nothing breaks — but every "what's going on" moment costs a guess among five. | None | **Very high** — every session |
| 5 | 4 recovery commands: `MMU_RECOVER`, `MMU_UNLOCK`, `ACE_CLEAR_OP`, `ACE_RECONCILE_TARGET` | Each fixes a *different* piece of stuck state. `MMU_UNLOCK` does not clear `ace_op`; running it alone can leave `ACE_AUDIT` still failing while the operator believes it's fixed. | Medium — false confidence at the worst moment | Low, but exactly when stressed |
| 6 | 7 wipe names: `KOMB`, `CLEAN_NOZZLE`, `ENABLE_KOMB`, `DISABLE_KOMB`, `FILAMENT_WIPE_ENABLE/DISABLE/TOGGLE` | Two real actions behind seven buttons that share no common root word — nothing hints they're related. | Low | Medium |
| 7 | `FILAMENT_PARK` vs `ACE_LANE_PARK` | Both safe (each independently ACE-guarded), but they are **two separate implementations** of the same job, reached from different callers (print-end/cancel vs mid-swap). Bug fixes have had to land twice. | Low to the operator, real to maintenance | Low |
| 8 | `MMU_*` layer vs native `ACE_*`/`ACE_LANE_*` | Same actions, different vocabulary depending on which of the two Mainsail instances (port 80 vs the Happy-Hare-styled port 82) is open. | None | Medium |
| 9 | `LOAD_FILAMENT`=`FILAMENT_LOAD`, `UNLOAD_FILAMENT`=`FILAMENT_UNLOAD` | Pure aliases, identical behaviour. | None | Low — pure clutter |
| 10 | `SPOOL_GUARD_CONTINUE`/`SPOOL_GUARD_STOP` | Look like a live safety dialog's answer buttons. The dialog that would show them cannot currently fire (`ask` mode confirmed non-functional by the file's own header). | Confusing if ever seen | Near zero |

---

## 3. Proposed command surface — intent to entry point

One entry point per intent. **Rename** = the macro's actual name changes (every caller updated).
**Internalize** = underscore-prefixed, drops out of every macro list, stays callable for chains.
**Delete** = remove outright, nothing else calls it.

| Operator's intent | Entry point | Change needed |
|---|---|---|
| "I want to print in \<colour/lane already loaded\>" | `T<n>` / `ACE_CHANGE_TOOL` | **Missing control** — no button anywhere offers "load this lane." See §4. |
| "Get this filament out, I'm swapping the spool" | `ACE_LANE_EJECT` | No change — already correctly named. |
| "I'm about to work on/near the toolhead, keep the spool" | `ACE_LANE_PARK` | Relabel only (button text, not macro name) to "Free toolhead (keep spool)." |
| "Cut and prep the tip" | `CROSSBOW_CUT_TIP` | No change. |
| *(raw blade actuation, calibration only)* | — | **Rename** `CROSSBOW_CUT` → `_CROSSBOW_ACTUATE` (internalize). Update the one call site inside `CROSSBOW_CUT_TIP`. This alone closes the surface-level version of the incident — the dangerous primitive stops being a button at all. |
| "I did something by hand, update the record" | `ACE_DECLARE_TIP` | Replace the typed `STATE=` param with 3 explicit buttons: "I cut it" / "It's at the nozzle" / "I shaped a tip" — same macro, `STATE=cut\|nozzle\|shaped` baked into each button's payload. |
| "Purge more, colour's not clean" | `ACE_PURGE_NOW` | Needs a length control (field or +10/+20mm steps), not console typing. |
| "The nozzle is blocked / nothing's coming out" | — | **Missing control**, confirmed — nothing in the config answers this intent. `MAINTENANCE_NOZZLE` is a hardware nozzle-*swap* macro, not a clog response, despite the name inviting confusion. Build a new composed entry point: audit temperature/sensors/assist state, then offer differentiated remedies (retry purge / cold pull / cut-and-reload) rather than one unguarded path. |
| "What's going on right now" | — | Consolidate the 5 status commands into the one always-open panel (§4). `ACE_AUDIT` is the one to keep as a distinct button (it's the only one that can *fail loudly*, `STRICT=1`); the other 4 become one live view. |
| "Something's stuck, un-stick it" | — | New consolidated "Recover" control: runs `ACE_AUDIT`, then offers exactly the sub-fix(es) that apply from `MMU_RECOVER`/`MMU_UNLOCK`/`ACE_CLEAR_OP`/`ACE_RECONCILE_TARGET` — not a menu of four names to guess between. |
| Turn wiping on/off | `ENABLE_KOMB`/`DISABLE_KOMB` (keep these two names — they at least share a root with `KOMB`) | **Delete** `FILAMENT_WIPE_ENABLE`, `FILAMENT_WIPE_DISABLE`, `FILAMENT_WIPE_TOGGLE`, `CLEAN_NOZZLE` (alias of `KOMB`, not the toggle — delete separately). |
| Load filament (any path) | `FILAMENT_LOAD` | **Delete** `LOAD_FILAMENT` (pure alias). |
| Unload filament (any path) | `FILAMENT_UNLOAD` | **Delete** `UNLOAD_FILAMENT` (pure alias). |
| Prompt-button-only load steps | *(internal already, in spirit)* | **Delete** `FILAMENT_LOAD_COLD`, `FILAMENT_LOAD_WARM`, `FILAMENT_LOAD_COMPLETE` — each is a 1-line pass-through whose own description admits it's "for prompt buttons." Point the prompt buttons directly at the existing `_FILAMENT_LOAD_COLD`/`_FILAMENT_LOAD_WARM`/`_FILAMENT_LOAD_COMPLETE`; underscore macros are callable from a prompt button exactly the same way. |
| Superseded unload wording | — | `ACE_LANE_UNLOAD` already does the right thing (refuses, names the two real commands). No change needed; candidate for outright deletion once nothing depends on the old name. |
| MMU-panel compatibility | `MMU_LOAD`/`MMU_UNLOAD`/`MMU_EJECT`/`MMU_PRELOAD`/`MMU_CHECK_GATE`/`MMU_RECOVER`/`MMU_UNLOCK`/`MMU_SOFTWARE_VARS` | Keep the names (needed for the Happy-Hare-styled panel and any external tooling). **Hide** from the primary Mainsail instance's visible groups — visibility change only, not a rename. |
| Calibration/bench (`ACE_BENCH_*`, `ACE_MEASURE_*`, `ACE_COLD_RETRACT`, `ACE_LANE_RANGE`, `CROSSBOW_DRYRUN`, `GOOSE_BELT_MOVE`/`OFF`, `TEST_UNPARK_*`) | unchanged names | Group into one "Advanced / Calibration" panel, hidden by default on both Mainsail and KlipperScreen (mechanism exists already, see §4). |

---

## 4. Mainsail panel design

Two tiers, matching the two delivery routes that actually exist on this box.

### Tier 1 — extend the bundle-patched `/ace/` panel (`panel/ace_index.html`)

**It is currently pure telemetry.** Read the file: `button{...}` CSS is defined and never used —
zero `<button>` elements exist in the DOM. It already holds a live websocket subscription and
already draws the lane/path diagram that answers "which lane is red" — it is the natural home for
the actions, not just the status.

Add, in place, no new page:

- **Lanes card** — one action button per row, computed from state already drawn:
  - Lane is the current one → **Eject** (danger style) and **Free toolhead** (warning style), both visible.
  - Lane not current, not empty → **Load** (primary). This is the missing "print in red" control.
  - Lane empty → **Load**, disabled, tooltip "insert filament first."
  - *(Needs subscribing to `printer["ace"]` for `current_index` — not currently in the panel's `OBJECTS` list.)*
- **Filament path card** — add **Purge more** (a stepped +10/+20mm control, not a typed field) and
  **Declare tip: Cut / Shaped / At nozzle** as three small buttons, positioned right under the
  diagram since that's where the operator is already looking to judge whether it's needed.
- **New Recover card** — shows `ACE_AUDIT`'s live `ok`/`why` (needs `ACE_AUDIT` on a periodic
  timer or the panel to trigger it itself); a single **Fix it** button, hidden entirely when clean.
- **Dryer card** — add Dry/Auto-dry/Off buttons and a material dropdown pulling from
  `ACE_DRY_PRESETS`; the card already shows the numbers that decide whether to run it.

### Tier 2 — Mainsail's native Expert-mode macro groups

For everything not worth bespoke panel code (confirmed via Mainsail's own docs: Expert mode groups
macros into named panels with per-macro colour, configured entirely in Settings → Macros, **zero
config file changes, survives Mainsail updates** unlike the bundle patch). Proposed groups:

| Group | Contents |
|---|---|
| **Cutter** | `CROSSBOW_CUT_TIP` (primary), `CROSSBOW_RESET`, `CROSSBOW_ESCAPE` |
| **Purge & wipe** | `ACE_PURGE_NOW`, `ACE_PURGE_MODE`, `ENABLE_KOMB`, `DISABLE_KOMB` |
| **Dry-roll** | `ACE_DRYROLL_MODE`, `ACE_DRYROLL_START`, `ACE_DRYROLL_STOP`, `ACE_DRYROLL_STATUS` |
| **Spool & billing** | `SPOOL_FITS`, `SPOOL_REASSIGN`, `SPOOL_STATE`, `ACE_BILL_SPOOL_NOW`, `ACE_WASTE_REPORT` |
| **Advanced / Calibration** (hidden by default) | every macro tagged "Move to Advanced" in §1 — `ACE_BENCH_*`, `ACE_MEASURE_*`, `ACE_COLD_RETRACT`, `ACE_LANE_RANGE`, `CROSSBOW_DRYRUN`, `GOOSE_BELT_MOVE`/`OFF`, `TEST_UNPARK_*`, `PRINT_ABORT_START`, `CALIBRATION_PTFE_LENGTH` (cap its distance first — appendix item 9) |
| *(not grouped anywhere — Simple-mode "hide")* | the whole `MMU_*` compatibility layer, `LOAD_FILAMENT`/`UNLOAD_FILAMENT` if not yet deleted |

**Verify once configured** whether the two Mainsail instances (port 80 / port 82) share Expert-mode
settings through Moonraker's database or need configuring twice — not established by the docs, and
the port-82 fork is old enough (v2.17, Happy-Hare-era) that its Expert mode may differ or be absent.

### KlipperScreen

`KlipperScreen.conf` already has a working, currently under-used version of the same idea:
```
#~# [displayed_macros Printer]
#~# filament_load_cold = False
#~# filament_load_cold_start = False
#~# filament_load_start = False
#~# filament_load_warm_start = False
#~# filament_runout_pause = False
#~# filament_runout_unload = False
```
Someone already started hiding the load-flow's internal-feeling steps from the touchscreen. **Extend
this list**, don't invent a new mechanism — add every macro tagged for internalizing/hiding in §1 and
§3. Zero risk, same file, matches a pattern already in use.

**Live prompt bug to fix while touching this file's neighbours**: `ace_ui.cfg`'s `ACE_LANES` dialog
emits ~13 `action:prompt_text` lines (toolhead status, per-lane path × 4, per-lane status × 4,
possible jam line). KlipperScreen renders only the *last* one — confirmed by the file's own header
comment, which already documents the constraint and still ships the multi-line dialog anyway. The
per-lane information that vanishes on the touchscreen should move into each lane's **button label**
instead (`"T1 red PLA — eject"` rather than a separate text line above it) — button text renders
regardless of the prompt-text collapse; that's how the per-lane EJECT row already survives today.

---

## 5. Disabled beats error — the controls that need live gating

| Control | Disabled when | Tooltip |
|---|---|---|
| Load T\<n\> (new) | lane empty, or lane is already current, or `path_busy` | "Lane is empty" / "Already loaded" / "Path busy — another lane is feeding" |
| Load T\<n\> (new), mid-print | never disabled | leave enabled but styled as a confirm — a deliberate manual colour override is legitimate; friction here is a feature, not a bug |
| Eject | toolhead holds a *different* lane's filament, or nothing to eject | "Toolhead holds T{cur}, not this lane" / "Already empty" |
| Free toolhead (Park) | nothing loaded (`current_index < 0`) | "Nothing loaded" |
| Cut & retract tip | axes not homed, or post-gear reads no filament, or already `parked=1/hot=0` | "Not homed" / "Nothing in the toolhead" / "Already parked and shaped — cutting again wastes another stub" |
| Escape | *(never disabled — always safe, no-ops if not needed)* | Style **urgent** only when live X < `safe_x`; otherwise quiet |
| Purge more | extruder below `min_extrude_temp`, or axes not homed | "Nozzle too cold" / "Not homed" |
| Declare tip: Cut/Shaped/Nozzle | *(never disabled)* | Always available — it's a statement of fact, not a physical operation |
| Recover / Fix it | `ACE_AUDIT.ok == 1` | hidden entirely, not greyed, when nothing is wrong |
| Spool guard Continue/Stop | *(structural — see appendix item 6)* | Until the underlying `ask` mode is fixed, remove these two buttons rather than leave them live-looking and dead |
| Dry / Auto-dry | dryer already running that mode | "Already drying" |

---

## Correctness / safety findings (not mine to fix — flagging only)

1. **The cut-twice hole survives the 2026-09-01 fix for the two commonest paths.** `ACE_LANE_PARK`,
   `ACE_LANE_EJECT` and `CROSSBOW_CUT_TIP` all gate their shape/cut decision on `filament_loaded_hot`
   /`filament_parked` and **never read `tip_state`**. Only the internal `_ACE_PREPARE_FOR_RETRACTION`
   hook (`ace.cfg:411-609`, reached only by print-end/runout/bare-unload) consults it. A bare
   `CROSSBOW_CUT` + `ACE_DECLARE_TIP STATE=cut`, followed by an ordinary `T<n>` swap or
   `ACE_LANE_EJECT`, will still re-cut.

2. **`FILAMENT_LOAD`'s cold-bite, `FILAMENT_RESUME`'s heat-load, and `FILAMENT_PURGE_MORE` have no
   ACE-lane guard at all.** `_FILAMENT_LOAD_COLD`'s `FORCE_MOVE STEPPER=extruder DISTANCE=10`,
   `_FILAMENT_HEAT_LOAD`'s purge sequence, and `FILAMENT_PURGE_MORE`'s unconditional `G1 E20`
   run once their sensor preconditions are met (`entry` true; or `postgear`/`entry` true) — states
   an ACE lane reaches in ordinary operation (entry-only mid-load, or parked-at-postgear). None of
   the three checks `printer["ace"].current_index`, unlike every sibling extruder-motion macro in
   this codebase (`_TIP_SHAPING`, `_PARK_FILAMENT_POSTGEAR`, `_SEEK_POSTGEAR_STEP`,
   `_RESUME_UNPARK`, `_BRUSH_PRIME` — all independently patched 2026-08-27..31 with the same
   `ACE_ENABLE_*_ASSIST` + hard-require pattern). Already flagged by the repo's own
   `assist_lint.py` (`_FILAMENT_HEAT_LOAD`, `_FILAMENT_GRAB` named explicitly in
   `VALIDATED_MACROS.md`) — not yet fixed.

3. **Two independent implementations of "park an ACE lane."** `ACE_LANE_PARK`
   (`ace_toolchange.cfg`) and `FILAMENT_PARK`/`_PARK_FILAMENT_POSTGEAR` (`park.cfg`) each carry their
   own ACE-lane guard and have each been independently patched for the same class of direction-
   coherence bug (mode 2 vs mode 3). `PRINT_END`/`CANCEL_PRINT` use the latter; mid-print swaps use
   the former.

4. **`ACE_PLAN_SWAP`'s armed target is RAM-only.** `SET_GCODE_VARIABLE`, not `SAVE_VARIABLE` — a
   planned swap silently evaporates on a Klipper restart with no warning to the operator that it's gone.

5. **`ACE_COLD_RETRACT` swallows driver failures.** Own description: "Does not raise on failure." A
   refused retract looks identical to a successful one on screen unless the console text is read.

6. **`SPOOL_GUARD_CONTINUE`/`SPOOL_GUARD_STOP` answer a dialog that cannot currently fire.** The
   file's own header states `--mode ask` "DOES NOT CURRENTLY WORK ON THIS MACHINE" because `PAUSE`
   is ignored during `PRINT_START`'s `startup_in_progress` window.

7. **`PRINT_ABORT_START` is the automatic exit from every print-start abort checkpoint, and it skips
   every cleanup step every other terminal macro has.** No `_ACE_SUPPRESSION_DISARM`/assist release,
   no `_TIP_SHAPING`/`FILAMENT_PARK`, and it calls the renamed stock cancel (`BASE_CANCEL_PRINT`)
   directly rather than `CANCEL_PRINT` — bypassing `MMU_TTG_MAP RESET=1`, `ACE_DRYROLL_RELEASE`,
   `ACE_RECONCILE_TARGET`, and the `_END_HEATER_SAFETY` fuse all in one step. If it fires with ACE
   feed/rollback assist armed, nothing disarms it.

8. **The wipe on/off toggles don't persist.** `FILAMENT_WIPE_ENABLE/DISABLE/TOGGLE`,
   `ENABLE_KOMB`/`DISABLE_KOMB` all write with `SET_GCODE_VARIABLE`, not `SAVE_VARIABLE` — a
   restart silently reverts to the config-file default (`True`), regardless of what was last chosen.
   Consequential specifically because the documented use case for disabling it is a broken brush arm.

9. **`CALIBRATION_PTFE_LENGTH` has no distance cap.** Unlike its sibling `_SEEK_POSTGEAR`
   (explicit `max_distance: 200.0`), this one extrudes 1mm cold every 0.2s with no upper bound — if
   the post-gear switch never trips (no filament, dead sensor, clamped ACE lane), it runs
   indefinitely. `PTFE_SFS_LENGTH_CALIBRATE`, next to it in the same file, is dead the other way:
   its entire body is an unconditional `action_raise_error` — a button that can never succeed.

---

## Proposed memory entries (not written — for the curator to review)

- **`ux-command-surface-audit`** (type: project) — the 93-macro count, the rename/internalize/delete
  list from §3, and the confusability ranking from §2, so a future session doesn't re-derive them.
- **`ace2-tip-state-not-consulted`** (type: project) — the correctness finding in appendix item 1:
  `tip_state` exists, is written correctly, and is read by exactly one internal hook that the two
  commonest operator paths never reach. Extends `ace2-state-journal.md`.
