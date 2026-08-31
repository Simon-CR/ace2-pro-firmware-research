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

---

## 3. Layouts, at real pixel dimensions

Three artboards, all present in the mockup. Grid is 12 columns everywhere; only the gutter and the
column span change.

### 3.1 Desktop / wide browser — 1440 × N, content 1240

Page: `--bg`, `padding: 24px`, content `max-width: 1240px`, centred. Grid: 12 × 82.9 px columns,
20 px gutter (`grid-template-columns: repeat(12, minmax(0,1fr)); gap: 20px`).

| # | block | span | height | contents |
|---|---|---|---|---|
| 1 | header card | 12 | 108 | row A (56 px): `ACE 2 Pro` title · `connected` pill · `fw V1.1.3W` pill · `path clear` pill · job pill · right-aligned `STOP ALL` (destructive, 36 px, 140 px wide). Row B (44 px, above a `--divider`): dryer strip (§2.7) |
| 2 | banners | 12 | 0 or 96 each | fault, then jam, then any in-flight notice. Absent when clear — they do not reserve space |
| 3 | path card | 8 | 300 | `PATH` micro-label, the SVG (§2.2) at 760 × 220, caption line, `Calibrate path` button in the caption when uncalibrated |
| 4 | toolhead card | 4 | 300 | §2.10, four sensor rows + verdict, plus `Audit` and `Query sensors` |
| 5 | lane tiles | 3 each | 268 | four §2.1 tiles across |
| 6 | hand-fed strip | 12 | 84 | §2.8 |
| 7 | action bar | 12 | 60 | `Audit` · `Recover` · `Clear op` · `Clear suppression` · `Reconcile target` · spacer · `Console` (link) |

Total above the fold at 1440 × 900: header 108 + path/toolhead 300 + lanes 268 = 676 plus gutters —
**the header, the path, the toolhead verdict and all four lane tiles are visible without
scrolling.** That is the "single pane of glass" requirement, met at the size he actually uses.

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
| 3 | path card | 236 | the SVG rotates to a **vertical rail** 366 × 180: four lanes as short horizontal stubs feeding a vertical shared run. Same rules, same sources |
| 4 | toolhead row | 76 | the four sensor dots on one row with labels beneath, verdict on a second line |
| 5 | lane tiles ×4 | 116 collapsed / 244 expanded | collapsed = swatch, `T2 · Bambu Lab Yellow PLA`, badge, `884 / 934 mm`, bar. Tap anywhere on the tile to expand the four buttons. **One tile expanded at a time**; expanding scrolls it to just below the sticky header |
| 6 | hand-fed strip | 108 | two stacked rows |
| 7 | sticky action bar | 64 + safe-area inset | `Audit` · `Recover` · `Clear op` · `⋯` (opens a sheet with the rest). `STOP ALL` is a **destructive 56 px pill pinned to the right of this bar** — always reachable, one thumb, no scroll |

Type steps up: `meta` and `reason` 12.5 → 13.5 px; `measure-xl` 30 → 26 px (the number is narrower on
a 366 px column and 30 px wraps `884 / 934 mm`). Buttons are 48 px tall, full width of their half
column (175 px), so every hit area is ≥ 48 × 48.

Landscape phone falls through to the 700–899 desktop breakpoint; it is not a separate design.

### 3.3 KlipperScreen — 800 × 480 landscape, one screen, no scrolling

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
