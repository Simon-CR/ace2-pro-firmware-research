# QOL / Waste review — ACE 2 Pro lane loading, preload, park

Read-only review, 2026-09-01. Live driver `~/ACEPRO/extras/ace/`, Klipper extras
`~/klipper/klippy/extras/ace_preload_guard.py` / `ace_path_calibrate.py`, config
`~/printer_data/config/`, log `~/printer_data/logs/klippy.log` (209,904 lines at read time).
Correctness/safety is QA's job, not this one — where a finding below is really a bug, it is
named and handed off, and this report scores only what it cost.

**One finding dwarfs the rest.** Everything expensive tonight traces to a single fallback branch
in the toolchange-failure handler (`commands.py`, `cmd_ACE_CHANGE_TOOL`'s exception path) that
writes a **guessed** `ace_current_index` as **committed fact** on a failure. That one write is
what turned a bad lane read into a night: it poisoned the Retry button (always re-targets the
same wrong lane), poisoned the status the operator would otherwise trust (Mainsail/KlipperScreen
read `ace_current_index` as "current tool"), and forced the eject-by-eject-by-hand elimination
that burned the hour. Fix it once, at the source, and the two biggest items on both lists below
shrink together.

## Timeline reconstructed from the log (basis for every number below)

Restart anchor: `klippy.log:91772`, `Start printer at Tue Sep 1 07:27:59 2026 (... 59691.7)`.
Every later `Stats N.N:` line converts to wall clock as `07:27:59 + (N − 59691.7)`.

| Wall time | Log line(s) | Event |
|---|---|---|
| 07:35:23 | 104816 | First `ACE_TANDEM_EXTRACT` failure at the 48mm cap, T0 |
| 07:41:10 → 07:43:47 | 143539 … 144257 | **15 consecutive identical 48mm-cap failures**, all T0 |
| 07:58:44 | 170243 | Extraction failure, T1, different cap (35mm) |
| 08:09:11 | 183705 | Second isolated 48mm-cap failure, T0 |
| ~08:20:15 | 209167 | Last clean `ace_mmu_shim: gate map restored` — gate 0 → spool 22, still correct |
| 08:20 – 08:35 | 209673–209904 | Manual RFID rescans; T1 returns `sku=SM22` (T0's own tag) |
| 08:35:50 | `_rfid_before.json` | Live snapshot: gate map now corrupted (see below) |
| after 08:35 | 209613–209900+ | Recovery: `ACE_LANE_NORMALIZE` run on T1 (twice), T2, T0 |

`grep -c "entry never cleared after 48mm of tandem pull"` → **17** exact hits tonight (15 in the
one cluster above, plus the two isolated ones) — consistent with the reported 16; I'm using the
log-verified 17 throughout. `extract_speed: 20` / `extract_cap: 48` confirmed in
`ace_unload.cfg:49-50`.

**RFID theft, with hard evidence, not inference.** `ace_mmu_shim: gate map restored` logs the
*persisted* map identically at all four reconnects tonight (lines 169665, 182852, 196420,
209167): `index 0 → spool_id 22 (eSUN Natural PLA+)`, `index 2 → spool_id 24 (Bambu Yellow)`.
The live snapshot captured at 08:35:50 (`L:\overnight\_rfid_before.json`, `eventtime 63758.08`)
shows the *runtime* map after the corruption: `ace_gate_map["1"].spool_id == 22`,
`ace_gate_map["0"].spool_id == -1` — gate 0's real binding wiped, gate 1 wearing gate 0's
identity. `ace_staged_mm: [0.0, 884.1, 884.1, 0.0]` in the same snapshot proves T0's tracked
position was genuinely lost (not just displayed wrong) while T1/T2 still held real numbers. The
causal window is narrow and provable: correct at 08:20:15 (line 209167), corrupted by 08:35:50,
and in between the only relevant event is `[RFID] scanning T1` returning `sku=SM22` (line 209687)
— an antenna crosstalk read of the neighbouring lane's tag, not a race in this code.

## QOL opportunities (ranked)

| # | What | Cost / occurrence | Cost / session (tonight) | Fix | Effort |
|---|---|---|---|---|---|
| 1 | `ace_current_index` committed from a **guess**, not evidence, on toolchange failure | Poisons every subsequent read of "what's loaded" until manually corrected | Root cause of the whole night: 17 doomed retries + the >1h hand-diagnosis + 4 forced re-normalizes | Don't write `active_tool = tool_index` when `is_filament_path_free_instant()` (already called one branch away) says the path is occupied — write "indeterminate" instead, per the state-journal rule already on file | **Small** — the check exists, it's a routing fix |
| 2 | Retry button re-issues the **identical** failing command | ~10.4s of dead motion + a click, guaranteed to fail from attempt 2 on | 15 attempts, 156.5s, zero successes | Same 3 call sites (`commands.py:1887`, `runout_monitor.py:523,932`) already emit a full diagnosis in the error text (`"...Run ACE_AUDIT, then ACE_LANE_EJECT T=0 FORCE=1..."`) — make THAT the primary button instead of bare `T{n}` | **Small** — text already written, just not wired to a button |
| 3 | RFID cross-read silently overwrites another lane's identity | Undiagnosable at the console; only visible as "this lane now refuses to load" | The single biggest time cost tonight (task-reported >1h of eject/insert elimination) | Before writing a fresh RFID `sku` into `ace_gate_map`, check whether that `sku` is already bound to a *different* gate; refuse/flag instead of silently overwriting | **Small** (guard the write) — the antenna crosstalk itself is a hardware RE question, out of this scope |
| 4 | `FLASH_LED` (opcode 70, per-lane, `components/loop/quick1/slow1/quick2/slow2`) has **zero callers** anywhere — confirmed by grep across every driver `.py` and every `.cfg`/macro | An addressable light on every lane, unused since day one | Every top finding tonight was a legibility failure the operator paid for *at the machine* | See ranking below — wire the highest-value state first | **Medium** — protocol fully decoded already, needs the state→pattern mapping + one call site per transition |
| 5 | No operator-visible surfacing of the contradiction the guard already detects | Operator has to eject lanes by hand to learn what `ace_preload_guard._check_invariant` already knows and logs | Same >1h — this is the capability that was already paid for and sitting idle | `_check_invariant` already computes and `logging.warning`s "STATE INVARIANT VIOLATED" — promote that from console/log to a persistent panel flag | **Small** — detection exists, only the surfacing is missing |
| 6 | `ACE_SCAN_TAG` is a fetch, not a live scan — a clean read only lands ~2.5s **after** `ACE_LANE_NORMALIZE`'s own motion turns the spool a full revolution (`ace_path_calibrate.py`'s own comment: a preload aborted at `grab_length` is under one turn and misses the tag) | Undocumented anywhere an operator can see it | Plausibly stretched the hand-diagnosis: re-ejecting before a normalize's follow-through completes defeats the read it was about to get | Surface this as UI copy ("reading tag — do not remove") during the 2.5s window, or just say it in the panel instead of only in a code comment | **Small** |

**FLASH_LED candidate meanings, ranked by operator time saved:**
1. **Per-lane ground truth** (loaded-confirmed / empty / staged-unconfirmed / fault) — turns
   ">1 hour of eject-by-eject elimination" into "look at the unit." Highest value by a wide margin
   because it attacks tonight's #1 and #3 costs directly.
2. **RFID conflict flag** — would have caught the identity theft in the ~15s it took to happen,
   not after an hour of symptom-chasing.
3. **Motion-fault / cap-hit indicator** — would have told the operator after failure #1 or #2 that
   clicking Retry again would not help, likely cutting the 15-attempt storm to 1-2.
4. **Preload/normalize-busy** — legible, but lower marginal value; motion is already visible and
   audible at the unit.
5. **Dryer status** — least tied to tonight's cost, already partly visible elsewhere.

## Waste (ranked)

| # | What | Cost / occurrence | Cost / session (tonight) | Fix | Effort |
|---|---|---|---|---|---|
| 1 | Repeated cold grind at the **same fixed 48mm span** | 48mm of commanded tandem gear rotation (extruder 20mm/s + ACE side), **verified zero displacement** — `entry never cleared` is the guard's own confirmation nothing moved | 17 × 48mm = **816mm of grind-equivalent motion**, 15 of those hits landing on the identical span inside 156.5s — compounding a notch/flat at one point rather than spreading wear, per the precedent already on file (`ace2-eject-buckle.md`, `ace2-cutter-hh-comparison.md`) that a fixed-spot grind produces a section that must be cut off before the spool is reusable | Same fix as QOL #1/#2 — if the failure signature repeats identically, stop offering the identical action | **Small** |
| 2 | 4 full `ACE_LANE_NORMALIZE` cycles in the final ~10 minutes of recovery (T1 ×2, T2, T0) — each a 784mm bulk feed @60mm/s + fine seek + step-back + final offset | ~35-40s of motion each (13.1s bulk alone; `ace_path_calibrate.py`'s own comment measures the all-fine-chunk alternative at ~70s, so this design is the *cheap* path, not the expensive one) | ~140-160s of pure re-homing motion | **This is insurance being paid correctly, not a design flaw** — it only runs when a lane's position is genuinely unknown (confirmed by the snapshot: T0's `ace_staged_mm` really was `0.0`), and the error text's own recommended recovery (`ACE_LANE_EJECT T=0 FORCE=1`) necessarily zeroes that position. Fixing QOL #1-#3 removes the *need* to pay this 4 times, not the mechanism itself | N/A — do not touch the normalize routine |
| 3 | RFID requery on every reconnect | 4 slots queried per reconnect | 4 reconnects × 4 slots = 16 protocol reads tonight, plus the manual rescans | Negligible — protocol traffic, no motion, no wear. Noted only so it isn't mistaken for something bigger | — |

## The top three, in prose

**1. The guessed `active_tool` write is the night's actual cause, not a symptom of it.**
`commands.py:1796-1846` decides what to commit to `ace_current_index` after any toolchange
exception. In the branch that fired tonight (idle/startup, `filament_pos == "bowden"`, path
**not** free — i.e. someone else's filament is sitting in the shared bowden), the code calls
`manager.is_filament_path_free_instant()` (a cheap, sensor-grounded check — toolhead switch +
RDM, `manager.py:972-988`), gets `False`, and then **pins `active_tool = tool_index` anyway** —
the tool the operator just *asked for*, not the one physically in the path. That single write is
inconsistent with the state-journal rule already standing on this machine ("status dictates
permissions… commit only after verified success") and it is why every downstream signal agreed
with each other and was wrong together: the Retry button, the Mainsail/KlipperScreen "current
tool" field, and `ACE_GET_CURRENT_INDEX` all read the same poisoned value. Fixing this one
write — commit "indeterminate," not a guess, whenever the path-free check already says the guess
is unsafe — removes the need for both the Retry-loop fix and a chunk of the eject-by-eject
diagnosis in one move.

**2. A Retry button that cannot succeed, offered as the primary action, 15 times in 156.5
seconds.** The exact string `Retry T{tool_index}|T{tool_index}|primary` lives in three places
(`commands.py:1887`, `runout_monitor.py:523`, `:932`) and always re-issues the literal command
that just failed against whatever state just produced the failure — with #1 unfixed, that state
never changes between clicks. The error text sitting right next to it is genuinely good — it names
the measured distance, gives the healthy baseline (~28mm against a 20mm entry→postgear span),
explains what 48mm means mechanically, and names the exact recovery command
(`ACE_AUDIT` → `ACE_LANE_EJECT T=0 FORCE=1`). None of that diagnostic value reaches a button. This
is the standing "any error message naming a command is a missing button" rule, caught in the act:
the fix is not new logic, it's wiring text that already exists to a control that already exists.

**3. RFID identity theft writes silently and is discovered only by elimination.** The snapshot at
`L:\overnight\_rfid_before.json` proves gate 1 usurped gate 0's spool binding sometime between
08:20:15 and 08:35:50, triggered by a cross-lane tag read (`sku=SM22` returned for a scan of T1,
line 209687) that nothing checked against the map already on record. This is the single most
expensive item on the night by the task's own framing (over an hour), and the fix is narrow: a
scan result that contradicts an existing binding should refuse the overwrite and say so, not win
silently. The antenna crosstalk itself — why an adjacent slot's reader picked up the wrong tag —
is a hardware RE question for the ACE2 findings doc, not something this pass can size.

## What is already good — worth protecting while fixing the rest

- **`ACE_LANE_NORMALIZE`'s bulk-then-fine-then-backoff shape is genuinely well engineered.** It
  only pays the 784mm feed when the position is truly unknown, and the design explicitly rejected
  the naive all-chunked alternative after measuring it at ~70s vs. today's ~13s bulk leg
  (`ace_path_calibrate.py` header comments). Do not "optimize" this by skipping it — that is
  exactly the assumption Simon's own datum rule forbids, and it is cheap relative to what it
  buys.
- **The error text on a failed tandem extraction is excellent** — it states the measured distance,
  the healthy baseline, what reaching the cap mechanically implies, and the exact recovery
  command. The gap is entirely in the button layer above it, not in the diagnosis itself.
- **`ace_preload_guard._check_invariant` already detects exactly the class of contradiction that
  cost an hour tonight** (filament at a sensor with no lane owning it, or a lane recorded loaded
  with no sensor backing it) and logs it unprompted. The capability exists; only the last step —
  putting it somewhere the operator looks — is missing.
- **The persisted gate map held correct through the whole night** (`gate map restored` agreed at
  all four reconnects, right up to 08:20:15) — the corruption was a narrow, provable, live-only
  window, not a systemically broken subsystem. Most of the safety net worked.

## Proposed memory entries (not written — curated elsewhere)

- **`ace2-current-index-guess-on-failure`** (type: project) — one line: the toolchange-failure
  fallback in `commands.py` commits a guessed `active_tool` instead of "indeterminate" when
  `is_filament_path_free_instant()` already says the guess is unsafe; this is the root cause of
  the 2026-09-01 wrong-lane retry storm and the RFID-theft diagnosis cost, and the fix is a
  routing change at an existing check, not new logic.
- **`ace2-flash-led-unused`** (type: project) — one line: `FLASH_LED` (opcode 70,
  `protocol_ace2.py:38/336`) is fully decoded (per-lane, two-phase blink timing) and has zero
  callers anywhere in driver or config; ranked candidate uses and the reasoning for the ranking
  live in `L:\overnight\qol-lane-loading.md`.
