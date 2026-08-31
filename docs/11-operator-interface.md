# The operator interface: what the ACE 2 Pro's control panel has to be

**Reviewed 2026-08-31.** Machine: ACE 2 Pro (fw V1.1.3W) on a Voron Trident, Klipper +
[Kobra-S1/ACEPRO](https://github.com/Kobra-S1/ACEPRO) driver, Mainsail v2.19.0 (:80) and a
Happy-Hare Mainsail fork v2.17.0 (:82), KlipperScreen on an 800×480 HDMI panel.

---

## Why this is not a cosmetic chapter

**The ACE 2 Pro has no controls on the unit itself.** No buttons, no screen, no lid switch you can
hold. Every load, eject, park, dry, recover, calibrate and diagnostic reaches it through Klipper.

That makes the front-end *the machine's control panel*, not a skin over one. A missing button on a
normal printer is an inconvenience. Here it is a capability the owner does not have — the action
can only be performed by typing a macro name from memory into a console.

Everything below follows from that single fact.

---

## 0. What can and cannot be built (settled — do not re-derive)

| Claim | Status |
|---|---|
| Mainsail has a plugin / custom-card API | **No.** Checked against the docs and the compiled bundles. |
| Fluidd has one | **No.** |
| A custom page can be served alongside Mainsail | **Yes** — this is what `/ace/` already does. Prior art: [decay71/multiACE](https://github.com/decay71/multiACE) serves at `/multiace/`. |
| The compiled Mainsail bundle can be patched | Yes, and is — `panel/patch_mainsail.py` injects a nozzle sensor node into the MMU card's Vue render function after every Mainsail update. Fragile by construction. |
| A standalone app on its own port could talk to Moonraker | **Not without work.** `trusted_clients` covers RFC1918, but `cors_domains` is narrow (`*.lan`, `*.local`, localhost, `my.mainsail.xyz`). A raw `IP:port` origin fails CORS on REST. Serving from the existing nginx origin sidesteps it entirely. |
| KlipperScreen can answer `action:prompt_*` dialogs | **Yes** — core feature, `ks_includes/widgets/prompts.py`, dispatched from `screen.py:912`. See §3 for the defect. |

**Conclusion: improve `/ace/`. Do not start a new front-end.** It already solves delivery, auth and
CORS by being same-origin, and it is restored automatically after every Mainsail update.

---

## 1. What exists today

| Surface | File | State |
|---|---|---|
| Web panel `/ace/` | `~/printer_data/config/panel/ace_index.html` (14.7 KB) | Live on :80 and :82. Websocket to `/websocket`, SVG filament path, per-lane rows, dryer stats. **Zero buttons — it is a read-only dashboard.** |
| Bundle patcher | `~/printer_data/config/panel/patch_mainsail.py` | Restores `/ace/` into both sites and patches the MMU card. Trigger is **cron `*/15`**, not the systemd path unit its own docstring claims. |
| Lane dialog | `~/printer_data/config/ace_ui.cfg` → `ACE_LANES` | The real control surface. `action:prompt_*` dialog: per-lane status, four eject buttons, a park button, three footer buttons. |
| MMU card | Happy-Hare Mainsail fork, compiled into `assets/index-*.js` on both sites | Vertical filament graphic + sensor dots + MMU command buttons. |
| KlipperScreen ACE panel | `~/ACEPRO/KlipperScreen/acepro.py` (3487 lines) | **Not installed.** No symlink in `~/KlipperScreen/panels/`, no menu entry in `KlipperScreen.conf`. It has never run on this machine. |

So the honest summary of the operator's current options:

- **At a desk:** a read-only web page you have to know the URL of (nothing links to `/ace/` from
  Mainsail), plus `ACE_LANES` from the macro list, plus the console.
- **At the machine:** `ACE_LANES` from KlipperScreen's macro list — which renders, as §3 shows,
  as **one line of text and four EJECT buttons**.

---

## 2. Broken — ranked by how often the operator hits it

### 2.1 A staged lane is published as UNLOADED, so every panel lies about it

**Live, right now.** Lane 2 holds 884.1 mm of filament staged down its bowden:

```
ace_preload_guard.staged        = {"2": 884.1}
mmu.filament_position_per_gate  = [0.0, 0.0, 884.1, 0.0]
mmu.filament_pos_per_gate       = [0, 0, 0, 0]        <-- 0 == "unloaded"
```

Cause, `klippy/extras/ace_mmu_shim.py:982`:

```python
"filament_pos_per_gate": [POS_UNLOADED if p == POS_HOMED_GATE else p
                          for p in per_pos],
```

`_bowden_fraction()` returns `POS_HOMED_GATE` *specifically* to mean "staged in its own lane tube,
tip drawn on the hub dot". The publisher then flattens that to `POS_UNLOADED`.

**The collapse is deliberate and correct for what it was for.** The comment above it says so: the
Happy-Hare Mainsail card only lights its Load button at pos 0, and in HH's enum a lane resting at
the gate *is* 0 — their 1 is a mid-load transient. **Do not revert it.**

The damage is downstream, in the panels that wanted the richer value:

| Panel | What it draws | What is true |
|---|---|---|
| `/ace/` lane row | `laneState()` → **"in ACE, not fed"** (`ace_index.html:202-206`) | 884 mm down the lane tube |
| `/ace/` path diagram | Lane 2 filled to **94.6 %** of the way to the hub (`ace_index.html:273-274`, from the *mm* array) | correct — and directly contradicts the row above it |
| `/ace/` shared-run colour | `owner = perGatePos.findIndex(p => p >= 2)` → `-1` → `slots["-1"]` undefined → generic blue | should be the lane's colour |
| `ACE_LANES` dialog | **"T2 path: at the ACE, never fed"** (`ace_ui.cfg:72-75`) | same lie, on the touchscreen |

One card making two contradictory claims about the same lane, on the same screen, at the same time.

**Fix:** publish the uncollapsed array under a second name (`filament_pos_per_gate_true`, or a
`staged_mm` array) and have `/ace/` and `ACE_LANES` read that. The HH contract keeps its collapsed
number. Effort: ~10 lines in the shim, ~5 in the panel. Payoff: removes the single most-hit lie.

### 2.2 KlipperScreen renders only the LAST `prompt_text` of a dialog

`~/KlipperScreen/ks_includes/widgets/prompts.py:37`:

```python
elif data.startswith('prompt_text'):
    self.text = data.replace('prompt_text ', '')      # OVERWRITES
    return
```

`show()` then builds exactly one `Gtk.Label` from `self.text`. Mainsail accumulates every
`prompt_text` line; KlipperScreen keeps only the last.

`ACE_LANES` emits **ten to twelve** `prompt_text` lines. At the machine the operator sees the last
one only — typically `T3: empty` — and then four buttons labelled *T0 eject … T3 eject*.

Discarded: the toolhead state (melt zone vs parked-and-shaped vs "present but state says
unloaded"), which lane is loaded, entry/postgear sensor states, all four lane path positions, and
the jam warning.

**This is the worst finding in the review.** Gloves on, mid-print, the touchscreen presents four
destructive buttons and no basis whatsoever for choosing between them.

Two fixes, do both:

1. **Immediate, zero risk:** make the *last* `prompt_text` of every ACE dialog the one-line summary
   that carries the decision. Pure macro edit, no core patch, survives every update.
2. **Proper:** one-line change to `prompts.py` (`self.text += "\n" + …`) and upstream it. It will be
   lost on KlipperScreen update, so pin it with the same cron-restore mechanism `patch_mainsail.py`
   already uses, or get it merged.

### 2.3 `ace_lane_pos` holds a value nothing writes and nothing reads

`save_variables.ace_lane_pos = ['gate', 'parked', 'parked', 'gate']`.

`ace_lane_state.cfg:12-15` documents exactly four legal values: `gate`, `bowden`, `toolhead`,
`empty`. `parked` is not one of them. The only writer, `_ACE_LANE_STATE`, is never called with
`POS=parked` anywhere in the tree — the value is the seed default from `variables.cfg:31` and has
never been overwritten.

Consequence in `ace_ui.cfg:72`: the test is `stored[i] == 'bowden'`, so lanes 1 and 2 fall through
to the `else` branch and read **"at the ACE, never fed"**. This is a second, independent path to
the same lie as §2.1.

Also stale: `gcode_macro _ACE_LANE_STATE.positions` reads `['gate','gate','gate','gate']` while
`save_variables.ace_lane_pos` reads `['gate','parked','parked','gate']`. **A UI must read
`save_variables`, never the macro variable.**

### 2.4 The MMU card's bowden animation is dead, and is load-shaped by construction

The filament graphic is a single SVG rect anchored at the gate end and grown toward the nozzle:

```
<rect ref="filamentRect" class="filament-animation"
      x="243" y="25" width="14" height="{filamentRectHeight}" fill="{currentGateColor}">
```

`.filament-animation { transition: height .5s ease-in }` — so height changes tween.

The geometry is right (top = pre-gate, bottom = nozzle; the landmark constants line up exactly with
the sensor dot y-positions). The problem is the interpolation:

```js
if ([START_BOWDEN, IN_BOWDEN].includes(this.mmuFilamentPos) && this.bowdenProgress >= 0) {
    const s = this.endOfBowdenPos - Fe.START_BOWDEN;
    return Fe.START_BOWDEN + s * this.bowdenProgress / 100;     // progress 0 -> gate, 100 -> nozzle
}
```

Two facts:

- **The branch is dead here.** `ace_mmu_shim.py:1023` publishes `"bowden_progress": -1` as a
  hardcoded constant. The `>= 0` guard never passes, so the bar teleports between five discrete
  heights instead of sweeping. There is no smooth bowden animation on this machine at all.
- **The mapping is unconditionally load-shaped.** `progress → distance-from-gate` regardless of
  travel direction. Happy Hare counts `bowden_progress` 0→100 across the *unload* move too. The
  component *reads* `mmu.filament_direction` (in the encoder sub-component) and never consults it
  here. So the moment `bowden_progress` is wired up honestly, **the bar will sweep gate→nozzle
  while filament travels nozzle→gate.** That is the reported reversal, sitting latent in the
  formula.

`mmu.filament_direction` is not published by the shim either.

**Verdict:** the reversal is real and provable *in the code*; it is currently masked by the dead
constant rather than fixed. Wiring `bowden_progress` without also publishing `filament_direction`
and branching on it would ship the reversed animation immediately.

**Recommendation:** do not wire `bowden_progress` into the HH card. Draw the bowden run in `/ace/`
instead, from `ace_hub_encoder.distance_mm / ace_cal_hub_to_entry`, which is a measured pulse count
rather than a commanded-length reckoning and is signed by the encoder itself.

### 2.5 A dialog whose text names two buttons it does not render

`ACEPRO/extras/ace/manager.py:2754` — connection-issue prompt: *"Please fix the issue, then use
RESUME to continue or CANCEL_PRINT to abort"*. The only button rendered (`:2766-2769`) is
**Dismiss**. An operator who trusts the buttons is stranded; one who reads the prose has to go find
a console. Add the two buttons the sentence already names.

### 2.6 The KlipperScreen ACE panel would ship two dead controls if installed

Not currently reachable (not installed), so payoff is deferred — but fix before installing:

- **`TR` does not exist.** `acepro.py:1628` and `:1652` send it for every Unload. Nothing registers
  `TR` anywhere in the tree. Worse, the panel raises an "Unloading…" toast *before* the response
  arrives, so the operator sees a success message followed by an "Unknown command" popup, with the
  filament untouched.
- **The mid-print lock is a no-op.** `_update_ace_pro_switch_lock` (`:910`) reads
  `self.ace_pro_control`, which is never assigned — `ace_pro_control` is a local in
  `create_main_screen`. `getattr(...)` returns `None` every time, so the ACE Pro power switch is
  never disabled during a print.

### 2.7 The bundle patcher's failure mode is silent, and its docstring is wrong

`patch_mainsail.py:45-46` — when a new Mainsail release reshapes the toolhead node, the regex misses
and the loop `continue`s. It then returns `"no MMU bundle found"`, **the same string it returns when
there is genuinely no bundle**, and `main()` returns 0 regardless. Nothing distinguishes "nothing to
do" from "the patch broke on a new build".

The docstring claims a systemd path unit `ace-panel-patch.path` fires on `index.html` rewrite. No
such unit exists. The real trigger is `crontab -l`: `*/15 * * * *` plus `@reboot sleep 60`. So after
a Mainsail auto-update the panel and the nozzle dot can be missing for up to 15 minutes, silently.

### 2.8 Small, cheap, and each one a picture that can lie

| Item | Detail |
|---|---|
| `/ace/` subscribes to `ace_hub_encoder` and never reads it (`ace_index.html:100`) | `distance_mm` is exactly the field that would draw the shared bowden run correctly (§2.4). Subscribed, ignored. |
| `ace_buffer_watch.lane_empty` is always `[false,false,false,false]` | Even with lanes 0 and 3 physically empty. Dead field. `inserted` (inverted) is the live one. `/ace/` and `ACE_LANES` already use `inserted` — keep it that way and delete `lane_empty`. |
| `/ace/` draws entry=0 / postgear=1 as a normal state | `renderPath` (`ace_index.html:302-306`) tests `postOn` before `entryOn` with no cross-check. That row of the truth table is **impossible on a continuous strand** — it means broken filament or a dead switch. It should be drawn as a fault, not as healthy. |
| `ace_path_calibrate` publishes `{}` at rest | Nothing to show; `/ace/` prints "No calibration stored — run ACE_CALIBRATE_PATH" from `save_variables` instead, which is correct. But see §3.1: that sentence should be a button. |
| `acepro.py` subscribes to `dryer`, `fan_speed`, `enable_rfid` | None exist on the live object (`dryer_status`, `rfid_sync_enabled`, no fan field). Harmless — Moonraker just omits them — but they are lies in the source. |

### 2.9 Most of the Mainsail MMU card's buttons cannot work on this machine

The Happy-Hare card is compiled against a full HH installation. This machine has a shim, not Happy
Hare. Classifying every `MMU_*` string in the bundle by whether its surrounding minified context
dispatches gcode gives **19 commands the card actually sends**. Six are implemented here:

`MMU_GATE_MAP` · `MMU_LOAD` · `MMU_SELECT` · `MMU_SLICER_TOOL_MAP` · `MMU_TTG_MAP` · `MMU_UNLOAD`

**Thirteen are not implemented anywhere** — not in `ace_mmu_shim.py`, not as a `[gcode_macro]`:

`MMU_CHECK_GATES` · `MMU_ENDLESS_SPOOL` · `MMU_GRIP` · `MMU_HOME` · `MMU_LED` · `MMU_MOTORS_ON` ·
`MMU_MOTORS_OFF` · `MMU_RELEASE` · `MMU_REMAP_TTG` · `MMU_SERVO` · `MMU_SPOOLMAN` · `MMU_STATS` ·
`MMU_SYNC_GEAR_MOTOR` · `MMU_TEST_CONFIG`

Pressing one produces Klipper's "Unknown command" toast and nothing else.

**Partial mitigation already exists, by accident.** The shim deliberately omits `encoder`, `servo`,
`grip`, `sync_feedback_*`, `clog`/`flowguard` and `espooler` from the `mmu` object, and the card
hides widgets whose backing field is absent. That very likely hides `MMU_SERVO`, `MMU_GRIP` and
`MMU_SYNC_GEAR_MOTOR`. It does not plausibly hide `MMU_HOME`, `MMU_MOTORS_ON/OFF`,
`MMU_CHECK_GATES`, `MMU_STATS`, `MMU_TEST_CONFIG`, `MMU_LED`, `MMU_RELEASE`, `MMU_REMAP_TTG`,
`MMU_ENDLESS_SPOOL` or `MMU_SPOOLMAN`, which sit in the card's toolbar and settings menus.

There is precedent for exactly this failing silently rather than loudly. `ace_ui.cfg:171-179`
records the "MAP TOOLS" button: the dialog's render read
`gcode_macro _MMU_SOFTWARE_VARS.automap_strategy` without optional chaining, the shim did not
provide the macro, the render threw, and **the button silently did nothing** — no error, no toast.
That was found on 2026-08-26 and fixed with a stub. Every unimplemented command above is a candidate
for the same class of failure.

**Recommendation:** stub or hide, per command. A stub that says *"this machine has no selector —
nothing to home"* is worth more than a button that errors, and far more than one that does nothing
visible. Decide each one; do not blanket-stub, or the card starts lying in a new way.

### 2.10 Suspicions that turned out to be wrong

Worth recording so nobody re-raises them:

- **`ACE_SCAN_TAGS` is not dead.** It is registered in `klippy/extras/ace_rfid_scan.py:40`, and
  `[ace_rfid_scan]` is loaded from `ace.cfg:695`. It does not appear in `printer/objects/list`
  because that endpoint only lists objects with a `get_status`, not commands registered by a Python
  extra. The footer button works.
- **`ACE_BUFFER_DISARM` is not dead either** — `ace_buffer_watch.py:100`. The problem with it is
  §3.2, not existence.
- **Gates 4–5 (the hand-fed manual lanes) *are* distinguishable** from the four real ACE lanes:
  `mmu.manual_tools == [4,5]`. A UI has no excuse for drawing them as ACE lanes.
- **The `/ace/` panel has no dead buttons**, because it has no buttons.

---

## 3. Missing / awkward — ranked by how often the operator hits it

### 3.1 The rule

> Every message that tells the operator to type a command is a missing button. The machine has
> already worked out what should happen next; it should offer it.

A sweep of every `RESPOND`, `action_raise_error`, `respond_info` and raised `CommandError` in the
config tree and the driver found **23 distinct commands named in error or status text with no
button anywhere**. Ranked by number of distinct sites naming them:

| Command | Sites | Named at |
|---|---|---|
| `ACE_AUDIT` | 5 | `ace.cfg:641`, `ace_toolchange.cfg:435,520,572,638` |
| `MMU_GATE_MAP GATE=<n> SPOOLID=/MATERIAL=` | 4 | `ace.cfg:989,1076`; `ace_purge.cfg:286,678` |
| `SAVE_VARIABLE VARIABLE=filament_loaded_hot` | 3 | `ace_mmu_commands.cfg:130,190`; `ace_unload.cfg:135` |
| `MMU_RECOVER GATE=<n>` | 3 | `ace_mmu_commands.cfg:190,192,199` |
| `ACE_LANE_PARK T=<n>` | 3 | `ace.cfg:921`; `ace_toolchange.cfg:220`; `ace_unload.cfg:305` |
| `ACE_LANE_EJECT T=<n>` | 2 | `ace_unload.cfg:285,305` |
| `ACE_PURGE_NOW LENGTH=` | 2 | `ace_purge.cfg:517,522` |
| `MMU_UNLOCK`, `ACE_CLEAR_OP` | 1 each | `ace_toolchange.cfg:638` (same message) |
| `ACE_BUFFER_DISARM` | 2 | `ace_ui.cfg:80`; `ace_buffer_watch.py:241` |
| `ACE_DISABLE_FEED_ASSIST T=<n>` | 1 | `ace_toolchange.cfg:572` |
| `ACE_PLAN_SWAP CANCEL=1` | 1 | `ace.cfg:1002` |
| `ACE_LANE_UNLOAD_ABORT` | 1 | `ace_unload.cfg:110` |
| `ACE_PRELOAD_GUARD_STATUS` | 1 | `ace_unload.cfg:128` |
| `PRINTER_STATE` | 1 | `ace_unload.cfg:135` |
| `ACE_DRYROLL_MODE`, `ACE_DRYROLL_START`, `ACE_LANE_NORMALIZE` | 1 each | `ace_dryroll.cfg:282,419` |
| `ACE_AUTODRY`, `ACE_DRY` | 1 each | `ace.cfg:191,333` |
| `MMU_TTG_MAP TOOL=<n> GATE=<n>` | 1 | `ace_preflight.cfg:185` |
| `SPOOL_REASSIGN SPOOL=<n>` | 1 | `spool_guard.cfg:183` |
| `MMU_SELECT_BYPASS` | 1 | `ace_mmu_shim.py:1153` |
| `ACE_CLEANUP_STALE_VARS CONFIRM=1` | 1 | `manager.py:513` |
| `QUERY_FILAMENT_SENSOR SENSOR=<rdm>` / `ACE_CHANGE_TOOL TOOL=-1` | 2 | `manager.py:2898,2918` |
| `ACE_RESET_SHARED_BUS_BINDINGS` | 1 | `manager.py:3569` |

Two of these deserve special mention:

- **`ACE_BUFFER_DISARM` is named inside a dialog** (`ace_ui.cfg:80`, the jam-latched line). The
  dialog is *already open* and *already knows the tool number*. Printing the command name instead
  of adding `Clear jam on T{n}|ACE_BUFFER_DISARM` is the purest form of this failure — and it
  happens at the exact moment the operator is least able to go look something up.
- **`/ace/` itself does it** — `ace_index.html:319` prints *"No calibration stored — run
  ACE_CALIBRATE_PATH."* on a page that has no way to run it.

### 3.2 What the operator actually types

Moonraker's `gcode_store` ring buffer (1000 entries, 2026-08-29 23:39 → 2026-08-31 00:46),
top-level `type: "command"` entries, macro-internal traffic filtered out:

| Count | Command | Has a button? |
|---|---|---|
| 17 | `ACE_RAW_FEED T= MODE= LENGTH= SPEED=` | no |
| 17 | `FORCE_MOVE STEPPER=extruder DISTANCE= VELOCITY=` | no |
| 12 | **`ACE_AUDIT`** | no |
| 3 | `ACE_RAW_STOP T=` | no |
| 3 | `ACE_RECONCILE_TARGET` | no |
| 3 | **`_ACE_SUPPRESSION_DISARM`** | no — and it is hidden |
| 2 | `MMU_UNLOAD` | no |
| 2 | `ACE_BUFFER_STATE` | no |
| 2 | `_ACE_SUPPRESSION_ARM SECONDS=` | no — hidden |
| 2 | `ACE_DEBUG_SET_TARGET_INDEX TOOL=` | no |
| 1 ea | `ACE_DRYROLL_STOP`, `ACE_LANE_EJECT T=0 FORCE=1 SHAPE=0 HOME=0`, `ACE_CLEAR_OP`, `_ACE_LANE_STATE T=0 POS=gate`, `ACE_PRELOAD_GUARD_STATUS`, `ACE_RAW_CMD`, `ACE_DEBUG_SET_*` | no |

> **Caveat, stated plainly.** That window overlaps a period of heavy assisted work on this machine,
> so some of those entries were typed by tooling rather than by hand. It does not change the
> conclusion: whoever typed them, there was no button, and the next person to need them will find
> the same wall. `ACE_AUDIT` in particular is corroborated independently — it is named in five
> separate error messages.

**`_ACE_SUPPRESSION_DISARM` is the sharpest single finding.** It is `_`-prefixed, so it is hidden
from every macro list. It is called by **nothing** — no macro body, no delayed_gcode, no Python
path; the `_ACE_SWAP_WATCHDOG` delayed_gcode duplicates its logic inline rather than calling it. A
hidden macro that nothing else invokes is not internal — it is a public recovery command that was
accidentally made invisible. It was typed bare, three times, to clear a stuck suppression flag.

The underscore hides a macro from the panel. It does not stop anyone needing it.

### 3.3 The controls that simply do not exist anywhere

Not in an error message, not in the console history — just absent:

| Missing | Why it matters |
|---|---|
| **Load a lane** | `ACE_LANES` offers eject on all four lanes and park on one. It cannot load. Selecting a colour is the single commonest ACE action and there is no button for it in the dialog built for lane actions. (`T{n}` is registered and works — it just is not offered.) |
| **Any dryer control** | `/ace/` shows four dryer numbers and a paragraph about absolute humidity, and cannot start or stop drying. `ACE_DRY`, `ACE_AUTODRY`, `ACE_DRYROLL_START/STOP` all exist. |
| **Stop motion** | There is no single "stop everything the ACE is doing" control. `ACE_STOP_FEED` / `ACE_STOP_RETRACT` / `ACE_DISABLE_FEED_ASSIST` exist separately. Per the standing rule that **feed assist is motion**, a runaway lane needs one button, reachable at the machine, that does all three. |
| **Assign a spool to a lane** | `MMU_GATE_MAP GATE=n SPOOLID=n` is named in four error messages. Spoolman is connected (`spoolman_connected: true`) and `mmu.gate_spool_id` is live. This is a picker, not a command line. |
| **A link to `/ace/`** | Nothing in either Mainsail links to it. The operator must remember the URL. |

### 3.4 Information density: `/ace/` is inverted

The page currently spends:

- a full card, four large numbers and a two-line paragraph of prose on the **dryer**, which is idle
  almost always and which the page cannot control;
- **zero pixels** on any action.

The prose ("Absolute humidity is the honest one — heating collapses RH with the same water still
present") is correct and worth keeping — in this document, not on a panel someone reads while
holding a spool. Collapse the dryer to one line (`29 °C · 22 % RH · 5.6 g/m³ · idle`) with a
control, and give the space to the lanes.

Conversely the page hides the numbers that decide the next action: which lane owns the shared path,
how far past the hub the tip is, whether a cold pull is legal, and whether the path is busy. Three
of those are already subscribed and unused.

---

## 4. The design

### 4.1 The split, and why

| | Web `/ace/` | KlipperScreen |
|---|---|---|
| Where the hands are | at a desk or on a phone, both hands free, time to read | standing at the machine, gloves on, often mid-print, one thumb |
| Belongs here | spool assignment, material/colour/temperature editing, dry schedules, path calibration, audit output, history, endless-spool config, per-lane diagnostics | see the four lanes, load one, park one, eject one, stop motion, clear a jam, run an audit, answer a prompt |
| Does not belong here | nothing — space is cheap | anything needing a keyboard, anything with more than two outcomes, anything you would only do once a month |

The test for KlipperScreen is: **you just opened the ACE lid, or you are watching a swap go wrong.**
If the action is not one of those, it goes on the web.

**Hard requirement:** the touchscreen must be fully usable without the web UI, *including answering
prompts*. Core KlipperScreen already supports prompts; §2.2 is the defect standing between that
support and it being true.

### 4.2 `/ace/` — proposed layout

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  ACE 2 Pro    ● connected  fw V1.1.3W     29°C · 22%RH · 5.6 g/m³ · dryer idle │
│                                                    [ Dry 4h ]  [ Auto-dry ▸ ]  │
├───────────────────────────────────────────────────────────────────────────────┤
│  PATH                                              ● path clear   no lane owns │
│   T0 ─────────────────────────╮                                                │
│   T1 ─────────────────────────┤                                                │
│   T2 ███████████████████████──┤━━━━━━━━━━━━━○────────○────────○  hub 555.9mm   │
│   T3 ─────────────────────────╯   hub      entry  gears   melt                 │
│   T2 is 50 mm short of the hub · nothing at the toolhead · cold pull is legal  │
├──────────────────┬──────────────────┬──────────────────┬──────────────────────┤
│ ▐ T0             │ ▐ T1             │ ▐ T2      LOADED │ ▐ T3                 │
│   empty          │   Unknown        │   Sunlu PLA      │   empty              │
│   —              │   at the ACE     │   spool 24 · 335g│   —                  │
│   ·············· │   ·············· │   staged 884/934 │   ··············     │
│                  │                  │                  │                      │
│ [ Load    ]  (i) │ [ Load       ]   │ [ Park swap-rdy] │ [ Load     ]  (i)    │
│ [ Eject   ]  (i) │ [ Eject      ]   │ [ Eject       ] │ [ Eject    ]  (i)    │
│ [ Assign… ]      │ [ Assign…    ]   │ [ Retract→gate ] │ [ Assign…  ]         │
│  no filament     │                  │  [ Assign… ]     │  no filament         │
├──────────────────┴──────────────────┴──────────────────┴──────────────────────┤
│  [ Audit ]  [ Recover ]  [ Clear op ]  [ Calibrate path ]        [ Console ]  │
└───────────────────────────────────────────────────────────────────────────────┘
```

Rules the layout encodes:

- **A lane is an object.** Colour, material, spool, where the filament actually is, and what can be
  done to it right now, all in one tile.
- **Offer only what the state allows, and disable rather than hide.** A greyed `Load` with the
  reason *"no filament in lane 0"* under it beats one that errors when pressed, and beats one that
  vanished. `(i)` marks a disabled control carrying a stated reason.
- **`path_busy` greys every motion control at once,** with the reason naming the owning lane.
- **Destructive and safe are not adjacent by accident.** `Eject` (spool comes out, needs re-threading
  by hand) is styled distinctly from `Park` (lane stays gripped, reloadable). The existing
  `ace_ui.cfg` header already makes this distinction in prose; the panel should make it in colour.
- **Gates 4–5 are drawn separately** (a fifth, narrower "hand-fed" strip), from `mmu.manual_tools`.
  They are not ACE lanes and must not look like them.

### 4.3 `/ace/` — data source behind every element

Nothing here is invented; every field was verified present on the live machine.

| Element | Source |
|---|---|
| connection pill | `ace_instance_0.connection_state`, `.status`, `.firmware` |
| dryer line | `ace_instance_0.temp`, `.humidity`, `.dryer_status.{status,target_temp,duration,remain_time}`; abs. humidity via Magnus (already implemented, `ace_index.html:143`) |
| `Dry` / `Auto-dry` buttons | `ACE_DRY`, `ACE_AUTODRY TARGET_RH= TEMP=` (`ace.cfg`) |
| path busy pill | `ace_preload_guard.path_busy` |
| lane colour swatch | `ace_instance_0.slots[i].color` — **`[0,0,0]` means absent, not black** (already handled) |
| lane material | `mmu.gate_material[i]` → fallback `ace_instance_0.slots[i].material` |
| lane family (for temp/shaper) | `mmu.gate_material_family[i]` |
| lane spool name / id / weight | `mmu.gate_filament_name[i]`, `mmu.gate_spool_id[i]`, `ace_instance_0.slots[i].{sku,total,current}` |
| lane present | `ace_buffer_watch.inserted[i]`, **null when `.stale`** — never `lane_empty` |
| lane under tension | `ace_buffer_watch.at_rest[i]` |
| staged mm / lane fill | `mmu.filament_position_per_gate[i]` (= `ace_preload_guard.staged`) |
| lane tube length | `save_variables.ace_cal_park_to_hub[i]`, else `ace_park_to_entry − ace_cal_hub_to_entry` (1490 − 555.9 = 934.1) |
| lane state word | **new** uncollapsed per-gate position (§2.1) — not `filament_pos_per_gate` |
| loaded lane | `ace.current_index` (`-1` = none) |
| shared-run tip | `ace_hub_encoder.distance_mm` ÷ `save_variables.ace_cal_hub_to_entry` |
| hub dot | `filament_switch_sensor hub_detect.filament_detected` (+ `.reading`, analog) |
| entry / gears dots | `filament_switch_sensor toolhead_entry` / `toolhead_postgear` `.filament_detected` — **read as a truth table**, and draw entry=0/post=1 as a FAULT |
| melt-zone dot | `mmu.in_melt_zone`; "cold pull is legal" from `mmu.cold_unload_ok` |
| jam banner | `ace_buffer_watch.jam`, `.jam_tool` → button `ACE_BUFFER_DISARM` |
| operation in flight | `save_variables.ace_op`; `ace_swap_timing` |
| print state (gates mid-print actions) | `print_stats.state`, `idle_timeout.state` |
| hand-fed lanes | `mmu.manual_tools` (`[4,5]`) |
| Load / Park / Eject / Retract | `T{i}` · `ACE_LANE_PARK T={i}` · `ACE_LANE_EJECT T={i}` (+`FORCE=1` when orphaned) · `ACE_LANE_UNLOAD T={i}` |
| Audit / Recover / Clear op / Calibrate | `ACE_AUDIT` · `MMU_RECOVER GATE={i}` + `MMU_UNLOCK` · `ACE_CLEAR_OP` · `ACE_CALIBRATE_PATH T={i} FROM_PARK=1` |
| Assign… | Spoolman picker → `MMU_GATE_MAP GATE={i} SPOOLID={n}` |

### 4.4 KlipperScreen — proposed layout (800 × 480, landscape, one screen)

```
┌──────────────────────────────────────────────────────────────────────┐  480px
│ ACE 2 Pro   ● conn   29°C 22%RH   PATH CLEAR          [ ✕ STOP ALL ] │  48
├────────────┬────────────┬────────────┬────────────┬──────────────────┤
│    T0      │    T1      │ ▐  T2      │    T3      │   TOOLHEAD       │
│   empty    │  Unknown   │  Sunlu PLA │   empty    │                  │
│            │  at ACE    │ ◀ LOADED   │            │   entry    ○     │
│            │            │  884/934mm │            │   gears    ○     │
│            │            │            │            │   melt     ○     │
│ [  Load  ] │ [  Load  ] │ [  Park  ] │ [  Load  ] │                  │
│ [ Eject  ] │ [ Eject  ] │ [ Eject  ] │ [ Eject  ] │  cold pull OK    │
│ no filament│            │            │ no filament│                  │
├────────────┴────────────┴────────────┴────────────┴──────────────────┤
│  [ Audit ]   [ Recover ]   [ Clear op ]   [ Dryer ]   [ More ▸ ]     │  64
└──────────────────────────────────────────────────────────────────────┘
   160          160          160          160          160
```

Four lane tiles at 160 px is exactly what `acepro.py` already does in its landscape branch, and it
fits with 160 px left for the toolhead column. The bottom bar is five 160 px buttons.

**On this screen because you can only do it here:** you just swapped a spool (Load / Eject); you are
watching a swap go wrong (Stop all, Recover, Clear op); you need to know if a cold pull is safe
before you touch anything (the toolhead column).

**Deliberately not here:** material / colour / temperature editing (three nested sub-screens and a
keypad in `acepro.py` — desk work, and Spoolman on the web is a better source), endless-spool
configuration, RFID sync toggles, path calibration, spool assignment.

**`STOP ALL` is the one addition that is purely a safety control:** `ACE_STOP_FEED T=<all>` then
`ACE_DISABLE_FEED_ASSIST T=<all>`. Enabling feed assist on a free strand makes the ACE feed
continuously; the operator standing next to an unspooling lane needs one button, not three commands.

**Open question for QA — do not decide from this document.** `ACE_RAW_FEED` and
`FORCE_MOVE STEPPER=extruder` were the two most-typed commands in the window sampled, and both are
at-the-machine recovery actions. They are also precisely the commands that have ground filament on
this machine when the ACE and the extruder moved out of step. A "Manual jog" sub-screen would be the
obvious `More ▸` entry. **Whether exposing it as a touch control is protective friction being
removed, or a genuine gap, is a QA call, not a UX one.**

### 4.5 KlipperScreen — data sources

Same objects as §4.3, subscribed via KlipperScreen's existing Moonraker connection. The only
addition is that the lane tile's state word must come from the **uncollapsed** per-gate position
(§2.1), and lane presence from `ace_buffer_watch.inserted` with `.stale` treated as *unknown*, never
as *empty*.

### 4.6 Prompts on the touchscreen

Constraints measured from `ks_includes/widgets/prompts.py`:

| Constraint | Value |
|---|---|
| `prompt_text` lines rendered | **1** (the last) — see §2.2 |
| Buttons per `prompt_button_group` row | capped at 4 (`set_max_children_per_line(min(4, n))`) |
| `prompt_button` fields | max 3, `|`-separated; a 4th field silently drops the button |
| `prompt_footer_button` | rendered in the `Gtk.Dialog` action area — works correctly today |
| Dismiss | `Escape`/`BackSpace` or the ✕, both send `action:prompt_end` |
| Stray `prompt_text` with no preceding `prompt_begin` | silently discarded (`screen.py:917`) |

Rules for every ACE dialog, from those constraints:

1. The **last** `prompt_text` carries the decision.
2. Never more than 4 buttons in a group.
3. A button's gcode must be a **bare macro name** — a payload containing its own `MSG=` and a colon
   does nothing when pressed. (`ace_ui.cfg:182` already documents this; three buttons shipped with
   that exact bug in August 2026.)
4. Always emit `action:prompt_end` before `prompt_begin`, or the dialog never renders and the lines
   land in the console as raw text. (Also already documented, `ace_ui.cfg:8`.)
5. **Never ask a question the sensors answer.** The entry/post-gear pair plus the slot statuses
   determine the state; derive it and state the conclusion. A prompt exists to get a *decision*, not
   a *reading*.
6. When a choice is genuinely needed, offer **two clear options**, each saying what it will do —
   not an open-ended set.

---

## 5. Ranked work list

### Broken

| # | Item | Effort | Payoff |
|---|---|---|---|
| 1 | Publish an uncollapsed per-gate position; point `/ace/` and `ACE_LANES` at it (§2.1) | S | **Very high** — removes the most-hit lie, on every staged lane |
| 2 | Put the decision in the last `prompt_text` of every ACE dialog (§2.2) | S | **Very high** — every prompt, every time, at the machine |
| 3 | Patch `prompts.py` to accumulate text, and upstream it (§2.2) | S | High — but needs a restore mechanism |
| 4 | Fix `ace_lane_pos` — either write `parked` from `_ACE_LANE_STATE` or remove it from the seed (§2.3) | S | High |
| 5 | Add RESUME / CANCEL_PRINT buttons to `manager.py:2754` (§2.5) | XS | High when it fires |
| 6 | Draw entry=0 / post=1 as a fault in `/ace/` (§2.8) | XS | Medium — rare but always serious |
| 7 | Do **not** wire `bowden_progress`; draw the bowden from `ace_hub_encoder.distance_mm` (§2.4) | M | Medium |
| 8 | Fix `TR` → real unload macro, and the dead `ace_pro_control` lock, **before** installing `acepro.py` (§2.6) | S | Deferred |
| 9 | Stub or hide the 13 unimplemented `MMU_*` commands the Mainsail card sends (§2.9) | M | Medium — one wasted press each, and one of them already failed *silently* |
| 10 | Make `patch_mainsail.py` fail loudly; correct its docstring to say cron (§2.7) | XS | Low, but it is a lie in the source |
| 11 | Delete `ace_buffer_watch.lane_empty`; drop the unused `ace_hub_encoder` subscription or use it (§2.8) | XS | Low |

### Missing / awkward

| # | Item | Effort | Payoff |
|---|---|---|---|
| 1 | Buttons on `/ace/` at all — Load / Park / Eject / Retract per lane, state-gated, disabled-with-reason (§4.2) | L | **Very high** |
| 2 | `Load T{n}` in `ACE_LANES` — the commonest action, currently absent (§3.3) | XS | **Very high** |
| 3 | `ACE_AUDIT` button on both surfaces (§3.2 — typed 12×, named in 5 error messages) | XS | Very high |
| 4 | `_ACE_SUPPRESSION_DISARM` → a named `Clear suppression` button; stop hiding it (§3.2) | XS | High |
| 5 | `Clear jam on T{n}` button inside the jam dialog that currently prints the command name (§3.1) | XS | High — fires at the worst moment |
| 6 | `Recover` / `Clear op` — `MMU_UNLOCK`, `MMU_RECOVER GATE=n`, `ACE_CLEAR_OP` (§3.1) | S | High |
| 7 | `STOP ALL` on KlipperScreen (§4.4) | S | High — safety, and only reachable at the machine |
| 8 | Dryer controls on `/ace/`; collapse the dryer card to one line (§3.3, §3.4) | S | Medium-high |
| 9 | `Eject (force)` where the state is orphaned — `ACE_LANE_EJECT T=n FORCE=1` (§3.1) | XS | Medium |
| 10 | Spool picker → `MMU_GATE_MAP GATE=n SPOOLID=n` (§3.3) | M | Medium |
| 11 | `Calibrate path` button; delete the "run ACE_CALIBRATE_PATH" sentence (§3.1) | XS | Medium |
| 12 | Install `acepro.py`, or replace it with the §4.4 layout (§1) | M | Medium — nothing on the touchscreen until then |
| 13 | Link `/ace/` from Mainsail (`patch_mainsail.py` already edits the bundle) (§3.3) | S | Medium |
| 14 | Draw gates 4–5 as a separate hand-fed strip from `mmu.manual_tools` (§4.2) | S | Low-medium |
| 15 | Manual-jog screen (`ACE_RAW_FEED`, `FORCE_MOVE`) — **QA decision, not UX** (§4.4) | M | Unknown by design |

---

## 6. Standing constraints worth remembering

- **Mainsail and Fluidd have no plugin API.** Two routes only: patch the compiled bundle, or serve a
  standalone page from the same origin. `/ace/` is the second, and it is the right one.
- **KlipperScreen shows only the last `prompt_text` of a dialog.** Design every prompt for that.
- **The underscore prefix hides a macro from the panel. It does not stop anyone needing it.** A
  hidden macro that nothing else calls is a public command in disguise.
- **Feed assist is motion.** Any control that enables it belongs with the motion controls and behind
  the same guards, not with the settings toggles.
- **A disabled control with a stated reason beats one that errors when pressed, and beats one that
  is not there.**
- **Never ask the operator for state the sensors already carry.** Derive it, say the conclusion, and
  only prompt for a genuine decision — with two clear options, each saying what it will do.
