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
