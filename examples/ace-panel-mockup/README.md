# ACE panel — design mockup

A rendered proof of [`docs/12-panel-visual-design.md`](../../docs/12-panel-visual-design.md).
**Built 2026-08-31.**

## How to open it

Double-click `index.html`, or drag it into a browser. That is the whole procedure.

It is a **single self-contained file** — all CSS and JS inline, no CDN, no build step, no server. It
runs from `file://`, offline, and can be sent to a phone as one attachment.

Top-right: a **theme toggle**, so the light theme is provable rather than claimed. Top-centre: a
**state switcher** with the eleven states that decide behaviour.

## What you are looking at

Four artboards, all rendered from the same mock payload:

| # | Artboard | Size | What it is |
|---|---|---|---|
| 1 | **Dashboard card** | 600 × 400 | How the panel appears *in place*, as the Mainsail webcam-iframe card it is already registered as. Carries the **floor**: every way out of a fault is here. |
| 2 | **Full page** | 1240 wide | `/ace/#lane=N`. Same file, layout chosen by width. Per-lane destinations, jog, RFID, the derivation behind every number, the sensor truth table, the dryer in full. |
| 3 | **Phone portrait** | 390 × 844 | 48 px touch targets, sticky header and action bar. |
| 4 | **KlipperScreen** | 800 × 480, true size | Sufficiency, not depth. Everything reachable, nothing scrolls. |

Below them, the **mock payload** is printed in full so you can see exactly what each artboard was
rendered from.

**The doorway is live.** The `⟶` control on a card lane tile is the deep link — clicking it focuses
that lane on the page artboard and scrolls to it. In the real panel it is
`<a target="_blank" href="/ace/#lane=2">`, which from inside an iframe opens a real tab.

## What is real and what is mocked

**Real** — taken from the live machine at `10.49.9.130:7125` on 2026-08-31, and used verbatim:

- Every **field path**. `ace_instance_0.slots[i].color`, `save_variables.variables.ace_staged_mm`,
  `ace_buffer_watch.inserted`, `mmu.manual_tools`, and the rest. The mock object has the shape of a
  Moonraker `notify_status_update` payload, so wiring it to a websocket is a delete, not a rewrite.
- Every **gcode string**. Each control carries its command in a `data-gcode` attribute — inspect any
  button to read exactly what it would send. Nothing is sent; the one line that would send it is in
  the click handler, commented.
- Every **enabled-when predicate** and every **disabled reason**. Where a macro already refuses in its
  own words, the reason reuses that wording.
- The **default state**: T2 staged 884.1 mm of 934.1, auto-dry armed at 20 %RH holding against 23 %,
  `toolhead_entry` switched off, `ace_lane_pos = ['gate','parked','parked','gate']`.
- The **geometry**: 934.1 mm lane tube, 555.9 mm hub → entry, 18 mm post-gear offset. The ladder is
  drawn to true scale from those numbers.
- The **dryer presets**, read from `_ACE_DRY_PRESETS.table` exactly as the live macro publishes them.

**Mocked** — the transport and the passage of time:

- No websocket. State changes come from the switcher, not from the printer.
- No gcode is dispatched. Buttons log to the console instead.
- The **action rail**'s items are hand-written for the states that need them. In the real panel they
  are matched out of `notify_gcode_response` against a command manifest.
- Spoolman is not queried; `Assign spool…` opens nothing.
- Timestamps ("2m") are literals.

**Not represented**: hover and pressed states are CSS-live (try them), but press-and-hold jog,
in-flight sweeps and the prompt dialog are specified in the doc and not built here.

## The acceptance test

`acceptance.js` runs the mockup's **own** predicate and control logic — extracted from
`index.html`, not a copy — across all eleven states and asserts the acceptance criteria:

```
node acceptance.js
```

**1702 checks**, covering:

- every required control exists on every lane, either enabled or **disabled with a real reason**;
- every enabled control carries a non-empty gcode string;
- **`STOP ALL` is enabled in every state**, including disconnected, stale and faulted;
- the **jog interlock**: ACE-side and extruder-side jog are never both enabled;
- jog **never** emits `ACE_RAW_FEED` (untracked by the preload guard) and **never** `FORCE=1`;
- `Write tag` is never enabled, because this firmware has no write path;
- `stale` renders as **unknown, never empty**, and refuses motion;
- the **floor rule**: jam, orphaned lane, sensor fault, stuck suppression and dryer fault each have
  an enabled way out **on the card**.

It found a real bug on its first run: `Load` stayed enabled while an orphaned strand blocked the
toolhead, because with `current_index = -1` no lane-named reason applied. Fixed, and the test now
holds it.

## The render check

`acceptance.js` proves the control *logic*; `render-check.js` proves the *markup* that logic
produces.

```
node render-check.js
```

It renders all four artboards in all eleven states — 44 renders — and asserts balanced tags, no
stray `undefined` / `NaN` / `[object Object]`, and that **every element styled as a control carries a
target** (`data-gcode`, `data-lane`, `data-step` or `data-door`). That last rule is the mockup's
version of the standing no-dead-buttons rule: a button with nothing behind it cannot survive here
either.

It also earned its keep immediately, catching `title="undefined"` on every enabled Eject in the card
and touchscreen artboards.

## Known limits of the drawing

The ladder is drawn **to true scale** over 1590 mm, so `entry`, `gears` and `melt` genuinely cluster
at the right-hand end — they are within 100 mm of each other. The card groups them under one
`TOOLHEAD` label; the page labels all five. That clustering is the truth, and the alternative
(a piecewise scale) would be a picture that lies about geometry.
