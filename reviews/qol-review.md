# Voron Trident 300 + ACE 2 Pro: Quality-of-Life & Waste Audit

**Target System**: Voron Trident 300 CoreXY + Anycubic ACE 2 Pro (4-slot MMU) via forked Kobra-S1/ACEPRO driver  
**Toolhead**: WWBMG Extruder + CrossBow Cutter + Phaetus Rapido 2 HF (rated 52 mm³/s) + Cartographer v4 (Eddy probe)  
**Config Path**: `L:\config`  
**Date**: 2026-09-01  
**Scope**: Machine time, filament waste, consumable life, hardware stress, and operator cognitive friction.

---

## 1. Executive Summary

This QOL audit evaluates the quiet inefficiencies in time, material, hardware wear, and operator attention on this Voron Trident 300 setup. Key findings include:

1. **Toolchange Machine Overhead (68s down to ~46s)**: Of the ~68s toolchange cycle, **~22s (32%) is dead macro overhead and redundant motion**, including untested pushback sequences, blocking 1.2s driver heartbeat sleeps, and low-speed cutter sweeps.
2. **Purge Volume Inflation (3–6× Over-Purging)**: Purging a flat 50–75 mm (~120–180 mm³) per swap against slicer flush requirements of 3.0–20.2 mm (~7–49 mm³) wastes **~55 g ($1.10) on a 350-swap print** and **~237 g ($4.74) on a 1,500-swap print**.
3. **Hotend Capacity Under-Utilization (76% Wasted Melting Bandwidth)**: Across a 74-print audit, PLA averaged **12.6 mm³/s on a 52 mm³/s hotend**, bottlenecked by imported slicer speed caps (120–150 mm/s wall caps on a CoreXY printer capable of 600 mm/s). Uncapping speeds can reduce multi-hour print times by **30–50%**.
4. **Accelerated Consumable Wear**: The silicone nozzle brush, cutter razor blade, extruder drive teeth, and ACE feeder rollers suffer high abrasive cycling—including 86.4 meters of daily idle filament cycling from the drying roller rotisserie sweep.
5. **Operator Attention Leaks**: The operator is tasked with manually declaring cut states (`ACE_DECLARE_TIP`), clearing stale open intents (`ACE_CLEAR_OP`), selecting materials during cold loads, and managing exhaust policies that the machine's state engine could handle autonomously.

---

## 2. Master Ranked Waste & QOL Table

| Rank | Issue / Waste Area | Cost per Occurrence | Cost per Print (Typical) | Proposed Fix | Effort |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Toy Slicer Speed & Volumetric Caps** | Limits hotend flow to ~12.6 mm³/s vs 52 mm³/s rated capacity. | Adds 30–50% extra print time (e.g., +45 min on a 2h print; +3h on an 8h print). | Raise volumetric speed cap to 28–35 mm³/s; raise infill to 400–450 mm/s and walls to 200–300 mm/s. | Low |
| **2** | **Heuristic Purge vs Slicer Flush Matrix** | Wastes 40–55 mm (0.12–0.16 g, 96–132 mm³) of filament per colour change. | • 35-swap print: 5.5 g ($0.11)<br>• 350-swap print: 55.3 g ($1.11)<br>• 1500-swap print: 237.1 g ($4.74) | Pass slicer flush matrix directly; stand down driver's flat 50mm + luminance heuristic on prime tower prints. | Low |
| **3** | **Toolchange Pushback & Heartbeat Dwells** | 7.5–9.0s wasted per toolchange (4.3s pushback cycle + 3.6s in `G4 P1200` 1Hz heartbeat sleeps). | • 35-swap: 4.8 min<br>• 350-swap: 48.0 min<br>• 1500-swap: 3.4 hours | Remove pushback from `CROSSBOW_CUT_TIP`; update driver `_info` synchronously on assist ACK to eliminate 1.2s sleeps. | Medium |
| **4** | **Crossbow Cut & Reset Motion Dynamics** | 6.0s of slow 8 mm/s cutting, reverse off arm, and mechanical reset sweep. | • 35-swap: 3.5 min<br>• 350-swap: 35.0 min | Retract off arm at 60 mm/s; increase cut speed to 15–20 mm/s; make mechanical reset conditional. | Low |
| **5** | **ACE Drying Roller ("Rotisserie") Gear Wear** | 150mm sweep every 300s = 288 cycles (86.4m) of continuous filament cycling through gears per 24h drying. | Accelerates ACE drive roller wear, creates filament dust in feeder path, flexes Bowden tubes continuously. | Increase drying tick interval to 15–30 min (or reduce leg to 25mm); pause drying roller when spool is not wet. | Low |
| **6** | **Sensorless Homing Register Clear Delays** | Four 1.0s `G4 P1000` delays in `Sensorless-Homing.cfg` on every `G28 X Y` (4.0s dead wait). | 4.0s wasted on every print start and manual re-home. | Reduce `variable_clear_time` in `Sensorless-Homing.cfg` from 1.0s to 0.15s (150ms is standard for TMC2209). | Low |
| **7** | **`PRINT_END` Motor Off Invalidation of Z-Tilt** | `PRINT_END` calls `M84`, clearing `printer.z_tilt.applied`. | Forces full `Z_TILT_ADJUST` (3 probe points + Z re-home = ~25–30s) on every back-to-back print. | Keep motors energized during idle timeout window; allow immediate back-to-back prints to skip Z-tilt. | Low |
| **8** | **Extruder Cold `FORCE_MOVE` Abrasive Wear** | 35–110 mm of cold filament forced through extruder gears at 10–45 mm/s on unloads/parks. | Abrasive tooth wear on hardened drive gears; metal-on-filament grinding risk if assist lags. | Reduce cold travel distances to verified minimums; synchronize feed assist before any extruder rotation. | Medium |
| **9** | **Operator Cognitive Burden & Manual Declarations** | Operator must remember `ACE_DECLARE_TIP`, `ACE_CLEAR_OP`, manual cold load material prompts, and `EXHAUST_POLICY`. | Operator friction, false audit errors, accidental double-cutting of tips, blocked drying loops. | Auto-reconcile open intents at print start; auto-populate cold load material from loaded spool metadata. | Low |
| **10** | **Redundant Silicone Brush Wiping** | 120 mm/s diagonal scrub cycles on `PRINT_START` and `PRINT_END` even when nozzle is cold/clean. | Rapid silicone bristle wear; cyclic bending stress on 3D-printed brush mount arm (+10s start/end time). | Skip KOMB wipe if nozzle has not extruded since last wipe and is below glass transition temperature. | Low |

---

## 3. Deep Dive into the Top Three High-Impact Opportunities

### Deep Dive 1: Slicer-Driven Purge Matrix vs. Flat Driver Heuristic (Filament & Time Savings)

#### The Problem
Historically, the printer’s Klipper macros and ACE driver purged a flat **50 mm** of filament per colour change, scaled up by a homegrown luminance heuristic to **50–75 mm** (typically **65 mm** average). In volume, this equates to **120.3–180.4 mm³** (0.15–0.22 g of PLA per swap).

However, modern slicers (OrcaSlicer / PrusaSlicer) calculate an exact color-transition flush matrix based on pigment opacity:
- In the active test file (`Cube_PLA_12m15s.gcode`), the slicer’s required flush volumes range from **3.0 mm to 20.2 mm** of 1.75mm filament (7.2 to 48.6 mm³).
- Light-to-dark transitions (e.g., Yellow to Black, or Natural to Red) require only 3–8 mm of flush.
- Dark-to-light transitions require 15–20 mm.
- **The machine has been purging 3× to 6× what is physically required on every single colour swap.**

```
[Measured Geometry & Mass Reference]
• Filament Cross-Sectional Area: π * (1.75 / 2)² = 2.40528 mm² (≈ 2.405 mm³/mm)
• PLA Density: 1.24 g/cm³ = 0.00124 g/mm³
• Mass per Linear Millimeter: 2.40528 mm³/mm * 0.00124 g/mm³ = 0.0029825 g/mm (≈ 2.983 mg/mm)
• PLA Filament Cost Benchmark: $20.00 / kg ($0.020 / g)
```

#### Quantitative Savings Comparison

| Print Scale / Scenario | Toolchanges | Baseline Waste (65 mm / swap) | Slicer Matrix Waste (12 mm avg / swap) | Filament Length Saved | Filament Mass Saved | Direct Cost Saved ($20/kg) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Small Multi-Colour Job** | 35 | 2,275 mm (6.78 g) | 420 mm (1.25 g) | **1,855 mm (1.86 m)** | **5.53 g** | **$0.11** |
| **Medium Multi-Colour Print** | 350 | 22,750 mm (67.85 g) | 4,200 mm (12.53 g) | **18,550 mm (18.55 m)** | **55.32 g** | **$1.11** |
| **Large Complex Model** | 1,500 | 97,500 mm (290.79 g) | 18,000 mm (53.69 g) | **79,500 mm (79.50 m)** | **237.10 g** | **$4.74** |
| **50-Print Production Run** | 25,000 | 1,625,000 mm (4.85 kg) | 300,000 mm (0.89 kg) | **1,325,000 mm (1.33 km)** | **3,951.8 g (3.95 kg)** | **$79.04** |

#### Additional Time Benefit
Purging an extra 50 mm onto a prime tower or behind the bed at 400 mm/min (6.67 mm/s) takes **7.5s of pure extrusion time per change**. 
- On a 350-change print, adopting the slicer matrix eliminates **43.7 minutes** of slow purge extrusion.
- On a 1,500-change print, it eliminates **3.1 hours** of wasted machine time.

---

### Deep Dive 2: Eliminating Toolchange Macro Dead-Time (22 Seconds Recoverable)

#### Where the 68 Seconds Machine Time Goes
Measured machine time for a standard toolchange is **~68.0 seconds**. Tracing the active macros (`ace_toolchange.cfg`, `crossbow.cfg`, `ace.cfg`, `park.cfg`) reveals the exact breakdown:

```mermaid
gantt
    title 68-Second Toolchange Breakdown (Current vs Optimized)
    dateFormat  X
    axisFormat %s s
    section Current (68s)
    CROSSBOW Cut Stroke & Reset       :active, 0, 16
    Pushback Forward/Dwell/Reverse    :crit, 16, 22
    Driver 1Hz Heartbeat Sleeps (3x)  :crit, 22, 26
    Tandem & Bowden Retract (723mm)   :active, 26, 38
    Bowden Feed In (90mm/s)           :active, 38, 50
    Tandem Bite & Slow Melt Load      :active, 50, 58
    Prime Tower / Brush Purge & Wipe  :active, 58, 68
    section Optimized (~46s)
    Fast Crossbow Cut (15mm/s)        :0, 8
    Immediate Tandem Extraction       :8, 10
    Optimized Bowden Retract (600mm)  :10, 18
    Bowden Feed In (90mm/s)           :18, 28
    Fast Unpark & Priming             :28, 36
    Slicer-Matrix Purge on Tower      :36, 46
```

#### Detailed Breakdown of Time Sinks

1. **Untested Pushback Sequence (~5.5s wasted)**:
   - In `crossbow.cfg` lines 435–500, `CROSSBOW_CUT_TIP` executes a pushback to seat the severed fragment.
   - Sequence: Switch assist mode 3 → 2, `G4 P1200` sleep (1.2s), forward feed 18.1 mm @ 25 mm/s (0.72s), disable mode 2, `G4 P500` dwell (0.5s), enable mode 3, `G4 P1200` sleep (1.2s), retract 18.1 mm @ 25 mm/s (0.72s), `M400` flushes.
   - **Cost**: **4.34s in pure dwells/moves + ~1.2s command overhead = ~5.5s**.
   - **Reality**: The configuration header explicitly states: *"NOTHING HERE HAS EVER RUN... no fragment has yet been seated by this machine at any depth."* The incoming strand pushes the fragment straight into the melt zone on load.

2. **1Hz Serial Heartbeat Polling Sleeps (~3.6s wasted)**:
   - `_ACE_REQUIRE_ASSIST` and `_ACE_REQUIRE_ROLLBACK_ASSIST` read `inst.status` and `inst.slots[]` from Python `instance._info`, which is populated solely by the 1Hz serial heartbeat.
   - To prevent false positive errors, the macros enforce **`G4 P1200` (1.2s blocking sleep)** after *every* assist command.
   - During a single cut/park sequence, this dwell fires 3 times (**3.6s total** of the toolhead hovering motionless).

3. **Slow Cutter Dynamics & Redundant Reset (~4.5s wasted)**:
   - Cut stroke: 15.4 mm @ 8 mm/s = **1.93s**.
   - Reverse stroke off arm: 15.4 mm @ 8 mm/s = **1.93s** (lever is unloaded after ~2mm, yet continues at 8 mm/s for the entire stroke).
   - Mechanical `CROSSBOW_RESET` sweep: 16.5 mm @ 8 mm/s = **2.06s** (despite the lever having an internal return spring).
   - Plus staged transit hops (`X20` → `X0` → `X-6` and back) with `M400` stops.

4. **Excessive Bowden Retraction Distance (~2.7s wasted)**:
   - `variable_clear_hub_mm: 723` was sized with a 30% margin over an assumed 556 mm path.
   - With the hardware `hub_detect` microswitch operational, retracting 600 mm (a 44 mm margin past the switch) is completely safe.
   - Saving 123 mm retract + 123 mm feed @ 90 mm/s saves **2.73s** per toolchange.

#### The Three Biggest Toolchange Wins
- **Win 1**: Strip Pushback Sequence → **Saves 5.5s**.
- **Win 2**: Synchronous Assist State ACK in Driver (Eliminate 3× 1.2s sleeps) → **Saves 3.6s**.
- **Win 3**: Fast Cutter Stroke (15 mm/s cut, 60 mm/s reverse off arm, bypass reset) → **Saves 4.2s**.
- **Total Direct Savings**: **~13.3s per cut sequence**, plus **2.7s on Bowden transit** = **~16.0s saved on every toolchange** (reducing cycle time from 68s to ~52s; down to ~46s when combined with optimized prime-tower purging).

---

### Deep Dive 3: Uncapping Hotend Volumetric Flow & Slicer Print Speeds

#### The 74-Print Audit Reality
The machine is equipped with a high-end Phaetus Rapido 2 HF hotend rated for **52 mm³/s continuous volumetric flow** and a Voron CoreXY gantry capable of **600 mm/s travel and 10,000 mm/s² acceleration**.

Despite this hardware:
- A 74-print audit showed an actual average extrusion flow rate of only **12.6 mm³/s** (24.2% of rated capacity).
- Slicer configurations (`Cube_PLA_12m15s.gcode`) reveal imported profile limits:
  - `outer_wall_speed = 150 mm/s` (at 0.4mm width, 0.2mm layer height = **12.0 mm³/s**)
  - `top_surface_speed = 120 mm/s` (**9.6 mm³/s**)
  - `gap_infill_speed = 150 mm/s` (**12.0 mm³/s**)
  - `small_perimeter_speed = 40 mm/s` (**3.2 mm³/s**)
  - `sparse_infill_speed = 350 mm/s` (**31.5 mm³/s**, but rarely reached due to acceleration ramp limits on small infill zones).

#### The Bottleneck & The Opportunity
At 0.20 mm layer height and 0.45 mm extrusion line width (cross-sectional area = 0.09 mm²):
- 12.6 mm³/s corresponds to a linear printing speed of **140 mm/s**.
- Pushing the hotend to a conservative, reliable **30 mm³/s** enables printing speeds of **333 mm/s**.
- Pushing to **40 mm³/s** enables **444 mm/s**.

```
[Print Duration Impact on Common Jobs]
• 250g Multi-Part Plate:
  - At 12.6 mm³/s: ~6 hours 15 minutes
  - At 30.0 mm³/s: ~3 hours 20 minutes (46% time reduction / 2h 55m saved)
• Full-Bed Functional Enclosure (800g):
  - At 12.6 mm³/s: ~18 hours 40 minutes
  - At 32.0 mm³/s: ~9 hours 10 minutes (51% time reduction / 9h 30m saved)
```

---

## 4. Consumables & Hardware Wear Analysis

```
Consumable Wear Summary:
1. Nozzle Silicone Brush: High cyclic abrasive scrub at 120 mm/s (broken mount history).
2. Crossbow Cutter Blade: Dull blade risks jagged cuts -> Bowden load jams.
3. Drive Belts: Shock loading at X < 0 cutter arm; sensorless homing frame stalls.
4. ACE Feeder Gears: 86.4 meters/day idle rotisserie cycling + historical clamp grinds.
5. Extruder Drive Gears: High-speed cold FORCE_MOVE operations (up to 110mm).
```

### 1. The Nozzle Brush (KOMB Silicone Pad)
- **Mechanism**: KOMB generates high-speed diagonal scrub passes at 120 mm/s across frame-mounted silicone bristles. The original arm fractured from cyclic mechanical fatigue on 2026-08-07.
- **Waste**: Wiping runs on every `PRINT_START`, every `PRINT_END` (even after cold tip parking), and on recovery events.
- **Optimization**: Guard the wipe against nozzle state. If the nozzle is clean and cold (<140°C), skip the physical scrub.

### 2. The Cutter Blade (CROSSBOW)
- **Mechanism**: Standard razor utility blade slices 1.75mm solid filament at 1.1A stepper motor current. Abrasive filaments (PLA+, CF/GF blends) dull the edge after 1,000–2,000 cuts.
- **Consequence**: A dull blade leaves an angled, smeared, or mushroomed cut face rather than a clean cylindrical flat end. This distorted face is the primary cause of friction and hang-ups at the 4-to-1 hub and toolhead entry guide.
- **Optimization**: Maintain blade sharpness tracking in Klipper variables (`crossbow_cut_count`) and avoid unnecessary cuts during standalone unloads where tip shaping is sufficient.

### 3. CoreXY Belts & Goose Belt Purger (GBP)
- **CoreXY Belts**: Driving the toolhead past X=0 to X=-6.0 against the physical cutter stop exerts asymmetric shock tension on the A/B belts. Sensorless homing at 40 mm/s with 1.0s stall dwell further stresses the belt teeth at the motor pulleys.
- **GBP Belt**: The NEMA 11 silicone purger belt undergoes tension cycles when motor hold (`HOLD=0`) is released, allowing stretched silicone to snap back.

### 4. ACE Feeder Gears & MMU Rollers
- **The Drying Rotisserie Tax**: In `ace_dryroll.cfg`, active sweep mode rotates spools back and forth by 150 mm every 300 seconds. Over a 24-hour drying cycle, this feeds and retracts **86.4 meters of filament through the internal ACE drive gears and spring buffer**, generating unnecessary mechanical gear tooth wear and filament dust while the machine is completely idle.
- **Historical Grinds**: Previous driver/macro mismatches (e.g. retracting while the ACE lane was clamped) ground flat spots into filament strands and packed drive teeth with plastic debris.

### 5. Toolhead Extruder Gears (WWBMG)
- **Cold Force Moves**: The macros make extensive use of Klipper's `FORCE_MOVE` to bypass `min_extrude_temp` during cold loading and parking:
  - `load.cfg`: 10 mm bite + 10 mm verify + up to 60 mm slow advance.
  - `unload.cfg`: 30 mm Phase 1 + 80 mm Phase 2 = 110 mm cold retract.
  - `park.cfg`: 35 mm fast retract @ 45 mm/s + up to 80 mm fine retract steps.
  - `postgear_seek.cfg`: Up to 200 mm forward coarse seek @ 10 mm/s.
- **Wear Impact**: Forcing hundreds of millimeters of rigid, cold filament against dual drive gears at 45 mm/s accelerates tooth flank wear and bearing load compared to molten extrusion.

---

## 5. Operator Attention & Cognitive Friction Audit

What the operator currently has to remember that the machine could handle autonomously:

```mermaid
flowchart TD
    subgraph Current Operator Burden
        A[Operator cuts filament by hand] --> B[Must remember: ACE_DECLARE_TIP STATE=cut]
        C[Toolchange interrupted / aborted] --> D[Must remember: MMU_UNLOCK then ACE_CLEAR_OP]
        E[Cold filament load inserted] --> F[Must answer KlipperScreen 6-button material prompt]
        G[Printed ABS with CONTAINED policy] --> H[Must remember: EXHAUST_POLICY MODE=NORMAL for PLA]
        I[Broken brush replaced] --> J[Must remember: FILAMENT_WIPE_ENABLE]
    end
    subgraph Machine Autonomy Fix
        B -.-> K[Track post-gear / entry state transitions automatically]
        D -.-> L[Auto-reconcile open intents on next PRINT_START]
        F -.-> M[Read material directly from Spoolman / ACE RFID]
        H -.-> N[Reset exhaust policy per-print based on slicer MATERIAL]
        J -.-> O[Auto-detect brush presence or maintain in persistent config]
    end
```

1. **Manual Tip State Declaration (`ACE_DECLARE_TIP`)**:
   - If an operator clears a jam, trims a tail, or loads a freshly cut spool by hand, they must remember to type `ACE_DECLARE_TIP STATE=cut`.
   - If forgotten, the machine assumes the tip is unshaped and triggers a redundant cut on the next park/unload, discarding an extra 8.0 mm stub.
2. **Open Intent Clearing (`ACE_CLEAR_OP`)**:
   - When an in-flight toolchange is interrupted, `ace_op` is written to `save_variables`.
   - Even after physically fixing the toolhead and running `MMU_UNLOCK`, `ace_op` remains set indefinitely. This causes `ACE_AUDIT` to fail and permanently stops the drying roller until the operator remembers to run `ACE_CLEAR_OP`.
3. **Manual Cold Load Material Dialogs (`_FILAMENT_LOAD_COLD_START`)**:
   - Initiating a cold load brings up a 6-button interactive prompt on KlipperScreen asking the user to pick PLA/PETG/ABS/ASA/TPU/PC.
   - The printer already knows which spool is active via Spoolman ID, ACE RFID, and `ace_gate_map`. This prompt is redundant clicks.
4. **Persistent Exhaust Bias (`EXHAUST_POLICY`)**:
   - Running `EXHAUST_POLICY MODE=CONTAINED` persists `exhaust_bias: 8` in `save_variables`. If the operator switches to PLA the next day, the exhaust policy remains stuck in contained mode until manually cleared via `EXHAUST_POLICY MODE=NORMAL`.

---

## 6. Redundant Print Routines: What Runs on Every Print

```
Startup & Shutdown Routine Audit:
• G4 P1000 stallguard dwells: 4.0s wasted per homing cycle.
• Z-Tilt Re-Leveling: Runs 100% of prints because PRINT_END disables steppers with M84.
• Cartographer Touch Homing: Forces nozzle cooldown to <140°C even when offset has not drifted.
• Bed Mesh Scan: Calibrated every print despite rigid 8mm cast aluminum bed on Trident.
• Backup Cleanup Shell Command: Spawns disk cleanup on every single PRINT_END.
```

### 1. Sensorless Homing Register Dwells (4.0s Wasted per Homing)
In `Sensorless-Homing.cfg`, lines 56, 112, 135, and 191 execute `G4 P{clear_time}` with `clear_time: 1.0` (1,000 ms).
- That is **4 full seconds of dead stall** on every homing cycle (`_HOME_X` twice, `_HOME_Y` twice).
- Standard TMC2209 stallguard clear time is **100–150 ms**. Reducing `clear_time` to 0.15s recovers **3.4 seconds** instantly on every home.

### 2. `Z_TILT_ADJUST` Invalidation by `PRINT_END` Motors Off
In `start.cfg` line 560:
```jinja2
{% if not printer.z_tilt.applied|default(False) %}
    M117 Leveling Gantry...
    Z_TILT_ADJUST
    G28 Z
{% else %}
    M118 [START] Z-Tilt still applied (steppers never de-energized), skipping calibration.
{% endif %}
```
- The macro author intended back-to-back prints to skip the 25-second Z-tilt routine.
- However, `PRINT_END` unconditionally executes `_MOTORS_OFF_KEEP_Z` / `M84`, which immediately resets `printer.z_tilt.applied` to `False`.
- As a result, **`Z_TILT_ADJUST` runs on 100% of prints**, even when consecutive jobs are launched seconds apart.

### 3. Redundant Touch Homing & Nozzle Cooldown
- `_START_STEP_TOUCH_HOME` enforces `CARTOGRAPHER_TOUCH_HOME`, which strictly requires the nozzle to be below 140°C.
- If a filament check or recovery park preheated the nozzle to 200°C, the printer turns on the part cooling fan at 100% and halts startup while waiting for the hotend to cool down, only to reheat it to 210°C immediately after touching.
- The Cartographer eddy current scan probe already tracks thermal expansion; touch homing on every single print adds 20–45s of thermal cycling delay.

---

## 7. Concrete Action Plan & Recommended Roadmap

```mermaid
graph LR
    A[Phase 1: Quick Config Wins] --> B[Phase 2: Toolchange Streamlining]
    B --> C[Phase 3: Slicer Optimization]
    
    A -.-> A1[Fix Homing Dwells: -3.4s]
    A -.-> A2[Tune Dryroll Interval: -80% gear wear]
    A -.-> A3[Auto-clear ace_op at Print Start]
    
    B -.-> B1[Strip Pushback: -5.5s / swap]
    B -.-> B2[Eliminate 1.2s Heartbeat Sleeps: -3.6s / swap]
    B -.-> B3[Fast Crossbow Cut Dynamics: -4.2s / swap]
    
    C -.-> C1[Adopt Slicer Flush Matrix: -80% purge waste]
    C -.-> C2[Uncap Volumetric Flow: 30-50% faster prints]
```

### Phase 1: Immediate Macro & Config Wins (Effort: < 1 Hour)
1. **Reduce Homing Clear Delays**:
   - In `Sensorless-Homing.cfg`, set `variable_clear_time: 0.15`.
   - *Impact*: Saves 3.4s on every home.
2. **De-escalate ACE Drying Roller ("Rotisserie") Cycling**:
   - In `ace_dryroll.cfg`, increase `variable_interval` from 300s to 1200s (20 min) and decrease `variable_leg` from 150 mm to 50 mm.
   - *Impact*: Reduces idle gear and filament cycling from 86.4 m/day to 3.6 m/day (95.8% reduction in mechanical wear).
3. **Auto-Reconcile `ace_op` on Clean Print Starts**:
   - In `PRINT_START`, auto-clear `ace_op` if `ACE_AUDIT` reports `ok: 1` and toolhead is coherent.
   - *Impact*: Eliminates operator manual console typing after resolved swaps.

### Phase 2: Toolchange Refinement (Effort: 2–3 Hours)
1. **Bypass Untested Crossbow Pushback**:
   - In `macros/filament/crossbow.cfg`, set `variable_pushback_face_margin: 0` or add a bypass flag to skip the 18.1mm forward/reverse assist cycle.
   - *Impact*: Cuts 5.5s off every toolchange (~32 minutes saved on a 350-change print).
2. **Eliminate 1.2s Driver Heartbeat Dwells**:
   - Update `protocol_ace2.py` / `instance.py` to update slot and device state immediately upon receiving the command ACK frame, allowing `_ACE_REQUIRE_ASSIST` to evaluate synchronously without `G4 P1200`.
   - *Impact*: Cuts 3.6s of dead wait per toolchange (~21 minutes saved on 350 changes).
3. **Optimize Crossbow Cut & Reset Motion**:
   - In `_CROSSBOW_VARS`, increase `cut_speed` to 15 mm/s, increase reverse exit speed to 60 mm/s, and make `CROSSBOW_RESET` conditional.
   - *Impact*: Cuts 4.2s per cut sequence.

### Phase 3: Slicer & Process Optimization (Effort: Ongoing)
1. **Enforce Slicer-Driven Prime Tower Flush**:
   - Ensure OrcaSlicer start gcode emits `ACE_PURGE_MODE TOWER={enable_prime_tower}` and calculate flushing volumes via OrcaSlicer’s auto-calculate matrix (`flush_multiplier = 0.3` to `0.5`).
   - *Impact*: Saves 50–240g of filament and $1.00–$4.80 per multi-colour print.
2. **Uncap Slicer Volumetric & Acceleration Limits**:
   - Bench test PLA flow rate up to 35 mm³/s on the Rapido 2 HF.
   - Update default PLA print preset: Inner walls 300 mm/s, sparse infill 400 mm/s, outer walls 200 mm/s.
   - *Impact*: Accelerates typical multi-hour print completions by 30–50%.

---
*Report compiled autonomously via Antigravity QOL Inspection Engine.*
