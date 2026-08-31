# The ACE panel: visual design specification

**Written 2026-08-31.** Companion to [`11-operator-interface.md`](11-operator-interface.md), which is
the audit. That chapter establishes *what is broken and what is missing*, and its §0–§3 and §5 are
settled input here. This chapter is the *build specification*: tokens, components, layouts at real
pixel dimensions, and a traced control table.

**Why a separate chapter rather than an extension.** Chapter 11 is evidence — measured findings with
file:line citations, ranked. This is a contract — numbers two people can build the same screen from.
Mixing them would bury the specification inside 40 KB of audit, and the audit's findings must stay
citable on their own. Chapter 11 §4 remains the functional design; this supersedes it visually and
extends its data table.

A rendered proof of this specification lives at
[`examples/ace-panel-mockup/index.html`](../examples/ace-panel-mockup/) — a single self-contained
file with all three artboards, a theme toggle, and a state switcher.

### The organising question — re-weighted 2026-08-31, after the owner opened `/ace/`

> *"It's the same as the fake camera, and it's passive, so little value add to open it to only get
> the same view as the camera which I can see from the Klipper web UI."*

That is decisive, and it re-weights everything below. **A prettier read-only panel is not a
deliverable.** The nginx logs show `/ace/` has never been opened voluntarily, and the reason is now
on record: it duplicates a view he already has. The panel earns its existence only as a *control
surface*.

So the organising question for every pixel is not *what can I see here* but:

> **What can I click here that I currently have to type?**

The evidence pass counted **137 operator-facing messages naming a command with no button**, and
**none of those commands has ever been run from the machine**. Closing that deficit is the
deliverable. Three rules follow, and they override any layout instinct:

1. **Status earns pixels only where it is the direct basis for the next click.** A number that does
   not change what you press is scenery, and scenery is what the camera already gives him. The dryer
   prose is cut. The path diagram survives *because it is the thing you click to act on a lane*
   (§2.2); if it were only a picture it would shrink to a strip.
2. **Controls come first in the layout, not after the pretty part.** §3.1's block order puts the
   action rail and the lane tiles above the diagram, not below it.
3. **The web panel gets the depth; the touchscreen gets sufficiency.** He debugs at the desk. The
   touchscreen requirement is unchanged and non-negotiable — every action reachable, every prompt
   answerable — but it is sized for *completeness*, not richness. §3.3 is a sufficiency spec.

The single component that closes the 137-message deficit generically, rather than one button at a
time, is the **action rail** (§2.12). It is the most important thing in this document.

---

## 0. Live machine state this specification was measured against

Captured from `10.49.9.130:7125` on 2026-08-31. Every field path in §4 was verified present in this
payload; nothing here is invented.

```
ace.current_index                   = -1            (nothing loaded)
ace.target_index                    = -1
ace_instance_0.connection_state     = "connected"   firmware "V1.1.3W"
ace_instance_0.temp / .humidity     = 28 / 23
ace_instance_0.dryer_status.status  = "stop"
ace_instance_0.slots[2]             = ready, PLA, color [247,217,89], sku SM24, total 335g
ace_preload_guard.staged            = {"2": 884.1}      path_busy = false
ace_buffer_watch.inserted           = [false,true,true,false]   stale = false
ace_buffer_watch.lane_empty         = [false,false,false,false]  <-- dead, never draw
ace_hub_encoder.distance_mm         = 0.0    moving = false   mm_per_pulse = 0.9327
mmu.filament_pos_per_gate           = [0,0,0,0,0,0]     <-- collapsed, do not draw as state
mmu.filament_position_per_gate      = [0.0,0.0,884.1,0.0]
mmu.gate_status                     = [0,1,1,0,1,1]
mmu.manual_tools                    = [4,5]
save_variables.ace_staged_mm        = [0.0,0.0,884.1,0.0]
save_variables.ace_lane_pos         = ["gate","parked","parked","gate"]
save_variables.ace_cal_hub_to_entry = 555.9
save_variables.ace_park_to_entry    = 1490.0     -> lane tube 934.1
hub_detect.filament_detected        = false     reading 4.8
toolhead_entry.filament_detected    = false     enabled = FALSE
toolhead_postgear.filament_detected = false     enabled = true
print_stats.state                   = "standby"     idle_timeout.state = "Idle"
mmu_machine.unit_0                  = "ACE 2 Pro" / Anycubic / V1.1.3W / 6 gates
```

### 0.1 Three facts this capture adds to chapter 11

**A usable uncollapsed staged array already exists.** §2.1 recommends publishing a new uncollapsed
per-gate position from the shim. That is still the right fix for the *macros*, but the panel does not
have to wait for it: `save_variables.variables.ace_staged_mm` is live right now and reads
`[0.0, 0.0, 884.1, 0.0]` — the true value, uncollapsed, already persisted. **The panel should read
`ace_staged_mm` today and stop reading `filament_pos_per_gate` for state entirely.** That converts
§5-Broken-#1 from a shim change plus a panel change into a panel change alone, and it is consistent
with the standing rule that a UI reads `save_variables`, never the macro variable.

**`toolhead_entry` is currently `enabled: false`.** A disabled switch reports
`filament_detected: false` — which is indistinguishable, in every panel today, from *no filament*.
That is a picture that lies, of exactly the kind §2.8 catalogues, and it is live right now. **A
sensor with `enabled == false` must render as `sensor off`, never as a healthy empty reading**, and
any predicate that consumes it must degrade to *unknown* rather than to *false*. This is a new
component state, specified in §2.

**The two per-gate arrays have different lengths.** `filament_pos_per_gate` is 6 long (gates 0–5);
`filament_position_per_gate` is 4 long (ACE lanes only). A loop that walks `num_gates = 6` and
indexes the mm array runs off the end at gates 4 and 5 and gets `undefined`, which `Number()` turns
into `NaN` and a naive template prints as `NaNmm`. **Iterate ACE lanes over `0..3` and hand-fed gates
over `mmu.manual_tools` — never over `num_gates`.**

---

## 1. The visual language

### 1.1 What it is deliberately not

The owner's word for the stock aesthetic is *"too Klipper-like"*: engineering-focused, utilitarian,
unpolished. Named precisely, the thing to avoid is that Mainsail and Fluidd are **Material component
sets with a printer poured into them** — Vuetify cards, elevation shadows, filled buttons in a brand
primary, dense unlabelled icon rows, and colour used decoratively rather than semantically. Every
value looks like every other value.

This design goes the other way: an **instrument panel**.

1. **Chrome is desaturated to near-neutral.** The only saturated colour on the page is *filament
   colour*, which is data. Nothing competes with it.
2. **Semantic colour is rationed** to six roles and is always redundant with shape or position, so
   the panel survives being read by someone colour-blind, and survives being read at a glance.
3. **Hairline dividers, not card shadows.** Depth comes from a four-step surface ramp plus a 1 px
   border, not from elevation. Dark themes lit by shadow read as muddy; this one reads as etched.
4. **Caps micro-labels over large tabular numerals.** A measurement is the biggest thing in its
   block, its label the smallest. This is how a gauge is laid out and it is not how Material is.
5. **Nothing that asserts a position is animated** (§1.6). This is the rule that separates it from
   every other MMU front-end, and it is a correctness rule before it is a style one.

### 1.2 Colour tokens

Dark is the default — it is the printing context, and the owner keeps the UI open while printing.
Light is a full sibling theme, not an afterthought: every pair below is measured in both.

Theme selection, in this order: `[data-theme]` on `:root` wins; otherwise `prefers-color-scheme`;
otherwise dark. **Every colour is defined on bare `:root` first** so that a token can never exist
only inside a media query.

```css
:root {
  /* ground */
  --bg:          #0E1116;   /* page */
  --surface-1:   #151A21;   /* card */
  --surface-2:   #1C222B;   /* control rest, inset well */
  --surface-3:   #242C37;   /* control hover */
  --divider:     #2A333F;   /* decorative hairline INSIDE a card */
  --border:      #677689;   /* control boundary — the WCAG 1.4.11 one */
  --focus:       #5EC8D8;   /* focus ring */
  /* ink */
  --text:        #E7ECF3;
  --text-dim:    #A6B2C4;
  --text-faint:  #8B93A1;
  /* semantic */
  --ok:          #4ED88F;   /* loaded, healthy, present */
  --staged:      #F5B942;   /* staged mid-bowden, in flight, needs attention not alarm */
  --busy:        #7FA9F5;   /* path busy / operation in flight — informational, not a fault */
  --fault:       #FF6B6B;   /* impossible sensor state, jam latched, disconnected */
  --unknown:     #98A3B5;   /* stale, sensor disabled, indeterminate */
  --destructive: #FF6B6B;   /* eject and friends — same hue as fault, by intent */
  --accent:      #5EC8D8;   /* the one non-semantic accent: links, focus, active tab */
}
```

Light theme redefines exactly these tokens and nothing else:

```css
:root[data-theme="light"], :root:not([data-theme="dark"]) {  /* under prefers-color-scheme: light */
  --bg: #EEF1F6;  --surface-1: #FFFFFF;  --surface-2: #F4F7FB;  --surface-3: #E7ECF3;
  --divider: #D3DBE6;  --border: #7B899B;  --focus: #0B6E7C;
  --text: #141A22;  --text-dim: #4C5A6C;  --text-faint: #616B79;
  --ok: #0E7A4A;  --staged: #8A5600;  --busy: #2B5FC9;  --fault: #B92626;
  --unknown: #5E6B7D;  --destructive: #B92626;  --accent: #0B6E7C;
}
```

> **Forward compatibility.** No component may reference a theme by string. Every rule reads a token.
> A third theme (a high-contrast shop-floor variant, say) is added by defining the same 17 names
> under a new `[data-theme]` value; no component changes. The mockup proves this by carrying its
> whole palette in one block per theme.

#### Measured contrast — dark

Computed with the WCAG 2.1 relative-luminance formula. Body text requires 4.5:1; large text and UI
boundaries require 3:1.

| ink | hex | on `--bg` | on `--surface-1` | on `--surface-2` | on `--surface-3` | worst | AA body |
|---|---|---|---|---|---|---|---|
| `--text` | `#E7ECF3` | 15.93 | 14.72 | 13.47 | 11.87 | **11.87** | pass |
| `--text-dim` | `#A6B2C4` | 8.81 | 8.14 | 7.45 | 6.57 | **6.57** | pass |
| `--text-faint` | `#8B93A1` | 6.11 | 5.65 | 5.17 | 4.55 | **4.55** | pass |
| `--ok` | `#4ED88F` | 10.39 | 9.61 | 8.79 | 7.74 | **7.74** | pass |
| `--staged` | `#F5B942` | 10.72 | 9.90 | 9.06 | 7.98 | **7.98** | pass |
| `--busy` | `#7FA9F5` | 8.01 | 7.40 | 6.78 | 5.97 | **5.97** | pass |
| `--fault` | `#FF6B6B` | 6.81 | 6.30 | 5.76 | 5.08 | **5.08** | pass |
| `--unknown` | `#98A3B5` | 7.42 | 6.86 | 6.28 | 5.53 | **5.53** | pass |
| `--accent` | `#5EC8D8` | 9.66 | 8.93 | 8.17 | 7.20 | **7.20** | pass |

| boundary | hex | on `--bg` | on `--surface-1` | on `--surface-2` | on `--surface-3` | worst | AA UI (3:1) |
|---|---|---|---|---|---|---|---|
| `--border` | `#677689` | 4.08 | 3.77 | 3.45 | 3.04 | **3.04** | pass |
| `--focus` | `#5EC8D8` | 9.66 | 8.93 | 8.17 | 7.20 | **7.20** | pass |
| `--divider` | `#2A333F` | 1.48 | 1.37 | 1.25 | 1.10 | **1.10** | n/a — decorative |

#### Measured contrast — light

| ink | hex | on `--bg` | on `--surface-1` | on `--surface-2` | on `--surface-3` | worst | AA body |
|---|---|---|---|---|---|---|---|
| `--text` | `#141A22` | 15.45 | 17.49 | 16.28 | 14.73 | **14.73** | pass |
| `--text-dim` | `#4C5A6C` | 6.21 | 7.03 | 6.54 | 5.92 | **5.92** | pass |
| `--text-faint` | `#616B79` | 4.77 | 5.40 | 5.03 | 4.55 | **4.55** | pass |
| `--ok` | `#0E7A4A` | 4.75 | 5.38 | 5.01 | 4.53 | **4.53** | pass |
| `--staged` | `#8A5600` | 5.44 | 6.16 | 5.73 | 5.19 | **5.19** | pass |
| `--busy` | `#2B5FC9` | 5.18 | 5.86 | 5.45 | 4.94 | **4.94** | pass |
| `--fault` | `#B92626` | 5.51 | 6.23 | 5.80 | 5.25 | **5.25** | pass |
| `--unknown` | `#5E6B7D` | 4.79 | 5.42 | 5.04 | 4.57 | **4.57** | pass |
| `--accent` | `#0B6E7C` | 5.25 | 5.94 | 5.53 | 5.00 | **5.00** | pass |

| boundary | hex | on `--bg` | on `--surface-1` | on `--surface-2` | on `--surface-3` | worst | AA UI (3:1) |
|---|---|---|---|---|---|---|---|
| `--border` | `#7B899B` | 3.15 | 3.56 | 3.32 | 3.00 | **3.00** | pass |
| `--focus` | `#0B6E7C` | 5.25 | 5.94 | 5.53 | 5.00 | **5.00** | pass |
| `--divider` | `#D3DBE6` | 1.23 | 1.40 | 1.30 | 1.18 | **1.18** | n/a — decorative |

Nothing fails. The generator that produced both tables is committed alongside the mockup so the
published numbers cannot drift from the CSS.

**The two `--divider` rows are the only sub-3:1 pairs in the system, and they are deliberate.** WCAG
1.4.11 applies to boundaries *required to identify a component*. `--divider` never identifies
anything — it is a hairline rule inside an already-bordered card, and removing it loses no
information. Every boundary that does carry meaning uses `--border`. Stating this explicitly so
nobody "fixes" `--divider` into a heavy line and wrecks the density.

#### Semantic colour is never the only channel

| role | colour | redundant channel |
|---|---|---|
| loaded | `--ok` | filled dot + the word `LOADED` in the tile header |
| staged | `--staged` | partial fill bar with a numeric `884 / 934 mm` |
| empty | `--text-faint` | dashed tile border, em-dash in place of a value |
| stale / unknown | `--unknown` | `?` glyph in the dot, the literal word `unknown`, 45° hatch fill |
| fault | `--fault` | banner with a heading, plus the offending reading spelled out |
| busy | `--busy` | 2 px indeterminate rule under the header, plus the owning lane named |

### 1.3 Filament colour is data, not theme

`ace_instance_0.slots[i].color` is arbitrary RGB read off the spool. It can be `#FFFFFF`, it can be
`#101010`, it can be within a few points of either theme's card colour. Four rules:

1. **`[0,0,0]` means absent, not black.** Already handled correctly in `ace_index.html:rgb()` — keep
   it. An absent swatch renders as a dashed `--border` ring over `--surface-2`, with no fill.
2. **The swatch always carries a boundary, and the boundary is never the filament colour.** A 1 px
   inset `rgba(0,0,0,.35)` (dark) / `rgba(255,255,255,.55)` (light) hairline, plus a 1 px outer ring
   in `--border`. That outer ring is the 3:1 boundary, so a `#151A21` spool on a `#151A21` card is
   still a visible object. Measured: `--border` clears 3:1 against all four surfaces in both themes.
3. **Never put text on a swatch on the web.** Labels sit adjacent, on a surface token, so their
   contrast is the measured table above and not a function of what spool is loaded. This eliminates
   the whole class of failure rather than mitigating it.
4. **Where text on colour is unavoidable** — the KlipperScreen lane tile's `T2` chip, which is large
   text and therefore governed by 3:1 — pick the ink by max contrast, not by a hue guess:

   ```js
   // relative luminance; the crossover where black and white contrast equally is L = 0.1791
   const ink = relLum(rgb) > 0.1791 ? '#0B0E12' : '#F5F8FC';
   ```

   and then **verify**: if `contrast(rgb, ink) < 3.0` the chip does not tint at all — it falls back
   to `--surface-2` with a 3 px left rule in the filament colour. A mid-grey spool (`#808080`,
   best-case ink contrast 4.90) passes; a colour that does not, degrades instead of lying.

   Everywhere else the filament colour appears as a **fill or a rule, never as a text background**:
   the lane-tile left rule (3 px), the path diagram's tube stroke, and the swatch.

### 1.4 Type

No webfonts. The mockup is a single file that must run from `file://` with no CDN, and a font that
fails to load is a layout that shifts. Two stacks:

```css
--font-ui:   ui-sans-serif, -apple-system, "Segoe UI Variable Text", "Segoe UI",
             Roboto, "Helvetica Neue", Arial, sans-serif;
--font-num:  ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono",
             "DejaVu Sans Mono", Consolas, monospace;
```

Every numeric run — mm, °C, %RH, g, gate indices, times — carries
`font-variant-numeric: tabular-nums` so a changing value does not reflow its neighbours. This is the
single highest-value typographic decision on a live-updating panel and stock Mainsail does not make
it.

| level | size / line-height | weight | tracking | colour | used for |
|---|---|---|---|---|---|
| `measure-xl` | 30 / 32 px | 600 | −0.4 px | `--text` | the one number a card exists for: `884` mm, `28` °C |
| `measure` | 20 / 24 px | 600 | −0.2 px | `--text` | secondary numerics inside a tile |
| `title` | 17 / 22 px | 600 | +0.2 px | `--text` | page title, dialog heading |
| `tile-head` | 15 / 20 px | 650 | 0 | `--text` | `T2 · Bambu Lab Yellow PLA` |
| `body` | 14 / 20 px | 400 | 0 | `--text` | prose, control labels |
| `body-strong` | 14 / 20 px | 600 | 0 | `--text` | primary button label |
| `meta` | 12.5 / 17 px | 400 | 0 | `--text-dim` | `spool 24 · 335 g` |
| `reason` | 12.5 / 17 px | 400 | 0 | `--text-faint` | the disabled-because line |
| `micro` | 10 / 12 px | 650 | +0.14 em, uppercase | `--text-faint` | section labels: `PATH`, `LANES`, `DRYER` |
| `unit` | 12 / 12 px | 500 | 0 | `--text-dim` | the `mm` after a `measure-xl` |

**12.5 px is the floor for any text that carries meaning.** On the phone artboard `meta` and `reason`
step up to 13.5 px; `micro` stays 10 px because it is a label whose content is also encoded by
position. Nothing below 10 px exists in the system.

### 1.5 Space, radius, border, elevation

```css
--sp-1: 2px;  --sp-2: 4px;  --sp-3: 6px;  --sp-4: 8px;  --sp-5: 12px;
--sp-6: 16px; --sp-7: 20px; --sp-8: 24px; --sp-9: 32px; --sp-10: 40px;

--r-chip: 6px;    /* swatch, badge */
--r-ctl:  10px;   /* button, input */
--r-card: 14px;   /* card, tile */
--r-pill: 999px;  /* status pill */
```

Everything lands on a **4 px grid**; the 2 px and 6 px steps exist only for optical corrections
(icon nudges, hairline offsets).

- **Card:** `background --surface-1`, `border 1px solid --divider`, `radius --r-card`,
  `padding --sp-6`.
- **Control:** `background --surface-2`, `border 1px solid --border`, `radius --r-ctl`.
- **Elevation, dark:** none. No `box-shadow` anywhere. Depth is the surface ramp.
- **Elevation, light:** exactly one shadow, on cards only —
  `0 1px 2px rgba(20,26,34,.05), 0 6px 18px rgba(20,26,34,.05)`. Light surfaces need a little
  separation because the surface ramp is compressed at the top end; dark does not.
- **Focus:** `outline: 2px solid --focus; outline-offset: 2px`. Never removed, never replaced by a
  colour change alone. Applied via `:focus-visible`.
- **Nesting rule:** a bordered thing never sits directly inside another bordered thing sharing an
  edge. Controls inside a card are separated from the card border by at least `--sp-5`.

### 1.6 Motion

| what | duration | easing | notes |
|---|---|---|---|
| hover / press — background, border, colour | 110 ms | `ease-out` | the only routine transition |
| button pressed | 60 ms | `ease-out` | translateY(1px), no scale |
| disclosure (tile expand, dialog in) | 180 ms | `cubic-bezier(.2,.7,.3,1)` | height + opacity |
| dialog scrim | 140 ms | `ease-out` | opacity only |
| theme change | 200 ms | `ease-out` | `background-color`, `color`, `border-color` only |
| in-flight control | 1.6 s loop | `linear` | a 2 px indeterminate rule along the button's bottom edge |
| jam banner arrival | 240 ms | `cubic-bezier(.2,.7,.3,1)` | slide + fade, **once**; it never pulses |

**Explicitly not animated, and this is a correctness rule, not a taste one:**

- the filament fill length in the path diagram;
- the staged-mm figure, the encoder distance, temperature, humidity;
- the sensor dots;
- the lane fill bar.

The reason is §2.4 of chapter 11. A tweened position claims the filament passed through every
intermediate value. Status arrives as discrete snapshots; the values in between were never measured.
Tweening them invents motion that did not happen, and a picture that lies is worse than no picture,
because it is believed. **The path redraws instantly at each `notify_status_update`.** The only thing
permitted to move is a *marker* driven by `ace_hub_encoder.moving == true` — a small chevron that
pulses to say "the encoder is turning right now", which is a fact, and which sits beside the tube
rather than being the tube.

`@media (prefers-reduced-motion: reduce)` sets every duration above to `1ms` except the two opacity
fades, and stops the in-flight rule (it becomes a static `--busy` bar).

### 1.7 The one-line summary

> Klipper front-ends are Material component sets with a printer poured into them. This is an
> instrument panel: chrome desaturated to near-neutral so the only saturated colour on screen is
> filament colour, semantic colour rationed to six roles and never the sole channel, hairline etching
> instead of elevation shadows, 10 px caps micro-labels beneath 30 px tabular numerals — and nothing
> that asserts a position is ever animated.

---

## 2. Component inventory

Every component below is specified in seven states. Where a state is *impossible* for a component
that is said, rather than left blank — a blank is indistinguishable from an omission.

The seven: **default · hover · pressed · disabled-with-reason · in-flight · fault · stale/unknown.**

Two system-wide rules govern the last two:

- **`ace_buffer_watch.stale == true` collapses every field derived from it to *unknown*, never to
  *empty*.** Unknown renders as `--unknown` ink, a `?` in place of the dot, a 45° hatch instead of a
  fill, and the literal word `unknown`. The panel says it does not know; it does not guess *empty*,
  which is the guess that gets a lane ejected.
- **A `filament_switch_sensor` with `enabled == false` is *unknown*, not *false*.** Live today for
  `toolhead_entry` (§0.1). Rendered as a hollow dot with a diagonal slash and the label `sensor off`.

### 2.1 Lane tile

Desktop 283 × 268 px. The only component that owns a filament colour.

```
 ┌─┬───────────────────────────────────────────┐   ← 1px --divider, radius 14
 │▌│  ▪ T2                            [ STAGED ]│   ← 3px filament rule (left, full height)
 │▌│  Bambu Lab Yellow PLA                      │   ← tile-head 15px, ellipsis at 1 line
 │▌│  PLA · spool 24 · 335 g                    │   ← meta 12.5px --text-dim
 │▌│                                            │
 │▌│  STAGED                                    │   ← micro 10px caps
 │▌│  884 / 934 mm                              │   ← measure-xl 30px + unit 12px
 │▌│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░  94.6 %   │   ← 6px bar, radius 3
 │▌│                                            │
 │▌│  ┌──────────┐  ┌──────────┐                │
 │▌│  │  Park    │  │  Retract │                │   ← 40px tall, --sp-4 gap
 │▌│  └──────────┘  └──────────┘                │
 │▌│  ┌──────────┐  ┌──────────┐                │
 │▌│  │  Load    │  │  Eject   │                │   ← Eject is --destructive
 │▌│  └──────────┘  └──────────┘                │
 │▌│  T2 already owns the path                  │   ← reason 12.5px --text-faint
 └─┴───────────────────────────────────────────┘
```

Anatomy, top to bottom: `--sp-5` padding all round, plus `--sp-5` extra on the left for the rule.
Swatch 20 × 20 `--r-chip`. Header row 22 px. Name 20 px. Meta 17 px. Gap `--sp-6`. Micro 12 px.
Measure 32 px. Bar 6 px + `--sp-3`. Gap `--sp-6`. Two button rows 40 px each + `--sp-4` gap. Reason
17 px.

| state | left rule | header badge | measure block | buttons | reason line |
|---|---|---|---|---|---|
| **empty** (`inserted[i] == false`, not stale) | `--divider`, and the tile border becomes 1 px **dashed** `--divider` | `EMPTY` in `--text-faint` on `--surface-2` | `—` in `--text-faint`, no bar | Load/Park/Retract disabled; Eject disabled | `no filament in lane 0` |
| **present, at gate** (`inserted`, `ace_staged_mm[i] == 0`) | filament colour | `AT GATE` in `--text-dim` | `0 / 934 mm`, empty bar | Load enabled, Eject enabled, Park/Retract disabled | (none) |
| **staged** (`ace_staged_mm[i] > 0`, not the path owner) | filament colour | `STAGED` in `--staged` | mm + fill in filament colour | Retract enabled, Eject enabled, Load enabled | (none) |
| **loaded** (`ace.current_index == i`) | filament colour, **4 px** not 3 | `LOADED` in `--ok`, filled dot | mm + full bar | Park enabled, Eject enabled; Load disabled | `T2 is already loaded` |
| **stale / unknown** (`ace_buffer_watch.stale`) | `--unknown` | `UNKNOWN` in `--unknown`, `?` dot | `unknown` word, bar becomes 45° hatch in `--unknown` | **all four disabled** | `lane sensors last read 41 s ago — state unknown` |
| **fault** (jam latched on this tool, or an orphaned state) | `--fault`, 4 px | `JAM` / `ORPHANED` in `--fault` | last known mm, greyed to `--text-faint` | only `Eject (force)` and `Clear jam` enabled | the fault sentence (§2.6) |
| **hover** (tile) | unchanged | unchanged | unchanged | — | tile background `--surface-1` → mix with `--surface-2` at 40 % |
| **in-flight** (this lane is `ace_preload_guard` path owner and an op is running) | filament colour | `MOVING` in `--busy` + 2 px indeterminate rule under the header | mm updates, no tween | all disabled | `T2 · retracting — 884 mm` |

Notes that are easy to get wrong:

- The percentage beside the bar is `ace_staged_mm[i] / laneTube` where `laneTube` is
  `ace_cal_park_to_hub[i]` if non-zero, else `ace_park_to_entry − ace_cal_hub_to_entry` (= 934.1
  today, because `ace_cal_park_to_hub` is `[0,0,0,0]`). **If neither is available the bar is not
  drawn and the tile shows the raw mm only** — an unscaled bar is a lie about geometry.
- Buttons keep their **position** in every state. A control that moves between states cannot be
  learned. Only its enablement and label change.
- `Retract` and `Park` occupy the same slot as each other across states because they are the same
  gesture at two scales; `Load` and `Eject` are fixed.

### 2.2 Shared-path diagram

One SVG, `viewBox="0 0 900 300"`, rendered into a 12-column card. It is the component most able to
lie, so it has the strictest rules.

```
  T0  ○──────────────────────────╮
  T1  ●──────────────╌╌╌╌╌╌╌╌╌╌╌╌┤
  T2  ●━━━━━━━━━━━━━━━━━━━━━━━━╸ ┤━━━━━━━━━━━●─────────○────────○
  T3  ○──────────────────────────╯  hub    entry    gears    melt
                                    ▲ 555.9 mm measured
```

| element | geometry | source | fault / unknown rendering |
|---|---|---|---|
| lane tube | horizontal run 56 → 340 px, then a quadratic into the hub at (400, 150) | static | — |
| lane fill | filament colour, `stroke-width 8`, length = `ace_staged_mm[i] / laneTube` of the run | `save_variables.ace_staged_mm` | hatch pattern in `--unknown` when `stale` |
| lane end cap | 6 px dot at the fill end | — | `?` glyph when unknown |
| shared run | 400 → 860 px, `--surface-3`, `stroke-width 10` | static | — |
| shared fill | from the hub to the tip, in the **owning lane's** colour | owner = index of the lane where `ace_staged_mm[i] >= laneTube`, else `ace.current_index`; **never** `filament_pos_per_gate.findIndex(p => p >= 2)` (that is the §2.1 bug — it returns −1 and indexes `slots[-1]`) | `--unknown` when no owner can be determined but a sensor is lit |
| hub dot | (400, 150) | `filament_switch_sensor hub_detect.filament_detected`; analog `.reading` in the tooltip | slashed when `.enabled == false` |
| entry dot | (686, 150) | `toolhead_entry` | **slashed today** — `enabled: false` |
| gears dot | (778, 150) | `toolhead_postgear` | — |
| melt dot | (860, 150) | `mmu.in_melt_zone` | — |
| encoder marker | small chevron above the shared run at the hub | `ace_hub_encoder.moving` — the **only** animated element in the diagram | absent when false |

**The truth-table rule.** `entry` and `postgear` are read as a pair, not independently:

| entry | postgear | render |
|---|---|---|
| 0 | 0 | nothing at the toolhead — normal |
| 1 | 0 | tip in the gears, short of postgear — normal |
| 1 | 1 | through the gears — normal |
| **0** | **1** | **FAULT.** Impossible on a continuous strand. Banner: *"Broken strand or a dead switch: post-gear sees filament but entry does not."* Both dots go `--fault`; **no fill is drawn on the shared run**, because the panel cannot say where the filament is. |

Chapter 11 §2.8 flags that `ace_index.html:302-306` tests `postOn` before `entryOn` with no
cross-check and draws that row as healthy. This table is the fix.

**When calibration is missing** (`ace_cal_hub_to_entry == 0`) the diagram does not draw to scale and
does not pretend to: the tubes render as equal-length dashed rules, the caption reads *"lane lengths
are not to scale — path is not calibrated"*, and a **`Calibrate path` button sits in the caption**,
replacing today's `ace_index.html:319` sentence that tells the operator to type
`ACE_CALIBRATE_PATH` on a page with no way to run it.

### 2.3 Status pill

Height 22 px, `--r-pill`, `padding 0 10px`, `micro` type, 1 px border. Optional 6 px leading dot.

| variant | fill | border | ink | dot | used for |
|---|---|---|---|---|---|
| neutral | `--surface-2` | `--border` | `--text-dim` | none | `fw V1.1.3W` |
| ok | `--surface-2` | `--ok` at 55 % | `--ok` | filled `--ok` | `connected`, `path clear` |
| staged | `--surface-2` | `--staged` at 55 % | `--staged` | filled | `staged` |
| busy | `--surface-2` | `--busy` at 55 % | `--busy` | filled + 1.6 s indeterminate rule | `path busy · T2` |
| fault | `--surface-2` | `--fault` | `--fault` | filled | `disconnected`, `jam` |
| unknown | `--surface-2` | `--border` dashed | `--unknown` | `?` glyph | `stale` |

Hover/pressed/in-flight/disabled do not apply — a pill is never interactive. Said explicitly so
nobody makes one clickable and inherits no pressed state.

### 2.4 Action button

Three ranks. Heights: 36 px (bar), 40 px (in a lane tile), 48 px (phone), 52 px (KlipperScreen).
Minimum hit area 44 × 44 px on the web, **48 × 48 px on KlipperScreen** (§3.3).

| rank | rest | hover | pressed | focus |
|---|---|---|---|---|
| **secondary** (default) | `--surface-2` / border `--border` / ink `--text` | bg `--surface-3`, border `--focus` at 50 % | bg `--surface-2`, `translateY(1px)` | 2 px `--focus`, offset 2 |
| **primary** | `--surface-2`, border `--accent`, ink `--accent`, 2 px left rule `--accent` | bg `--surface-3` | as above | as above |
| **destructive** | `--surface-2`, border `--destructive` at 60 %, ink `--destructive` | bg = `--destructive` at 12 % over `--surface-2`, border full `--destructive` | as above | as above |

Destructive buttons are **never adjacent to their safe counterpart without a separator**: in the lane
tile `Eject` sits on the second row, diagonal from `Park`. Chapter 11 §4.2 makes this point in prose;
this is the geometry that enforces it.

**Disabled-with-reason** is the state that matters most, and it is a *pair*: the control plus a
`reason` line. Never a tooltip alone — the touchscreen has no hover, and the phone has no hover.

```
┌──────────┐
│  Load    │   opacity 1.0 (NOT dimmed to invisibility)
└──────────┘   bg --surface-1, border 1px dashed --border, ink --text-faint
 no filament in lane 0        ← reason, 12.5px --text-faint, directly beneath
```

- `aria-disabled="true"` and `tabindex="-1"`, but the element stays in the DOM and stays **readable**:
  ink is `--text-faint`, which clears 4.5:1 on every surface. The stock pattern of `opacity: .35` —
  which `ace_index.html` uses today — takes `--text` down to roughly 3.6:1 on `--surface-1` and fails
  AA. **Disabled is a dashed border and a faint ink, not an opacity.**
- The reason string is **verbatim from §4.2's control table**, not generated. It names the state, not
  the rule: *"no filament in lane 0"*, not *"predicate failed"*.
- The gcode string is still present in `data-gcode`, so the control is inspectable and one line from
  being wired.

**In-flight**: label is replaced by the present participle (`Loading…`), a 2 px `--busy` indeterminate
rule runs along the bottom edge, and the button is `aria-disabled`. Every *other* motion control on
the page simultaneously enters disabled-with-reason carrying `path busy — T2 is loading`.

**Fault**: a button never renders "fault". The banner does. A button in a fault state is either
disabled-with-reason or, for the recovery controls, plain enabled — deliberately, because those are
the ones you need.

### 2.5 Fault banner

Full width of its container, `--r-card`, `background: --fault at 10 % over --surface-1`, 1 px border
`--fault`, 3 px left rule `--fault`, `padding --sp-5 --sp-6`.

```
┌─┬─────────────────────────────────────────────────────────────────────┐
│▌│ SENSOR FAULT                                                        │  micro, --fault
│▌│ Post-gear sees filament but entry does not.                         │  body-strong
│▌│ That is impossible on a continuous strand: broken filament, or one  │  body, --text-dim
│▌│ of the two switches has failed. entry=0 · post-gear=1 · hub=0        │  meta, --font-num
│▌│  ┌────────────┐ ┌──────────────────────┐ ┌───────────────────────┐  │
│▌│  │   Audit    │ │ Query both sensors   │ │ Clear op              │  │
│▌│  └────────────┘ └──────────────────────┘ └───────────────────────┘  │
└─┴─────────────────────────────────────────────────────────────────────┘
```

Rules: a heading, a plain-language sentence, **the readings that produced it**, and at least one
button. A banner that states a fault and offers nothing is the failure chapter 11 §3.1 catalogues 23
times. The banner never pulses and never auto-dismisses; it clears when the condition clears.

States: default (as drawn) · hover/pressed (n/a, the container is not interactive) ·
disabled (n/a) · in-flight (its buttons carry the button in-flight state) ·
**stale** (border becomes dashed `--unknown`, heading becomes `SENSOR FAULT — READINGS STALE`, and
the buttons that would move filament go disabled-with-reason).

### 2.6 Jam banner

Same shell as the fault banner, distinguished by heading, by naming the tool, and by carrying the
one button chapter 11 §3.1 calls the purest example of the missing-button failure.

```
JAM LATCHED ON T2
The buffer stopped moving while T2 was feeding. Motion is blocked until it is cleared.
jam_tool=2 · last_event="offrest" · armed=false · off-rest 3.4 s
  [ Clear jam on T2 ]   [ Audit ]   [ Eject T2 (force) ]
```

`Clear jam on T2` → `ACE_BUFFER_DISARM`. Today `ace_ui.cfg:80` prints that command name as text
inside a dialog that is already open and already knows the tool number.

**Flagged for QA — do not decide from this document.** `ace_buffer_watch.jam` has **zero motion gates
anywhere in the config tree**; its only two consumers are the display lines at `ace_ui.cfg:79-80`.
Making the panel disable motion controls while a jam is latched would therefore be *new* friction
invented by the UI, not existing friction surfaced — and `ace_unload.cfg:41` records that buffer-watch
gating around unload was tried and removed on 2026-08-20 because a healthy extraction trips it.
**This design does not block on `jam`.** The banner appears above the lane tiles, the affected tile
takes the fault treatment, and the controls stay live — because the operator with a latched jam is
precisely the one who needs to move something. Whether a latched jam *should* block the destructive
controls is a QA call.

### 2.7 Dryer strip

Chapter 11 §3.4: the dryer currently occupies a full card, four large numbers and a paragraph of
prose, for a subsystem that is idle almost always and that the page cannot control. It collapses to
one 44 px strip inside the header card, with controls.

```
 DRYER   28 °C · 23 %RH · 5.6 g/m³ · idle          [ Dry 4 h ▾ ]  [ Auto-dry ]  [ Roll ▾ ]
```

- `28`, `23`, `5.6` in `measure` (20 px) with `unit` suffixes; `DRYER` in `micro`.
- Absolute humidity (Magnus, already implemented at `ace_index.html:143`) keeps its place because it
  is the honest number — but the paragraph explaining *why* moves into this document. The panel earns
  it with a `(i)` disclosure instead, opened on demand.
- **Running**: the strip gains a determinate progress rule using `remain_time / duration`, and the
  `Dry` button becomes `Stop drying` (destructive rank). `mode` reads e.g. `drying 45 °C · 2 h 14 m
  left`.
- **Unknown**: `ace_instance_0.temp == null` → `— °C`, controls disabled-with-reason
  `no reading from the ACE`.
- **Fault**: `connection_state != "connected"` → whole strip disabled, reason `ACE disconnected`.
- Dry-roll (`ACE_DRYROLL_*`) lives behind the `Roll ▾` split-button because it *moves filament* and
  therefore obeys the same path-busy and print-state predicates as the lane controls. It is not a
  settings toggle. Feed assist is motion; so is rolling.

### 2.8 Hand-fed strip (gates 4–5)

From `mmu.manual_tools == [4,5]`. **Must not look like an ACE lane**, per the standing constraint.
Full-width, 84 px, visually subordinate: `--surface-2` ground rather than `--surface-1`, no left
filament rule, a small `HAND-FED` micro-label, and **no ACE motion controls at all** — there is no
actuator to command.

```
 HAND-FED GATES                                     these lanes are loaded by hand
 ┌───────────────────────┐  ┌───────────────────────┐
 │ ▪ G4   present        │  │ ▪ G5   present        │      [ Assign spool… ]
 └───────────────────────┘  └───────────────────────┘
```

Per gate: `mmu.gate_status[i]` (1 = present), `mmu.gate_material[i]`, `mmu.gate_filament_name[i]`,
`mmu.gate_spool_id[i]`, `mmu.gate_color_rgb[i]`. **Do not index
`mmu.filament_position_per_gate` here** — it is 4 long (§0.1). The only control is `Assign spool…`,
because spool identity is the only thing software owns on a hand-fed gate.

States: present · absent (`gate_status == 0`, dashed) · unknown (`gate_status == -1` if it ever
appears) · fault (n/a) · in-flight (n/a) · hover/pressed (only on `Assign spool…`).

### 2.9 Spool picker

Opened by `Assign spool…` on any lane or hand-fed gate. A sheet (desktop: centred modal 520 px wide;
phone: bottom sheet, full width, 80 vh; KlipperScreen: **not present** — §6).

Data source is real and same-origin: `POST /server/spoolman/proxy` with
`{"request_method":"GET","path":"/v1/spool"}`, verified live 2026-08-31. Each row:

| element | source |
|---|---|
| swatch | `filament.color_hex` (null → dashed empty swatch, never black) |
| name | `filament.name` |
| vendor · material | `filament.vendor.name` · `filament.material` |
| remaining | `remaining_weight` g, in `--font-num` |
| current-lane marker | `mmu.gate_spool_id[i] == spool.id` |

Rows with `archived == true` are hidden. Search filters on name + vendor + material. Confirm sends
`MMU_GATE_MAP GATE={i} SPOOLID={id}`.

States: default · hover (row `--surface-2`) · pressed · **disabled-with-reason** (the whole sheet's
confirm, when `spoolman_connected == false`, reason `Spoolman is not reachable`) · in-flight (confirm
shows `Assigning…`) · fault (proxy returns an error → inline `--fault` row with the message and a
`Retry`) · **stale** (list older than 60 s while the sheet is open → a `--unknown` pill reading
`list may be out of date` plus `Refresh`).

### 2.10 Toolhead column

A vertical stack of four sensor rows plus a verdict. 160 px wide on KlipperScreen, a 3-column card on
desktop.

| row | source | states |
|---|---|---|
| hub | `filament_switch_sensor hub_detect` | on / off / **sensor off** (`.enabled == false`) — analog `.reading` shown in `--font-num` |
| entry | `toolhead_entry` | as above — **currently `sensor off`** |
| gears | `toolhead_postgear` | as above |
| melt | `mmu.in_melt_zone` | derived, no switch: labelled `derived` in `micro` so nobody mistakes it for a reading |
| verdict | `mmu.cold_unload_ok` | `cold pull is legal` in `--ok`, or `must heat first` in `--staged`, or `unknown` when any input sensor is disabled or stale |

A sensor row is 32 px: 10 px dot, label, then reading. The dot: filled `--ok` when true, hollow
`--border` when false, hollow with a 45° slash in `--unknown` when disabled, `--fault` filled when
the row participates in the impossible pair (§2.2).

**The verdict never renders as a bare `true`.** If any of its inputs is disabled or stale, it reads
`unknown — entry sensor is off`, because a wrong "cold pull is legal" is how filament gets ground.

### 2.11 Prompt dialog

Two renderings of the same `action:prompt_*` stream. The web version accumulates every `prompt_text`;
KlipperScreen keeps only the last (chapter 11 §2.2). Design rules in §6.

Web: modal 480 px, `--r-card`, scrim `rgba(6,8,12,.62)`, heading in `title`, body lines in `body`,
buttons in a right-aligned row wrapping at 3. KlipperScreen: `Gtk.Dialog`, one 26 px label line,
buttons at 4 per row, 52 px tall.

States: default · hover/pressed on its buttons · **disabled-with-reason** (a button whose predicate
has since gone false — e.g. `Resume` after the print was cancelled — greys with a reason line rather
than disappearing, so the dialog does not reflow under a moving thumb) · in-flight (the pressed
button shows the participle; the dialog stays open until `prompt_end`) · fault (a `--fault` strip
above the buttons carrying the error) · **stale** (the dialog was opened more than 5 minutes ago and
its underlying state has changed: a `--unknown` strip reads `state changed since this was
opened — re-check before acting`, and motion buttons go disabled-with-reason).

### 2.12 The action rail — the component that closes the 137-message deficit

Every one of those 137 messages is the machine having already worked out what should happen next and
then declining to offer it. Adding 137 buttons by hand is a year of work and a cluttered screen. The
rail solves it **generically**: it watches what the machine says, and turns any command named in that
text into a button.

**Mechanism.** Subscribe to Moonraker's `notify_gcode_response` and seed from
`GET /server/gcode_store?count=200`. For each message, match against a manifest of known commands and
emit a button per hit:

```js
// The manifest is the ~40 ACE/MMU commands, longest-first so ACE_LANE_UNLOAD_ABORT
// wins over ACE_LANE_UNLOAD. Arguments are lifted from the SAME message, so
// "run MMU_RECOVER GATE=2" produces a button that sends GATE=2, not a bare command.
const HIT = new RegExp("\\b(" + MANIFEST.join("|") + ")\\b((?:\\s+[A-Z_]+=[^\\s]+)*)");
```

Each rail item carries: **the button**, **the sentence that named it** (truncated to one line, full
text on hover/expand), **a timestamp**, and **the same enabled-when predicate the control table gives
that command** — so a rail button for `ACE_LANE_EJECT T=2` greys with the same reason as the tile
button, and the rail never becomes a back door around a guard.

**Rules that keep it from becoming noise:**

| rule | why |
|---|---|
| Dedupe by `command + args`; keep the newest, show a count badge | the same message fires five times during one bad swap |
| Cap at 6 items; oldest fall off | a rail longer than the screen is a log, and he has a console for logs |
| A command already offered by a visible enabled control is **not** added | no duplicate buttons for `Load`/`Eject`/`Audit` when the tile shows them |
| Items expire after 15 minutes, or immediately when their predicate becomes trivially satisfied (`Clear op` disappears once `ace_op` is empty) | a stale suggestion is worse than none |
| A command not in the manifest renders as **text with a copy affordance**, never as a button | never dispatch a string the panel does not understand |
| Messages of `type: "command"` (echoes of what was typed) are ignored; only `type: "response"` | otherwise the rail suggests what you just ran |

**Appearance.** Full-width strip directly beneath the header, above everything else. `--surface-2`
ground, `--r-card`, 1 px `--divider`, min-height 0 — **it is absent when empty and reserves no
space.** Each item is a 36 px row: a 3 px left rule in `--staged` (or `--fault` when the message was
`TYPE=error`), the button at its natural width, then the sentence in `t-meta`, truncated.

```
┌─┬──────────────────────────────────────────────────────────────────────────────┐
│▌│ [ MMU_RECOVER GATE=2 ]   filament is at the toolhead but no lane owns it…  2m │
│▌│ [ ACE_CLEAR_OP ]         toolchange T2→T1 interrupted; open intent recorded 2m│
│▌│ [ ACE_AUDIT ]            run ACE_AUDIT to see where the filament is        2m │
└─┴──────────────────────────────────────────────────────────────────────────────┘
```

**States:** default · hover/pressed on its buttons · disabled-with-reason (predicate from §4.3) ·
in-flight (participle + `--busy` rule) · fault (item's left rule `--fault`) · **stale** (the message
is older than 15 min and has not been superseded → the row dims to `--text-faint` and the button
becomes disabled-with-reason `this suggestion is {n} minutes old — re-check the state first`) ·
**empty (the component does not render at all)**.

**Why this is the highest-value component here.** It is the only one whose payoff scales with the
number of error messages rather than with implementation effort, it needs no macro changes, and it
degrades safely: an unknown command is text, a guarded command is guarded. It also *measures itself* —
if the rail is usually empty the machine is behaving, and if it is usually full that is the backlog,
visible.

**It does not replace the per-lane controls.** A rail is reactive; it only offers what has already
gone wrong. The lane tiles are how you act before anything says anything.

---

## 3. Layouts, at real pixel dimensions

Three artboards, all present in the mockup. Grid is 12 columns everywhere; only the gutter and the
column span change.

### 3.1 Desktop / wide browser — 1440 × N, content 1240

Page: `--bg`, `padding: 24px`, content `max-width: 1240px`, centred. Grid: 12 × 82.9 px columns,
20 px gutter (`grid-template-columns: repeat(12, minmax(0,1fr)); gap: 20px`).

**Block order is controls-first.** The diagram used to sit above the lane tiles; it now sits below
them. What is above the fold is what you can press.

| # | block | span | height | contents |
|---|---|---|---|---|
| 1 | header card | 12 | 108 | row A (56 px): `ACE 2 Pro` title · connection · path · **audit verdict** · open-intent · job pills · right-aligned `STOP ALL` (destructive, 36 px, 150 px). Row B (44 px, above a `--divider`): dryer strip (§2.7) with its three controls |
| 2 | banners | 12 | 0 or 96 each | fault, then jam, then open intent. Absent when clear — they reserve no space |
| 3 | **action rail** | 12 | 0 or 36 per item | §2.12. Directly under the header because it is the answer to "what does the machine want me to type". Absent when empty |
| 4 | **lane tiles** | 3 each | 268 | four §2.1 tiles across — **promoted above the diagram** |
| 5 | path card | 8 | 260 | `PATH` micro-label, the SVG (§2.2) at 760 × 200, caption, `Calibrate path` in the caption when uncalibrated. **Each lane's run is a click target** that scrolls to and highlights that lane's tile; without that behaviour this block drops to a 96 px strip |
| 6 | toolhead card | 4 | 260 | §2.10, four sensor rows + cold-pull verdict, plus `Audit` and `Query sensors` |
| 7 | hand-fed strip | 12 | 84 | §2.8 |
| 8 | recovery bar | 12 | 60 | `Audit` · `Recover` · `Clear op` · `Unlock toolchange` · `Reconcile target` · `Clear suppression` — every one of them a command an error message names today |

Above the fold at 1440 × 900: header 108 + rail (0–108) + lane tiles 268 = 376 to 484 plus gutters.
**All four lane tiles, every lane control, the recovery rail and `STOP ALL` are visible without
scrolling, and the diagram is what you scroll to.** That inverts the current panel, which spends its
first screen on a picture and a dryer paragraph and offers nothing to press.

Breakpoints, and what collapses:

| width | lane tiles | path + toolhead | other |
|---|---|---|---|
| ≥ 1180 | 4 across (span 3) | side by side (8 / 4) | as above |
| 900–1179 | **2 × 2** (span 6) | stacked, path 12 then toolhead 12 (height 180, rows become a 2 × 2 grid) | action bar wraps to two rows |
| 700–899 | 2 × 2 (span 6) | as above | header rows A and B stack; `STOP ALL` moves to a full-width row |
| < 700 | phone layout (§3.2) | — | — |

The tile never narrows below 268 px; below that it becomes the phone tile, which is a different
component, not a squeezed one.

### 3.2 Phone portrait — 390 × 844 (safe area 390 × 780)

Single column. `padding: 12px`, content 366 px. Two sticky elements, because a phone in a hand
scrolls and the two things you must never lose are *what state is it in* and *how do I stop it*.

| # | block | height | behaviour |
|---|---|---|---|
| 1 | sticky header | 56 | `ACE 2 Pro` · state pill · theme toggle. Backdrop `--bg` at 92 % with a `--divider` bottom edge |
| 2 | banners | 96 each | as desktop |
| 2b | **action rail** | 44 per item, max 3 | §2.12, immediately under the header. On a phone it is capped at 3 items and the sentence wraps to two lines |
| 3 | lane tiles ×4 | see row 5 | **promoted above the diagram**, same inversion as desktop |
| 4 | path card | 236 | the SVG rotates to a **vertical rail** 300 × 280: four lane stubs across the top feeding a vertical shared run. Same rules, same sources |
| 4 | toolhead row | 76 | the four sensor dots on one row with labels beneath, verdict on a second line |
| 5 | lane tiles ×4 | 116 collapsed / 244 expanded | collapsed = swatch, `T2 · Bambu Lab Yellow PLA`, badge, `884 / 934 mm`, bar. Tap anywhere on the tile to expand the four buttons. **One tile expanded at a time**; expanding scrolls it to just below the sticky header |
| 6 | hand-fed strip | 108 | two stacked rows |
| 7 | sticky action bar | 64 + safe-area inset | `Audit` · `Recover` · `Clear op` · `⋯` (opens a sheet with the rest). `STOP ALL` is a **destructive 56 px pill pinned to the right of this bar** — always reachable, one thumb, no scroll |

Type steps up: `meta` and `reason` 12.5 → 13.5 px; `measure-xl` 30 → 26 px (the number is narrower on
a 366 px column and 30 px wraps `884 / 934 mm`). Buttons are 48 px tall, full width of their half
column (175 px), so every hit area is ≥ 48 × 48.

Landscape phone falls through to the 700–899 desktop breakpoint; it is not a separate design.

### 3.3 KlipperScreen — 800 × 480 landscape, one screen, no scrolling

**This screen is sized for sufficiency, not for depth.** The owner debugs at the desk, not at the
machine; the touchscreen's finding that it sent one command in three weeks reflects where he stands
at least as much as it reflects the UI. The requirement here is unchanged and non-negotiable —
**every action reachable and every prompt answerable without a browser** — but the design effort and
the richness belong on the web panel. Read §3.3 as a completeness checklist and §3.1 as the design.

Hard constraint: everything fits in 480 px vertical and nothing scrolls. The arithmetic, exactly:

```
  8  padding-top
 48  header bar
  6  gap
 26  context line  (jam / fault / path-busy — always present, reads "path clear" when clear)
  6  gap
300  lane row
  6  gap
 72  footer bar
  8  padding-bottom
───
480
```

Horizontal, exactly:

```
  8  padding-left
148  T0    ┐
  8        │
148  T1    │
  8        ├ 4 lane tiles + 4 gaps of 8 = 624
148  T2    │
  8        │
148  T3    ┘
  8
160  toolhead column
  8  padding-right
───
800
```

**Minimum touch target: 48 × 48 px, held everywhere.** The narrowest interactive element in the
layout is a lane-tile button at 132 × 52 (148 tile − 2 × 8 padding). The header's `STOP ALL` is
148 × 48. Footer buttons are 150 × 72 (five across: `150 × 5 + 8 × 4 = 782`, with the last at 152 to
fill 784).

Lane tile at 148 × 300, top to bottom:

```
 ┌──────────────┐
 │ ▪ T2  LOADED │  36  swatch 18 + label + badge (badge wraps to its own 18px line if needed)
 │ Bambu Yellow │  22  name, 13px, 1 line, ellipsis
 │ PLA · 335 g  │  18  meta, 11px
 │              │
 │     884      │  38  measure, 26px tabular
 │   / 934 mm   │  16  unit
 │ ▓▓▓▓▓▓▓▓░░░  │  10  bar (6px + 4 gap)
 │              │
 │ ┌──────────┐ │  52  Park / Load  (primary)
 │ └──────────┘ │   8
 │ ┌──────────┐ │  52  Eject        (destructive)
 │ └──────────┘ │
 │ reason line  │  30  two lines at 11px if needed
 └──────────────┘
   36+22+18+8+38+16+10+8+52+8+52+30 = 298, +2 border = 300
```

Two buttons per tile, not four: at 148 px wide, four buttons means two columns of 66 px, which
fails 48 px only on paper and fails a gloved thumb in practice. **`Retract` and the rest live behind
the footer's `More ▸`**, which opens a full-screen sheet — the tile keeps the two actions that are
worth a dedicated target.

Footer, five buttons: `Audit` · `Recover` · `Clear op` · `Dryer` · `More ▸`.

Header, left to right: `ACE 2 Pro` (17 px) · connection dot · `28 °C 23 %` · spacer · `STOP ALL`
(destructive, 148 × 48, right-aligned, always in the same place).

Toolhead column, 160 × 300: `TOOLHEAD` micro-label, four 32 px sensor rows (§2.10), a `--divider`,
then the verdict in two lines of 13 px. No controls — every control on this screen is in a tile, the
header, or the footer, so a thumb has three fixed places to look.

**What is deliberately absent from this screen**, and why, is §6.

---

## 4. Every element traced, both directions

### 4.1 The predicate vocabulary

Every enabled-when expression in §4.3 is written from these, so the predicates are auditable rather
than prose. All paths verified live 2026-08-31.

| name | expression in live field names |
|---|---|
| `CONNECTED` | `ace_instance_0.connection_state == "connected"` |
| `FRESH` | `ace_buffer_watch.stale == false` |
| `PRESENT(i)` | `FRESH and ace_buffer_watch.inserted[i] == true` |
| `STAGED(i)` | `save_variables.variables.ace_staged_mm[i] > 0` |
| `LOADED(i)` | `ace.current_index == i` |
| `CUR` | `ace.current_index` (−1 = none) |
| `TGT` | `ace.target_index` (−1 = no change in flight) |
| `PATH_FREE` | `ace_preload_guard.path_busy == false` |
| `NO_OP` | `save_variables.variables.ace_op == ""` |
| `PRINTING` | `print_stats.state in ("printing", "paused")` |
| `SENSORS_OK` | `toolhead_entry.enabled and toolhead_postgear.enabled` — **false right now** |
| `AT_TOOLHEAD` | `toolhead_entry.filament_detected or toolhead_postgear.filament_detected` |
| `IMPOSSIBLE` | `SENSORS_OK and not toolhead_entry.filament_detected and toolhead_postgear.filament_detected` |
| `EJECTING` | `printer["gcode_macro ACE_LANE_EJECT"].running == 1` |
| `EJECT_TOOL` | `printer["gcode_macro ACE_LANE_EJECT"].tool` |
| `JAM` | `ace_buffer_watch.jam == true`, tool `ace_buffer_watch.jam_tool` |
| `AUDIT_OK` | `printer["gcode_macro ACE_AUDIT"].ok == 1` |
| `AUDIT_RUN` | `printer["gcode_macro ACE_AUDIT"].toolhead != ""` |
| `HOT` / `PARKED` | `save_variables.variables.filament_loaded_hot == 1` / `.filament_parked == 1` |
| `PATH_CLEAR_FOR_CAL` | `not hub_detect.filament_detected and not toolhead_entry.filament_detected and not toolhead_postgear.filament_detected` |
| `SPOOLMAN` | `mmu.spoolman_support != "" and /server/spoolman/status → spoolman_connected` |
| `DRYING` | `ace_instance_0.dryer_status.status != "stop"` |
| `AUTODRY` | `printer["gcode_macro ACE_AUTODRY"].active == 1` — **1 right now** |
| `ROLLING` | `save_variables.variables.ace_dryroll_active == 1` |
| `SUPPRESSED` | `printer["gcode_macro _ACE_SUPPRESSION_ARM"].armed_swapping == 1 or .armed_parking == 1` |

**`AUDIT_RUN` is load-bearing.** `printer["gcode_macro ACE_AUDIT"]` reads `{ok: 1, why: "",
toolhead: ""}` on a fresh boot — `ok` defaults to `1` before the audit has ever executed. Rendering
that as a green *coherent* is a picture that lies. **The verdict shows `not run yet` in `--unknown`
until `toolhead != ""`**, and the `Run audit` button takes primary rank while that is the case.

### 4.2 Element → field path

Extends chapter 11 §4.3. Rows marked **new** are elements this specification introduces; rows marked
**corrected** replace a mapping in §4.3 that does not hold.

| Element | Source |
|---|---|
| **Header** | |
| unit name / vendor / firmware | **new** — `mmu_machine.unit_0.{name,vendor,version}`; fall back to `ace_instance_0.firmware` |
| connection pill | `ace_instance_0.connection_state`, `.status` |
| transport detail (tooltip) | **new** — `ace_instance_0.{model,usb_port,protocol}` |
| path pill | `ace_preload_guard.path_busy` → `path busy · T{owner}` / `path clear` |
| audit verdict pill | **new** — `printer["gcode_macro ACE_AUDIT"].{ok,why,toolhead}`, gated on `AUDIT_RUN` |
| open-intent pill | **new** — `save_variables.variables.ace_op` (non-empty ⇒ `--fault`) |
| in-flight pill | **new** — `ace.target_index != -1` ⇒ `toolchange T{cur}→T{tgt} in flight` |
| job pill | `print_stats.{state,filename}`, `idle_timeout.state` |
| suppression state | **new** — `printer["gcode_macro _ACE_SUPPRESSION_ARM"].{armed_swapping,armed_parking,seconds}` |
| **Dryer strip** | |
| temp / RH | `ace_instance_0.temp`, `.humidity` |
| absolute humidity | Magnus from the two above (already implemented, `ace_index.html:143`) |
| mode / target / remaining | `ace_instance_0.dryer_status.{status,state_detail,target_temp,duration,remain_time}` |
| dryer progress rule | **new** — `1 − remain_time / duration`, drawn only when `duration > 0` |
| auto-dry state | **new** — `printer["gcode_macro ACE_AUTODRY"].{active,target_rh,temp,interval}`; persisted seed `save_variables.variables.ace_autodry_*` |
| dry-roll state | **new** — `save_variables.variables.{ace_dryroll_active,ace_dryroll_mode[i],ace_dryroll_pos[i]}`; policy `printer["gcode_macro _ACE_DRYROLL_VARS"].{interval,allow_printing}` |
| **Lane tile** | |
| swatch | `ace_instance_0.slots[i].color` — `[0,0,0]` = absent; fall back `mmu.gate_color_rgb[i]` |
| lane present | `ace_buffer_watch.inserted[i]`, **unknown when `.stale`** — never `lane_empty` |
| lane under tension | `ace_buffer_watch.at_rest[i]` |
| ACE-reported slot word | `ace_instance_0.slots[i].status` (`empty` / `ready` / `feeding` / `shifting`) |
| **staged mm** | **corrected** — `save_variables.variables.ace_staged_mm[i]`. §4.3 read `mmu.filament_position_per_gate[i]`; that array is 4 long while `num_gates` is 6, and it is a mirror rather than the source. `ace_staged_mm` is the persisted truth (§0.1) |
| lane tube length | `save_variables.variables.ace_cal_park_to_hub[i]` if non-zero, else `ace_park_to_entry − ace_cal_hub_to_entry` (= 934.1) |
| **lane state word** | **corrected** — derived in the panel from `PRESENT / STAGED / LOADED / AT_TOOLHEAD`. **Not** `mmu.filament_pos_per_gate` (collapsed, §2.1) and **not** `save_variables.ace_lane_pos` (holds the never-written value `parked`, §2.3) |
| material / family | `mmu.gate_material[i]`, `mmu.gate_material_family[i]`; fall back `slots[i].material` |
| spool name / id | `mmu.gate_filament_name[i]`, `mmu.gate_spool_id[i]` |
| spool weight / sku | `ace_instance_0.slots[i].{total,current,sku}` |
| print temp hint | **new** — `mmu.gate_temperature[i]`; range `slots[i].extruder_temp.{min,max}` |
| RFID present | **new** — `ace_instance_0.slots[i].rfid` (drives the `Scan tags` affordance) |
| consumed | **new** — `mmu.gate_consumed_mm[i]` |
| **Path diagram** | |
| lane fill fraction | `ace_staged_mm[i] / laneTube` |
| shared-run owner | **corrected** — the lane where `ace_staged_mm[i] >= laneTube`, else `ace.current_index`. §4.3 and `ace_index.html:296` use `filament_pos_per_gate.findIndex(p => p >= 2)`, which returns −1 and indexes `slots[-1]` |
| shared-run tip | sensor ladder (hub → entry → gears → melt); `ace_hub_encoder.distance_mm ÷ ace_cal_hub_to_entry` refines it between the hub and entry |
| encoder motion marker | **new** — `ace_hub_encoder.moving`, `.pulses`, `.mm_per_pulse` (subscribed and unused today, §2.8) |
| hub dot | `filament_switch_sensor hub_detect.filament_detected` + analog `.reading`; **`.enabled`** |
| entry / gears dots | `filament_switch_sensor toolhead_entry` / `toolhead_postgear` `.filament_detected` + **`.enabled`** |
| melt dot | `mmu.in_melt_zone` — labelled `derived`, no switch behind it |
| calibration caption | `save_variables.variables.{ace_cal_hub_to_entry,ace_cal_park_to_entry,ace_cal_when,ace_cal_mm_per_pulse}` |
| **Toolhead column** | |
| cold-pull verdict | `mmu.cold_unload_ok` — see the QA flag in §4.4 |
| hot / parked flags | `save_variables.variables.{filament_loaded_hot,filament_parked}` |
| melt-zone length | `save_variables.variables.meltzone_mm` (22.0) |
| cut geometry | **new** — `save_variables.variables.{crossbow_blade_to_nozzle,crossbow_postgear_to_blade,ptfe_postgear_to_nozzle}` |
| **Banners** | |
| jam | `ace_buffer_watch.{jam,jam_tool,last_event,offrest_seconds,armed,armed_tool}` |
| sensor fault | the entry/post truth table (§2.2) plus `.enabled` on both |
| eject in progress | `printer["gcode_macro ACE_LANE_EJECT"].{running,tool,do_home}` |
| **Hand-fed strip** | `mmu.manual_tools`, `mmu.gate_status[i]`, `mmu.gate_material[i]`, `mmu.gate_filament_name[i]`, `mmu.gate_spool_id[i]`, `mmu.gate_color_rgb[i]` — **never** `filament_position_per_gate` (§0.1) |
| **Spool picker** | `POST /server/spoolman/proxy {"request_method":"GET","path":"/v1/spool"}`; connection from `GET /server/spoolman/status` |
| **Panel health** | **new** — `GET /ace/status.json`, written by `patch_mainsail.py` (§5.3) |

Deleted from the panel by this specification: `mmu.filament_pos_per_gate` (collapsed),
`ace_buffer_watch.lane_empty` (dead), `save_variables.ace_lane_pos` (holds a value nothing writes),
`mmu.bowden_progress` (hardcoded `-1`, and load-shaped by construction — chapter 11 §2.4).

### 4.3 Control table

Every control the panel offers. **`gcode`** is the exact string sent. **`enabled-when`** is written in
the §4.1 vocabulary. **`disabled reason`** is the verbatim string rendered beneath the control — the
first matching row wins, and the strings are quoted here so they can be lifted into the
implementation unchanged.

Where the reason echoes a macro's own refusal, the macro's wording is used, because the operator
should not have to learn two vocabularies for the same fact.

#### Per-lane controls (lane tile, both surfaces)

| Control | gcode | enabled-when | disabled reason (first match) |
|---|---|---|---|
| **Load** | `T{i}` | `CONNECTED and PRESENT(i) and not LOADED(i) and CUR == -1 and PATH_FREE and NO_OP and TGT == -1 and not EJECTING` | `not CONNECTED` → `the ACE is disconnected` · `not FRESH` → `lane sensors are stale — state is unknown` · `not PRESENT(i)` → `no filament in lane {i}` · `LOADED(i)` → `T{i} is already loaded` · `CUR >= 0` → `T{CUR} is loaded — park or eject it first` · `EJECTING` → `an eject is running on T{EJECT_TOOL}` · `not PATH_FREE` → `the shared path is busy` · `TGT != -1` → `toolchange T{CUR}→T{TGT} is still in flight — reconcile it first` · `not NO_OP` → `open intent: {ace_op} — recover before moving filament` |
| **Park** (swap-ready) | `ACE_LANE_PARK T={i}` | `CONNECTED and LOADED(i) and AT_TOOLHEAD and PATH_FREE and NO_OP and not EJECTING` | `not LOADED(i)` → `T{i} is not the loaded lane` · `not AT_TOOLHEAD` → `nothing at the toolhead to park` · others as above |
| **Retract → gate** | `ACE_LANE_PARK T={i} FORCE=1` | `CONNECTED and STAGED(i) and not LOADED(i) and not AT_TOOLHEAD and PATH_FREE and NO_OP and not EJECTING` | `not STAGED(i)` → `T{i} is already back at the gate` · `LOADED(i)` → `T{i} is loaded — use Park` · `AT_TOOLHEAD` → `filament is still at the toolhead — clear it first` |
| **Eject** | `ACE_LANE_EJECT T={i}` | `CONNECTED and not EJECTING and (PRESENT(i) or AT_TOOLHEAD) and not (AT_TOOLHEAD and CUR >= 0 and CUR != i) and PATH_FREE` | `EJECTING and EJECT_TOOL != i` → `an eject is already running on T{EJECT_TOOL} — eject lanes one at a time` · `not PRESENT(i) and not AT_TOOLHEAD` → `T{i} is already empty and the path is clear` · `AT_TOOLHEAD and CUR >= 0 and CUR != i` → `the toolhead holds T{CUR}, not T{i}. Unload T{CUR} first.` |
| **Eject (force)** | `ACE_LANE_EJECT T={i} FORCE=1` | shown **instead of** Eject when `AT_TOOLHEAD and (CUR == -1 or (not HOT and not PARKED))`, and enabled under the same terms except those two | caution line, always visible, never a disable: `no lane is recorded as loaded — confirm by eye that this strand is T{i}'s before forcing` |
| **Abort eject** | `ACE_LANE_UNLOAD_ABORT` | `EJECTING` | `no eject is running` |
| **Assign spool…** | `MMU_GATE_MAP GATE={i} SPOOLID={id}` | `SPOOLMAN` — **deliberately not gated on `PATH_FREE`, `PRINTING` or `TGT`**: `MMU_GATE_MAP` is metadata only and never moves filament (`ace_mmu_shim.py:1180`) | `not SPOOLMAN` → `Spoolman is not reachable` |
| **Calibrate path** | `ACE_CALIBRATE_PATH T={i} FROM_PARK=1` | `CONNECTED and PATH_CLEAR_FOR_CAL and slots[i].status == "ready" and PATH_FREE and not PRINTING` | `hub_detect.filament_detected` → `hub_detect already shows filament — clear the path first` · same for `toolhead_entry` / `toolhead_postgear` · `slots[i].status != "ready"` → `lane T{i} is '{status}', not ready` · `PRINTING` → `not while a print is running` |
| **Plan swap to T{i}** | `ACE_PLAN_SWAP TOOL={i}` | `PRINTING and PRESENT(i) and not LOADED(i) and i not in mmu.manual_tools` | `not PRINTING` → `only during a print — otherwise just Load` · `i in manual_tools` → `T{i} is hand-fed — the machine cannot feed it` · `LOADED(i)` → `T{i} is already loaded` |

#### Global controls (header, footer, banners)

| Control | gcode | enabled-when | disabled reason |
|---|---|---|---|
| **STOP ALL** | `ACE_STOP_ALL` **(new macro — §4.5)** | **always.** No predicate, in any state, including disconnected, stale and fault | — |
| **Run audit** | `ACE_AUDIT` | always | — |
| **Recover** | `MMU_RECOVER GATE={i}` from a lane tile; `MMU_RECOVER` from the footer | `CONNECTED` | `not CONNECTED` → `the ACE is disconnected` |
| **Clear op** | `ACE_CLEAR_OP` | `not NO_OP` | `no open intent recorded` |
| **Unlock toolchange** | `MMU_UNLOCK` | `TGT != -1` | `no toolchange is in flight` |
| **Reconcile target** | `ACE_RECONCILE_TARGET` | `TGT != -1` | `no toolchange marker to resolve` |
| **Clear suppression** | `_ACE_SUPPRESSION_DISARM` | **always.** Deliberately not gated on `SUPPRESSED` — the reason you press it is that the state model is wrong, and it has no refusals | state line beneath, always visible: `suppression: armed (swapping)` / `armed (parking)` / `clear` |
| **Clear jam on T{n}** | `ACE_BUFFER_DISARM` | `JAM` | `no jam is latched` |
| **Buffer state** | `ACE_BUFFER_STATE` | always | — |
| **Scan tags** | `ACE_SCAN_TAGS` | `CONNECTED and any(slots[i].status != "empty")` | `every lane reads empty — nothing to scan` |
| **Preload guard status** | `ACE_PRELOAD_GUARD_STATUS` | always | — |
| **Printer state** | `PRINTER_STATE` | always | — |
| **Normalize lanes** | `ACE_LANE_NORMALIZE ALL=1` | `CONNECTED and PATH_CLEAR_FOR_CAL and not PRINTING` | `{sensor} shows filament — clear the path before parking` · `not while a print is running` |
| **Select bypass** | `MMU_SELECT_BYPASS` | `AT_TOOLHEAD or hub_detect.filament_detected` | `no filament at the hub or the toolhead — nothing to mark as bypass` |
| **Clear bypass** | `MMU_SELECT_BYPASS CLEAR=1` | `save_variables.variables.ace_bypass_loaded == 1` | `no bypass is selected` |
| **Dry {preset}** | `ACE_DRY MATERIAL={family}` or `ACE_DRY TEMP={t} MINUTES={m}` | `CONNECTED and not DRYING` | `not CONNECTED` → `the ACE is disconnected` · `DRYING` → `the dryer is already running` |
| **Stop drying** | `ACE_DRY_OFF` | `DRYING or AUTODRY` | `the dryer is idle and auto-dry is off` |
| **Auto-dry** | `ACE_AUTODRY MATERIAL={family}` | `CONNECTED and not AUTODRY` | `auto-dry is already holding {target_rh} %RH` |
| **Roll: start** | `ACE_DRYROLL_START` | `CONNECTED and AUDIT_RUN and AUDIT_OK and not (PRINTING and _ACE_DRYROLL_VARS.allow_printing == 0)` | `not AUDIT_OK` → `state is not coherent: {ACE_AUDIT.why}` · `not AUDIT_RUN` → `run an audit first — coherence has not been checked` · printing case → `rolling is disabled during a print` |
| **Roll: stop** | `ACE_DRYROLL_STOP` | `ROLLING` | `rolling is not active` |
| **Roll: mode** | `ACE_DRYROLL_MODE T={i} MODE={off\|sweep\|spin}` | `CONNECTED` | `the ACE is disconnected` |
| **Cleanup stale vars** | `ACE_CLEANUP_STALE_VARS` then `ACE_CLEANUP_STALE_VARS CONFIRM=1` | `not PRINTING` | `not while a print is running` |
| **Reset shared bus** | `ACE_RESET_SHARED_BUS_BINDINGS` | `not PRINTING` | `not while a print is running` |

Two entries in that table are **friction preserved on purpose**, and they are called out so a future
pass does not tidy them away:

- **`ACE_CLEANUP_STALE_VARS` stays two-step.** Its own docstring says CONFIRM exists "because stale
  `ace_inventory_N` keys hold user-entered spool data that cannot be rebuilt". The panel shows the
  dry-run list, then a separate destructive confirm. It is not a single button.
- **`Eject (force)` never becomes the default.** It appears only in the two states the macro itself
  allows FORCE to override, it keeps the destructive rank, and it carries the "confirm by eye"
  caution permanently — not as a dismissible toast. `ACE_LANE_EJECT`'s own refusal is three lines
  long for a reason.

Two more are **new gating this specification adds**, and both are conservative rather than permissive:
`Load` requires `CUR == -1` (the macro would accept a direct swap, but a panel that offers Load on
lane 3 while lane 2 is loaded invites a toolchange the operator did not mean), and every motion
control requires `FRESH`.

### 4.4 Flagged for QA — not decided here

1. **`mmu.cold_unload_ok` has zero consumers and `mmu.in_melt_zone` has one, and it is advisory.**
   The heat-vs-cold decision — the single most damaging call on this machine — is re-derived
   independently, in duplicated Jinja, inside both `ACE_LANE_EJECT` (`ace_unload.cfg:156`) and
   `ACE_LANE_PARK` (`ace_toolchange.cfg:229`), from `filament_loaded_hot` + `filament_parked` +
   post-gear. The shim publishes an answer nobody reads. **Which of the two is authoritative is a QA
   call, not a UX one.** Until it is settled, the panel displays *both*: `mmu.cold_unload_ok` labelled
   `driver says`, and the flag-derived verdict labelled `state says`, and when they disagree it draws
   a `--staged` warning rather than picking a winner. A panel that picks one and is wrong is worse
   than a panel that shows the disagreement.
2. **Whether a latched jam should block the destructive controls** (§2.6).
3. **Manual jog** — `ACE_RAW_FEED` and `FORCE_MOVE STEPPER=extruder`. Left flagged exactly as
   chapter 11 §4.4 left it. **This specification does not draw a manual-jog screen.**
4. **`Load` requiring `CUR == -1`** — see above. If the intent is that the panel should offer a
   direct lane-to-lane toolchange, that predicate loosens to `CUR != i`.

### 4.5 The one new command this design requires

`ACE_STOP_ALL`. There is no single command that stops the ACE, and the panel must not send four
lines of gcode to make one button work.

```
[gcode_macro ACE_STOP_ALL]
description: Stop every ACE motion at once: feed, retract, and feed assist on all four lanes
gcode:
    {% for i in range(4) %}
        ACE_STOP_FEED T={i}
        ACE_STOP_RETRACT T={i}
        ACE_DISABLE_FEED_ASSIST T={i}
    {% endfor %}
    ACE_LANE_UNLOAD_ABORT
    RESPOND MSG="[ACE] all motion stopped: feed, retract and assist disabled on T0-T3"
```

Three notes. `ACE_STOP_FEED` and `ACE_STOP_RETRACT` swallow their own exceptions
(`commands.py:659,700`), so they cannot abort the loop. `ACE_DISABLE_FEED_ASSIST` genuinely raises on
an invalid slot (`commands.py:897`) — `0..3` are always valid on this machine, so the loop is safe,
but the range must not be widened to `num_gates`, because gates 4 and 5 are hand-fed and have no
actuator. And `ACE_LANE_UNLOAD_ABORT` is included because a running eject is ACE motion; **feed
assist is motion, and so is an eject in progress.**

Size: XS. It is a prerequisite for the touchscreen's most important button.

---

## 5. Delivery

### 5.0 The placement question is already answered — verified 2026-08-31

The owner asked for the panel to be *"integrated as a card inside the Klipper web UI directly"*
rather than a separate URL. **It already is.** Verified read-only against the live machine:

```
GET http://10.49.9.130:7125/server/webcams/list
{ "name": "ACE Panel", "enabled": true, "service": "iframe",
  "stream_url": "/ace/", "snapshot_url": "/ace/",
  "aspect_ratio": "4:3", "location": "printer", "source": "database",
  "uid": "bb4cd0ee-e7c7-40db-ad99-98a0c7bb30ed" }
```

and in the live v2.19.0 bundle (`mainsail/assets/index-jNhnvCHe.js`):

- `service === "iframe"` dispatches to Mainsail's `html-iframe-async` component — a first-class entry
  in the webcam service dropdown (`Settings.WebcamsTab.HtmlIframe`), not a hack;
- `hasAspectRatio(){ return ["iframe"].includes(this.webcam.service) }` — **aspect ratio is an
  iframe-only setting**, free text with a validator, editable from Mainsail's own settings UI. That
  is the card's height mechanism, and it is under our control.

**Three things follow, and they matter more than the styling.**

1. **His "it's the same as the fake camera" is literal, not a simile.** The panel *is* a fake camera
   entry, already sitting on his dashboard. He was not describing a page he has to navigate to; he
   was describing a card he already has that shows him nothing he can press. **Placement was never
   the problem. Passivity was.** That makes §0's re-weighting the whole job.
2. **Chapter 11 §3.3's "Nothing in either Mainsail links to `/ace/`" is wrong** and the work item
   "link `/ace/` from Mainsail via `patch_mainsail.py`" is dead. There is no link to add.
3. **No bundle patching is needed for placement, now or ever.** The registration lives in Moonraker's
   `webcams` namespace — the database — which a Mainsail `update_manager type: web` extraction cannot
   touch, and **both :80 and :82 read the same Moonraker**, so it is already on both instances. This
   is a data-level insertion, not regex surgery, and it is strictly better than the dashboard-layout
   route: the dashboard column order *is* also persisted in the DB (`mainsail` namespace,
   `widescreenLayout1/2/3`, arrays of `{name, visible}`), but a name there only renders if the bundle
   already knows that component — so the DB alone would not have been enough. The webcam route
   sidesteps that entirely.

#### 5.0.1 Is the card actually interactive? Yes — verified from the component source

This was a gate: if Mainsail overlaid chrome on the iframe or swallowed pointer events, the whole
control-surface plan would die at placement. It does not. `assets/HtmlIframe-vUGZiy4a.js` is ~1 KB
and this is its entire render:

```js
t('div', {staticClass:'webcamBackground', style: wrapperStyle},
  [ t('iframe', {staticClass:'webcamImage', style: iframeStyle,
                 attrs:{src: url, title: camSettings.name}}) ])
```

| question | answer, from the source |
|---|---|
| `sandbox` attribute? | **No.** Scripts, forms and same-origin access all work. |
| overlay, `pointer-events`, click-to-expand on the container? | **None.** The host `<div class="webcamBackground">` carries only the wrapper's aspect-ratio style; the panel's only chrome (title, collapse chevron, camera-selector menu) sits in the header *above* the frame. |
| idle / power-saving blanking? | **No.** The component never reads `target_fps` or `target_fps_idle` and has no visibility logic — those fields apply to the mjpeg services. The panel does not blank when the printer is idle, which is exactly when a lane needs fixing. |
| aspect ratio format | `/^(\d+)\s*[:/]\s*(\d+)$/` — **any integer `W:H` or `W/H`**, defaulting to `16/9` when blank or unparseable. |
| rotation / flip | applied as a CSS `transform` **on the iframe**. Harmless for a camera, ruinous for a control panel. **Keep `rotation: 0`, `flip_*: false` on this entry, always.** |

**The one real cost of the webcam route, and it is not a blocker.** `WebcamPanel` shows **one camera
at a time**, chosen from a menu in its header, and the selection is stored per *page*
(`gui.view.webcam.currentCam[currentPage]`, `"dashboard"` or `"page"`) — **not per panel**. So:

- Two webcam cards on the dashboard is not a workaround. A layout entry named `webcam_ace` does
  render a second panel (`extractPanelName(e) = e.split("_")[0] + "-panel"`,
  `extractPanelId(e) = e.split("_")[1]`), but both panels read the same `currentCam["dashboard"]`
  and would show the same feed.
- The `All` grid does render both at once, but each cell is `col-12 col-md-6` — a **viewport**
  breakpoint, not a container one — so on a desktop viewport they sit side by side at half the
  column, ~300 px each. Too small for a control surface. Checked, and rejected.
- **The dashboard and the `/cam` page keep independent selections**, because the key is
  `currentPage`. That is the lever.

**Recommendation: pin the dashboard webcam card to `ACE Panel`, and put the Arducam on `/cam`.** He
watches the camera occasionally and deliberately; he needs the lane controls in his eyeline while he
works, and the panel is the thing he is currently having to leave in order to type a command. If he
would rather keep the camera on the dashboard, the fallback is `/cam` pinned to `ACE Panel` plus the
sidebar entry (§5.3) — one click, but a page change, and that reintroduces a smaller version of the
problem this whole section exists to remove.

**Not recommended: registering a real, non-webcam dashboard card.** That means injecting a Vue
component into the minified bundle — the same class of regex surgery as §2.7's nozzle dot, redone
every Mainsail release, failing silently. The webcam route gets the same placement from a database
record that no update can touch. **A verified second-best beats an unverified best.**

**And, stated once so nobody re-litigates it: KlipperScreen cannot host an iframe.** It is a
GTK/Python application, not a browser. **The touchscreen is a separate build regardless of how the
web surface is delivered**, which is why §3.3 is a sufficiency spec and §6.1 is a split rather than a
port.

#### 5.0.2 The size constraint

The card is sized by aspect ratio at the dashboard column's width — roughly **420 × 315 to
620 × 465 at `4:3`**. That is not the 1240 px artboard. So:

| where | size | what it must do |
|---|---|---|
| **dashboard card** (the surface he actually looks at) | ~600 × 400 at `3:2` — **change the ratio from `4:3`; recommend `3:2` or `16:10`** | header, action rail, four lane tiles in a 2 × 2 grid, `STOP ALL`. **Every control reachable; no diagram.** |
| **card fullscreen / the `/webcam` page** | viewport width | the full §3.1 layout, diagram included |
| **`/ace/` opened directly** | viewport width | same as fullscreen |

One page, three widths, chosen by a `ResizeObserver` on `document.documentElement` — **not by
media queries**, because inside an iframe the media query sees the iframe's width, which is what we
want here but is worth stating so nobody "fixes" it later. The card-size layout is §3.1's
900–1179 px breakpoint with the path card dropped, and it is the layout that must be designed first
because it is the one he sees.

### 5.1 The decision

**Keep `/ace/` as a single hand-written `ace_index.html`, served where it already is, and keep the
iframe-webcam registration.** Do not introduce a build step. Do not add a bundle patch for placement.
Change `aspect_ratio` from `4:3` to a wider ratio and design the card-size layout first.

### 5.2 The two options, costed honestly

**Option A — extend `ace_index.html` (recommended).**

The specified panel is roughly 60–90 KB of inline HTML, CSS and JS, up from 14.7 KB. It stays one
file with no dependencies, which buys three things that matter here:

- `patch_mainsail.py`'s restore is a single `shutil.copy`. It keeps working unchanged.
- The panel can be edited over SSH, from a phone, at 2 am, with a spool in the other hand. That is
  not a hypothetical on this machine.
- No CDN, so the CSP-free same-origin story stays trivial and the panel works with the printer
  offline.

Cost: no component model, no type checking, and a single file that a careless edit can break
wholesale. Mitigations that are part of this slice — a `<template>`-per-component structure, one
`render()` driven by a single merged state object (the existing pattern), and the state switcher from
the mockup retained behind `?mock=1` so every state can be exercised without waiting for the machine
to enter it.

**Option B — rebuild as a small structured app (Vite + Preact/Vue, ~200 KB output).**

Better component ergonomics, worse everywhere else:

- **It breaks the 15-minute cron restore as written.** The output is a hashed bundle plus an assets
  directory, so `restore_panel()` becomes a tree sync, and the source of truth moves from a file the
  printer holds to a build artefact the printer cannot regenerate. A Mainsail update that wipes
  `/home/simon/mainsail` would restore whatever was last built and copied — which may be older than
  the source in git.
- **It puts a build machine in the loop.** Changing a button label needs node and a deploy, on a
  machine where the fix often needs to happen while standing at the printer.
- It does not survive a Mainsail update any better: neither option couples to Mainsail internals.
  The nozzle-dot bundle patch is a separate concern (§5.3) and is unaffected by either choice.

The one argument for B is that the specified panel is genuinely more complex than the current one.
It is not enough. **Recommend A.**

### 5.3 A sidebar entry as well — optional, and not the answer to placement

§5.0 settles placement: the card exists. This is a small extra for reaching the *full-width* layout
without going through the card's fullscreen control, and it is worth four lines because it costs
four lines.

Mainsail reads a **user navigation file** at `~/printer_data/config/.theme/navi.json`. Confirmed in
the live v2.19.0 bundle (`mainsail/assets/index-jNhnvCHe.js`):

```js
sidebarNaviFileChanged(e){ this.customNaviLinks=[],
  e&&(await fetch(e).then(e=>e.json()).catch(e=>{
      throw window.console.error(`Unable to parse .theme/navi.json.`),e
  })).forEach(e=>{ this.customNaviLinks.push({
      title: e.title ?? `Unknown`, icon: e.icon ?? da,
      href: e.href ?? `#`, target: e.target, position: e.position ?? 999 }) }) }
```

So:

```json
[ { "title": "ACE", "href": "/ace/", "icon": "mdi-printer-3d-nozzle", "position": 45 } ]
```

Why it is worth having anyway:

- `~/printer_data/config/.theme/` is **outside** `/home/simon/mainsail`, so a Mainsail
  `update_manager type: web` extraction cannot wipe it. **It needs no cron, no patcher, no regex.**
- It is a documented Mainsail feature reading a JSON file, not a regex against minified JS. It cannot
  fail silently in the way §2.7 describes; if the JSON is malformed Mainsail logs it to the console.
- The directory already exists on this machine (`custom.css`, 7.6 KB, under its own git repo), so the
  mechanism is already proven to load here.
- It applies to both instances, because both read their own config directory.

Effort: **XS.** One file, four lines.

### 5.3.1 The panel half of `patch_mainsail.py` can be deleted outright

The page still has to be reachable under Mainsail's static root, and today that is a 15-minute cron
`shutil.copy` that fails silently. **An nginx alias removes the copy entirely.** Verified by
inspection of the live host:

- `/etc/nginx/sites-available/mainsail` has `root /home/simon/mainsail;` and a catch-all
  `location / { try_files $uri $uri/ /index.html; }`. There is no existing `/ace/` location, so a new
  one takes precedence cleanly.
- The traversal permissions work: `/home/simon` is `drwx--x--x` (others may traverse but not list),
  and `printer_data`, `config`, `panel` are all `drwxr-xr-x` with `ace_index.html` at `-rw-r--r--`.
  nginx's `www-data` can reach and read it.

```nginx
location /ace/ {
    alias /home/simon/printer_data/config/panel/;
    index ace_index.html;
    try_files $uri /ace/ace_index.html;
}
```

The page would then be **served from where it actually lives**, survive every Mainsail update by
construction, and need no restore, no cron entry and no silent-failure mode. `patch_mainsail.py`
drops to only its MMU-card nozzle-dot injection — a much smaller thing to keep alive — and one known
defect (§2.7) disappears rather than being mitigated.

**Not applied.** This pass does not edit the live printer, and the change needs `nginx -t` plus a
reload. It is verified as achievable, not as done.

### 5.3.2 What remains of the bundle patch

**The bundle patch is demoted, not deleted.** It still owns the nozzle dot inside Mainsail's own MMU
card, which nothing else can add. But it stops being the only route to `/ace/`, and it must stop
failing silently:

- `patch_bundle()` returns `"no MMU bundle found"` both when there is no bundle and when the regex
  missed a reshaped one, and `main()` returns 0 either way. Split those into distinct returns and
  exit non-zero on the second.
- Write `~/printer_data/config/panel/patch_status.json` on every run — `{ran, ok, site, bundle,
  message, when}` — and copy it to `<site>/ace/status.json`. **The panel reads it and shows a
  `--fault` pill reading `Mainsail patch failed — nozzle dot missing` when `ok` is false.** The
  monitoring for a silent failure belongs on the screen the owner already has open.
- Correct the docstring: the trigger is `crontab` `*/15 * * * *` plus `@reboot sleep 60`. The
  `ace-panel-patch.path` unit it names does not exist.

### 5.4 The phone

`/ace/` gains a `manifest.webmanifest` and an apple-touch icon inside its own directory. Installed to
the home screen it opens standalone, on the same origin, with no address bar — which is the closest
thing to an ACE app the machine can have, and it costs one JSON file and one PNG. XS.

---

## 6. Web and touchscreen: the split, and the prompt rules

### 6.1 Where the hands are

| | Web `/ace/` | KlipperScreen |
|---|---|---|
| posture | desk or phone, both hands, time to read, a keyboard within reach | standing at the machine, gloves on, often mid-print, one thumb, no keyboard |
| what it is for | understanding and configuring | acting and recovering |

**Hard requirement, non-negotiable: the touchscreen is fully self-sufficient, including answering
prompts.** A prompt raised while the owner is at the machine must be answerable at the machine. He
has called it out as actively annoying when a dialog on the web blocks the touchscreen.

**On the touchscreen, because it is where you are when you need it:**
see all four lanes and where their filament is · Load · Park · Eject · Eject (force) · **STOP ALL** ·
Clear jam · Run audit and see its one-line verdict · Recover · Clear op · Clear suppression · dryer
on/off · **every prompt the machine can raise.**

**Web only, and why each:**

| Web only | Why |
|---|---|
| spool picker | a searchable list of dozens, plus a text filter. A keyboard task |
| material / colour / temperature editing | Spoolman is the better source of truth, and `acepro.py` needs three nested sub-screens and a numeric keypad to do it badly |
| `ACE_CALIBRATE_PATH`, `ACE_LANE_NORMALIZE` | minutes long, needs a verified-clear path, done quarterly |
| full audit output, `ACE_PRELOAD_GUARD_STATUS`, `ACE_BUFFER_STATE` | multi-line diagnostics. The touchscreen gets the verdict, not the transcript |
| swap history and timings (`ace_swap_timing`) | retrospective, never urgent |
| endless-spool and RFID-sync configuration | set once |
| `ACE_CLEANUP_STALE_VARS` | destroys unrecoverable user data, deliberately two-step |
| `ACE_RESET_SHARED_BUS_BINDINGS` | recovery from a bus fault, done with a laptop open |

The test, unchanged from chapter 11: **you just opened the ACE lid, or you are watching a swap go
wrong.** If it is neither, it is web.

### 6.2 Prompt design rules

Derived from the measured KlipperScreen constraints (chapter 11 §4.6). These are the rules; §6.3 is
a worked example.

1. **The LAST `prompt_text` carries the decision.** KlipperScreen keeps only that one
   (`prompts.py:37` overwrites rather than appends). Everything before it is web-only detail.
2. **Cap the dialog at three `prompt_text` lines.** More is Mainsail-only noise, and it costs nothing
   to send the rest to the console with `RESPOND MSG=`.
3. **The last line follows a fixed priority ladder** (§6.3), so the operator learns where to look
   instead of reading whatever happened to be emitted last.
4. **Four buttons per group, two groups maximum.** `set_max_children_per_line(min(4, n))`.
5. **A button payload may carry parameters** — `ACE_LANE_EJECT T=2 FORCE=1` works today — **but must
   contain no colon.** The action line is split on `:`, so a payload with its own `MSG=` and a colon
   silently does nothing. Three buttons shipped with that bug in August 2026.
6. **Every button label names its lane and its verb** (`T2 park`, never `Park`). On the touchscreen
   the label is the *only* context; the text line is one line and it is about something else.
7. **A destructive button never shares a group row with its safe counterpart.** Row one is the safe
   actions, row two is the destructive ones. Always the same way round.
8. **A dialog that names a command in its text must offer that command as a button in the same
   dialog.** The dialog is already open and already knows the tool number.
9. **Never ask what the sensors answer.** Derive it, state the conclusion, and prompt only for a
   genuine decision — two clear options, each saying what it will do.
10. **Always `prompt_end` before `prompt_begin`,** or the dialog never renders and the lines land in
    the console as raw text.

### 6.3 Worked example: `ACE_LANES` rewritten

**What it does today** (`ace_ui.cfg:20-128`, read live). The idle branch emits **10 or 11
`prompt_text` lines**, four eject buttons, an optional park button, and three footer buttons. On
KlipperScreen the operator sees the *last* line — typically `T3: empty` — above **four destructive
EJECT buttons and nothing to choose between them.** There is no Load button anywhere in the dialog
built for lane actions, and the jam line prints the string `ACE_BUFFER_DISARM` instead of offering it.

**And the busy branch crashes.** `ace_ui.cfg:48` reads
`printer['gcode_macro ACE_LANE_EJECT'].steps`. `ACE_LANE_EJECT` declares only `tool`, `running` and
`do_home` (`ace_unload.cfg:78-82`); `steps` was removed with the macro-side chunk loop on 2026-08-27
and the UI reference was not. The Jinja render raises `UndefinedError`, so **the dialog does not
appear at all while an eject is running — the one state where its Abort button is the point.** The
same dangling reference sits in `ace_unload.cfg:110`'s refusal message. Fixing both is part of this
rewrite, not a follow-up.

**The rewrite.** Three text lines, ending with the decision. Two button rows, safe then destructive.
Lane state moves into the labels, where the touchscreen can see it.

```
    RESPOND TYPE=command MSG="action:prompt_end"
    RESPOND TYPE=command MSG="action:prompt_begin ACE Lanes"

    # Line 1 - all four lanes on one line. Web reads it; the touchscreen discards it.
    RESPOND TYPE=command MSG="action:prompt_text T0 empty · T1 at gate · T2 staged 884/934mm · T3 empty"

    # Line 2 - the shared path and the sensors, including any that are switched OFF.
    RESPOND TYPE=command MSG="action:prompt_text path clear · toolhead empty · entry sensor OFF · postgear no"

    # Line 3 - THE DECISION. Priority ladder below. This is the only line the touchscreen shows.
    RESPOND TYPE=command MSG="action:prompt_text T2 is staged 50mm short of the hub. Cold pull is legal."

    # Row 1: safe actions, one per lane, label carries the state.
    RESPOND TYPE=command MSG="action:prompt_button_group_start"
    RESPOND TYPE=command MSG="action:prompt_button T0 empty|ACE_LANES|secondary"
    RESPOND TYPE=command MSG="action:prompt_button T1 load|T1|primary"
    RESPOND TYPE=command MSG="action:prompt_button T2 retract|ACE_LANE_PARK T=2 FORCE=1|warning"
    RESPOND TYPE=command MSG="action:prompt_button T3 empty|ACE_LANES|secondary"
    RESPOND TYPE=command MSG="action:prompt_button_group_end"

    # Row 2: destructive and recovery. Never mixed with row 1.
    RESPOND TYPE=command MSG="action:prompt_button T1 eject|ACE_LANE_EJECT T=1|error"
    RESPOND TYPE=command MSG="action:prompt_button T2 eject|ACE_LANE_EJECT T=2|error"
    RESPOND TYPE=command MSG="action:prompt_button Audit|ACE_AUDIT|info"
    RESPOND TYPE=command MSG="action:prompt_button STOP ALL|ACE_STOP_ALL|error"

    RESPOND TYPE=command MSG="action:prompt_footer_button Refresh|ACE_LANES|info"
    RESPOND TYPE=command MSG="action:prompt_footer_button Close|PROMPT_CLOSE|secondary"
    RESPOND TYPE=command MSG="action:prompt_show"
```

**The priority ladder for line 3.** First match wins. This is the whole design of the dialog: the
operator always reads one line, and it is always the most urgent true thing.

| # | condition | line 3 |
|---|---|---|
| 1 | `JAM` | `JAM latched on T{jam_tool}. Clear it before anything moves.` |
| 2 | `IMPOSSIBLE` | `FAULT: post-gear sees filament, entry does not. Broken strand or a dead switch — move nothing.` |
| 3 | `EJECTING` | `Eject running on T{EJECT_TOOL}. Abort it, or wait.` |
| 4 | `AT_TOOLHEAD and CUR == -1` | `Filament at the toolhead that no lane claims. Force-eject only once you can see whose it is.` |
| 5 | `not NO_OP` | `Open intent: {ace_op}. Recover before moving filament.` |
| 6 | `TGT != -1` | `Toolchange T{CUR}→T{TGT} never finished. Reconcile it before printing.` |
| 7 | `AT_TOOLHEAD and not HOT and not PARKED` | `Sensors show filament but the state says unloaded. Audit before moving anything.` |
| 8 | `CUR >= 0 and HOT` | `T{CUR} is loaded, tip in the melt zone. Park to swap; eject to change the spool.` |
| 9 | `CUR >= 0` | `T{CUR} is loaded and parked cold. A cold pull is legal.` |
| 10 | `any STAGED(i)` | `T{i} is staged {gap}mm short of the hub. Cold pull is legal.` |
| 11 | `any PRESENT(i)` | `Nothing loaded. T{i} is at the gate and ready to load.` |
| 12 | else | `No filament in any lane. Insert a spool, then Load.` |

Rows 2, 5, 6 and 7 all end in *stop and check*, and none of them exists in the dialog today.

**Buttons are built dynamically**, four per row maximum:

- Row 1, per lane: `T{i} load` when `Load` is enabled · `T{i} park` when loaded and at the toolhead ·
  `T{i} retract` when staged · `T{i} empty` (secondary, re-renders the dialog) otherwise.
- Row 2: `T{i} eject` for each ejectable lane (or `T{i} force` in the orphan case), then `Audit`, then
  `STOP ALL` — trimmed from the right if more than four are needed, because `STOP ALL` and `Audit`
  yield to a lane that genuinely needs ejecting.
- **When the ladder returns row 1 or 2** (jam, impossible pair) the rows collapse to exactly two
  buttons: `Clear jam on T{n}` / `Audit`, or `Audit` / `Query sensors`. In a fault the dialog stops
  offering choices and offers the next step.

Net effect: **10–11 text lines → 3; the touchscreen goes from one arbitrary line and four destructive
buttons, to one decisive line and a safe row above a destructive row.** And `Load` exists.

---

## 7. Sized and sequenced

Ordered so the panel becomes **clickable** before it becomes handsome. The earlier ordering put the
visual language third and the buttons fourth; that was written before the owner said the panel is
"passive, so little value add to open it". Controls now lead, and the styling arrives with them
rather than ahead of them.

| # | Slice | Size | What he sees after it |
|---|---|---|---|
| **0** | Change the ACE Panel webcam entry's `aspect_ratio` from `4:3` to `3:2`, and add the `.theme/navi.json` sidebar entry (§5.0, §5.3) | **XS** | The dashboard card it is **already registered as** gets a shape that fits four lane tiles side by side, plus a sidebar route to the full-width view. Two settings changes, no code. |
| **1** | **The action rail** (§2.12) bolted onto the *existing* panel, with the command manifest and the §4.3 predicates | **S–M** | **The first thing he can click.** Every message that says "run `MMU_RECOVER GATE=2`" becomes that button, with the sentence beside it. This lands before any restyling and it is the whole point of the panel. |
| **2** | Lane controls: Load / Park / Retract / Eject / Eject (force) per lane, plus the recovery bar (`Audit`, `Recover`, `Clear op`, `Unlock`, `Reconcile`, `Clear suppression`) — with verbatim disabled reasons | **L** | **The console stops being necessary for the common path.** Every disabled control says why. `_ACE_SUPPRESSION_DISARM` stops being invisible. |
| **3** | `ACE_STOP_ALL` (§4.5) + the `ACE_LANES` rewrite (§6.3), including the `.steps` crash that stops the dialog rendering during an eject | **S** | One control that stops everything, on both surfaces. The touchscreen dialog gains a Load button and one decisive line, and it appears during an eject instead of crashing. |
| **4** | Truth pass: read `ace_staged_mm`; delete `laneState()`'s "in ACE, not fed"; fix the shared-run owner; draw entry=0/post=1 as a fault; render `enabled:false` as `sensor off`; drop `lane_empty` | **S** | The panel stops contradicting itself about lane 2, and the controls above stop being gated on a lie. |
| **5** | The visual language: tokens, type, spacing, theme toggle, header + dryer strip, lane tiles as objects, hand-fed strip, the redrawn path diagram **as a lane selector** | **M** | **The panel looks like the mockup** — arriving on a surface that already does things. This is the "something nice done" slice, and it is fifth on purpose. |
| **6** | Dryer and dry-roll controls; the dryer prose is deleted, not moved | **S** | Drying starts and stops from the panel, in one 44 px strip instead of a quarter of the page. |
| **7** | Patcher hardening: distinct return values, non-zero exit, `status.json`, corrected docstring — plus the panel's health pill (§5.3) | **XS** | A red pill when a Mainsail update breaks the nozzle dot, instead of fifteen silent minutes. |
| **8** | Spool picker → `MMU_GATE_MAP` (§2.9) | **M** | Assigning a spool is a list with swatches and weights, not a command line named in four error messages. |
| **9** | KlipperScreen sufficiency pass: the §3.3 screen — either by fixing `acepro.py` (the `TR` command that does not exist, the dead `ace_pro_control` lock) or by building it fresh | **M** | **The machine gains a control panel at all.** Scoped to completeness, not richness. |
| **10** | `manifest.webmanifest` + icon in `/ace/` (§5.4) | **XS** | An ACE icon on the phone home screen that opens standalone. |
| **11** | Stub-or-hide pass on the 13 unimplemented `MMU_*` commands the Mainsail card sends (chapter 11 §2.9) | **M** | The Happy-Hare card stops offering buttons that error, and stops offering one that fails silently. |

Slices 0–1 are a single session, and slice 1 alone changes the panel's reason to exist. Slice 5 is
the one that answers "I really insist on having something nice done" — deliberately after the panel
has something to be nice *about*.

---

**Ends.** The rendered proof is at [`examples/ace-panel-mockup/index.html`](../examples/ace-panel-mockup/).
