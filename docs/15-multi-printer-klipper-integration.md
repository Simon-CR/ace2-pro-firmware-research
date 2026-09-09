# Generic Multi-Printer Klipper Integration & Configuration Guide

This guide describes how the Anycubic ACE 2 Pro integration is structured to run on **any Klipper-based 3D printer** — including custom-firmware setups such as the **Snapmaker U1**, Voron 2.4/Trident, Creality, RatRig, or custom toolhead designs.

---

## 1. Architecture: Clean Separation of Concerns

The integration is partitioned into three decoupled layers:

```
┌─────────────────────────────────────────────────────────────┐
│                       ACE 2 Pro MCU                         │
│  - FreeRTOS + Protobuf wire protocol (RS-485 / 230400 baud) │
│  - Patched stubs: autonomous sm_id injection, rawtag cache  │
│    and MIFARE Classic UID fallback (sentinel 0x0201)        │
└──────────────────────────────┬──────────────────────────────┘
                               │ Serial (USB/UART)
┌──────────────────────────────▼──────────────────────────────┐
│                    Klipper Host Driver                      │
│               (extras/ace/ in Klipper / ACEPRO)             │
│  - instance.py, manager.py, protocol_ace2.py                │
│  - ace_tag_formats.py: PURE Python tag parser (zero I/O)    │
│  - Config-driven motion geometry & sensor mapping           │
└──────────────────────────────┬──────────────────────────────┘
                               │ Moonraker Webhooks / MMU G-Code
┌──────────────────────────────▼──────────────────────────────┐
│                    Moonraker & UI Layer                     │
│  - components/filaman.py: Spoolman & gate binding           │
│  - Mainsail / Fluidd / Command Deck visualizer UI           │
│  - Emits standard MMU_GATE_MAP for slicer compatibility     │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Invariants vs. Configurable Parameters

When deploying the ACE 2 Pro on a new machine (such as the Snapmaker U1), only hardware-intrinsic protocol facts are fixed; all printer physical geometry and sensor configurations are modular.

### What is Invariant (Hardcoded by Necessity)
- **Protobuf wire protocol IDs & framing**: The ACE 2 Pro MCU command numbers (e.g. `CMD_FILAMENT_IDENTIFY = 68`, `CMD_GET_STATUS = 64`, `CMD_FEED_OR_ROLLBACK = 77`).
- **Firmware Sentinel Flags**:
  - `0x0065` (101): Native Anycubic positional decode.
  - `0x0201` (513): Raw UID tag detected (Mifare Classic / Bambu / unformatted ISO 14443-3 Type A).
  - `0x0202` (514): Raw image cached in reader buffer.
  - `0x0203` (515): Autonomous `sm_id` injected into SKU field.
- **RFID Physical & Cryptographic Constants**:
  - Bambu Lab HKDF-SHA256 salt (`9a759cf2c4f7caff222cb9769b41bc96`).
  - MIFARE Classic 1K sector/block offsets (Sector 0/1).
  - Anycubic page 4 magic bytes (`7B 00 65 00`).
  - NDEF TLV header byte (`0x03`).
- **R6 Anti-Collision Policy**: When multiple spools in the backend database share the same tag UID, Klipper strictly refuses to guess and alerts the operator.

### What is Configurable (Per Printer & Situation)

All machine-specific parameters are specified in the printer configuration file under `[ace]`:

| Config Parameter | Default | Snapmaker U1 / Custom Consideration |
| :--- | :--- | :--- |
| `filament_runout_sensor_name_nozzle` | `filament_runout_nozzle` | Name of the switch/sensor situated **before** extruder gears (e.g. `filament_sensor`). Must not be behind gears. |
| `filament_runout_sensor_name_rdm` | `None` | Pre-buffer / hub switch sensor name (if present). |
| `parkposition_to_toolhead_length` | `1000` mm | Physical distance (in mm) from the ACE 2 buffer park datum to the toolhead entry sensor. Measure carefully along the Bowden tube path! |
| `parkposition_to_rdm_length` | `150` mm | Distance from park datum to the 4-into-1 hub or splitter entrance. |
| `feed_speed` | `60` mm/s | Transport speed for long Bowden feeding (safe range: 40–90 mm/s). |
| `retract_speed` | `60` mm/s | Fast retraction speed through clear Bowden path. |
| `toolhead_slow_loading_speed` | `5` mm/s | Velocity when feeding from post-gear switch into the hotend melt zone (flow-rate limited). |
| `toolhead_retraction_speed` | `10` mm/s | Toolhead extruder retraction speed during unpark/unload. |
| `extruder_feeding_length` | `1` mm | Open-loop coordinated feed where both ACE and extruder motor turn simultaneously to catch gear bite. |
| `runout_bite_length` | `12.0` mm | Distance to advance the new filament while tail is still gripped during runout swap. |
| `runout_hug_length` | `4.0` mm | Spring-buffer assisted compression distance to close gap between old tail and new strand. |
| `rfid_temp_mode` | `average` | Calculation mode for tag printing temperature (`average`, `min`, `max`). |
| `baud` | `auto` | Auto-negotiates 230400 for ACE 2 Pro. |

---

## 3. Multi-Vendor On-Tag Extraction (Scenario 3 / Offline)

The tag decoder module (`ace_tag_formats.py`) contains pure functions that extract 100% of pertinent metadata from physical tags without requiring network access, FilaMan, or Spoolman:

1. **Bambu Lab Tags**:
   - Uses `bambu_kdf()` to derive Sector keys from UID.
   - Decodes Sector 0/1: `tray_info_idx` (`GFA00`-`GFU02`), exact material (`PLA Basic`, `PLA Matte`, `PETG Basic`), RGBA color, nozzle min/max, bed min/max, spool weight, and diameter.
   - Emits `NAME="Bambu Lab <Material>"`.
2. **Anycubic Tags**:
   - Parses pages 4..39 for SKU, brand, material, 24-bit color, and nozzle/bed min/max.
3. **OpenSpool / OpenPrintTag / OpenTag / Spoolman NFC**:
   - Parses NDEF / raw JSON payloads for brand, material, color, temps, and spool ID.
4. **Creality CFS Tags**:
   - Parses Sector 1 ASCII/binary records for `Creality`, filament type (`Hyper PLA`, `CR-PLA`, `PETG`), color, and temps.
5. **Prusament**:
   - Parses NDEF text records for `Prusament`, material, color, and temperatures.
6. **Unidentified Raw Tags**:
   - Honest fallback to `Unidentified Tag (<UID>)` with `#808080` only when zero metadata is present.

---

## 4. Filament Path Sensor Topology & Motion Invariants

Every Klipper multi-material integration must account for physical sensor distribution and kinematic constraints (such as bed-moving-in-Z machines vs gantry-moving machines).

### Physical Sensor Topology (ACE to Toolhead)

```
[ ACE Slot Entry ] ──(optical switch: CHN_INSERT_0..3)
       │
[ Feed Motor Gears ]
       │
[ Internal Buffer ] ──(mechanical deflection switches: BUF_FEED, BUF_BACK - ASSIST ONLY)
       │
[ ACE Unit Outlet ]
       │
       ▼  ~954mm 100% PASSIVE PTFE BOWDEN TUBE (ZERO PRESENCE SENSORS)
[ 4-in-1 Hub ] ───────(microswitch: hub_detect + encoder: ace_hub_encoder)
       │
       ▼  ~650mm PASSIVE PTFE BOWDEN TUBE
[ Toolhead Entry ] ───(switch: toolhead_entry)
       │
[ Extruder Gears ] ───(turning nip: passes filament only when rotating)
       │
[ Post-Gear Sensor ] ─(switch: toolhead_postgear, 5.6mm above blade)
       │
[ Cutter Blade ] ─────(Crossbow / Boomerang / mechanical cutter, 46.1mm above nozzle)
       │
[ Melt Zone ] ────────(Rapido / Volcano / Hotend, 22mm melt chamber, nozzle tip @ 0mm)
```

1. **Zero Sensors in Bowden Tube**:
   Between `CHN_INSERT_0..3` and `hub_detect`, there are **zero presence switches**. The Bowden tube is 100% passive PTFE.
2. **Buffer Switches are NOT Distance Sensors**:
   The internal ACE deflection switches (`BUF_FEED`, `BUF_BACK`) detect physical loop flex and spring tension to throttle feed/rollback assist motor speeds. They do not track distance or presence along the tube.

### Calibration & Motion Rules

1. **Bowden Calibration on Full Gate Entry Only**:
   - Bowden path calibration (`ace_path_calibrate.py`) updates the running nominal length ONLY on fresh entries from the gate (`start <= 100mm`) when the strand trips `hub_detect`.
   - Normalizing lanes already staged in the Bowden tube (`start > 100mm`) strictly re-anchors the park datum (`hub_detect - 50mm`) without adding commanded length to the calibration history. This permanently prevents PTFE creep inflation.
   - All calibration measurements are smoothed using a rolling 95th-percentile filter over the last $N=10$ samples (`ace_cal_ptfe_history`).

2. **Drying / Roasting (Dry-roll) Direction Invariant**:
   - When baking/drying spools, the dry-roll routine sweeps filament back and forth inside the tube and buffer to prevent flat spots and heat evenly.
   - **Critical Invariant:** Dry-roll sweeps must **ALWAYS begin with a retraction (away from the hub, towards the spool/buffer)**. Because motion starts backwards, an error of $\pm 100\text{mm}$ in estimated tube length is completely harmless and will never drive filament into the hub or toolhead.

3. **Cold Idle Toolchanges & Self-Toolchanges (Zero Unrequested G28)**:
   - On printers where the bed moves in Z (e.g. Voron Trident), an unrequested `G28` drives the bed toward the nozzle and risks crashing into parts or bed hardware.
   - **Self-Toolchange (`current_tool == tool_index`):** When idle and the strand is parked at post-gear (`filament_parked == 1`, `filament_loaded_hot == 0`), commanding `T<current>` confirms `ACE: Tool T{tool_index} is already active (parked at post-gear)` and returns immediately without homing or unparking.
   - **Cold-Parked Toolchange:** When switching tools while idle, an outgoing cold-parked tool sitting at `toolhead_postgear` (5.6mm above the cutter blade) is extracted cold via `_tandem_extract` (extruder motor reverse + ACE rollback). It requires **zero toolhead XY motion, zero nozzle heating, and zero Crossbow cut**. The incoming tool is fed cold to post-gear. Neither operation requires homed axes.
   - **Hot Meltzone Toolchange:** If and only if the outgoing strand is hot in the melt zone (`filament_loaded_hot == 1`), the toolhead must move to the cutter and purge bucket, which requires homed XY axes.

4. **Cutter Macro Pre-Check Invariant**:
   `CROSSBOW_CUT_TIP` evaluates absence (`not post`) and cold-parked state (`hot == 0 and svv.filament_parked == 1`) *before* demanding homed axes. If the strand is already parked and shaped in the cold zone, it skips the cut cleanly (`already parked and formed - nothing to cut, skipping`) without raising an unhomed axes exception.

---

## 5. Porting Steps for Snapmaker U1 & Custom Printers

To port this setup to the Snapmaker U1 or another Klipper printer:

1. **Copy Driver Extras**:
   Copy `extras/ace/` into your Klipper installation:
   ```bash
   cp -r /path/to/ACEPRO/extras/ace ~/klipper/klippy/extras/
   ```
2. **Measure Bowden Distances**:
   - Measure tube distance from ACE 2 Pro output buffer to the U1 toolhead filament sensor.
   - Set `parkposition_to_toolhead_length` in `printer.cfg`.
3. **Configure Toolhead Sensor**:
   - In `printer.cfg`, define:
     ```ini
     [ace]
     filament_runout_sensor_name_nozzle: <u1_entry_sensor_name>
     parkposition_to_toolhead_length: <measured_length>
     feed_speed: 70
     retract_speed: 70
     ```
4. **Enable Moonraker FilaMan / Spool Resolution**:
   - Ensure Moonraker includes the updated `filaman.py` component for automatic gate mapping and temperature feeding.

---

## 6. Dual-Topology Deployment Model: Snapmaker U1 vs. Voron Trident CM4

The ACE 2 Pro Klipper integration is architected around a dual-topology deployment model, accommodating both resource-constrained embedded printer controllers and high-performance all-in-one appliance hosts.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ SCENARIO 1: Embedded Host Offloading (e.g. Snapmaker U1)                    │
│                                                                             │
│  ┌─────────────────────────┐             ┌───────────────────────────────┐  │
│  │   Snapmaker U1 Host     │  HTTP/WS    │   External Host / Server      │  │
│  │  - Minimal Linux OS     │◄───────────►│  - multiACE Daemon (port 7126)│  │
│  │  - Klipper + Moonraker  │  MOONRAKER  │  - Web Visualizer UI          │  │
│  │  - extras/ace driver    │  _URL       │  - Heavy RFID / Spoolman sync │  │
│  └─────────────────────────┘             └───────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ SCENARIO 2: Self-Contained Appliance (e.g. Voron Trident 300 CM4)           │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │   Raspberry Pi CM4 (8GB eMMC / Lite)                                  │  │
│  │  - Klipper Host (klippy + extras/ace/ driver)                         │  │
│  │  - Moonraker API Engine                                               │  │
│  │  - Prism Touch Native (Qt 6.11 / QML appliance engine on EGLFS :0)    │  │
│  │  - multiACE Daemon (port 7126, local loopback / LAN)                  │  │
│  │  - Local Spoolman / FilaMan database sync                             │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.1 Deployment Topologies

1. **Scenario 1: Embedded Host Offloading (Snapmaker U1)**:
   - Target environment: Printers with embedded Linux SOCs, restricted RAM/CPU budgets, or vendor-locked host environments where running background Python services or web servers is undesirable.
   - Decoupled delegation: The printer host runs only Klipper (`klippy`), Moonraker, and the minimal `extras/ace` driver layer communicating over serial/UART to the ACE 2 Pro MCU.
   - Remote daemon: The multiACE daemon runs on an external server, workstation, or container, targeting the printer via `MOONRAKER_URL=http://<printer_ip>:7125`. Kinematics, Web UI rendering, and external integrations are completely offloaded from the embedded controller.

2. **Scenario 2: Self-Contained Appliance (Voron Trident 300 CM4)**:
   - Target environment: Full-featured host controllers (e.g. Raspberry Pi CM4 with 8GB RAM) operating as a stand-alone 3D printing appliance.
   - All-in-one stack: Klipper, Moonraker, the multiACE daemon (port 7126), and Prism Touch Native (Qt 6.11 / QML running directly on Linux framebuffer via EGLFS on `:0`) execute on the same host.
   - Zero-latency IPC: Fast inter-process communication occurs over local UNIX domain sockets or loopback HTTP/WebSocket connections (`127.0.0.1:7125`), ensuring deterministic state transitions and responsive touch interaction.

### 6.2 Generic Klipper Single-Extruder Adaptations

To ensure robust operation across diverse Klipper kinematics and single-extruder setups, several core adaptations have been implemented:

1. **Startup Reactor Pause Bugfix (Callback Deferral)**:
   - *Problem*: Synchronous invocation of `self.gcode.run_script_from_command()` inside `write_variables()` or `_open_ace()` during Klipper initialization or serial connect attempts to pause the reactor when it is not in active dispatch. This raises `ReactorError: Internal error - reactor pause disabled` in Klipper's `assert_no_pause()` check and leaks greenlet timer waiters.
   - *Fix*: Wrap initialization G-code dispatches inside `self.printer.get_reactor().register_callback(...)`. This defers macro and variable initialization until the event reactor loop enters active dispatch, eliminating startup crashes across all Klipper distributions.

2. **Dynamic Configuration Path Resolution (`_resolve_cfg_path`)**:
   - Instead of hardcoding paths to `~/printer_data/config` or `/home/pi/klipper_config`, multiACE uses dynamic path resolution (`_resolve_cfg_path()`).
   - Dynamically discovers configuration directories across Snapmaker U1, MainsailOS, FluiddPi, and BTT CB1 environments, preventing 404 file errors when loading macro files or writing runtime persistent variables.

3. **Dedicated Slot Park (`ACE_LANE_PARK`) and Eject (`ACE_LANE_EJECT`) Controls**:
   - Single-extruder multi-material operations require distinct separation between unparking for immediate toolchange vs. ejecting for filament replacement:
     - `ACE_LANE_PARK T={slot}`: Retracts the active strand to the cold park datum (at `toolhead_postgear` / Bowden entry), retaining the strand staged in the Bowden tube for instant reload.
     - `ACE_LANE_EJECT T={slot}`: Fully extracts the strand from the toolhead and Bowden path, rewinding it completely onto the spool cradle in the ACE unit so the operator can safely remove or replace the spool.

4. **Zero-Load Web UI & Rotisserie Enhancements**:
   - The multiACE web UI provides complete management for empty/unloaded slots, manual feed/rollback buttons, and slot configuration cards even when no filament is present at the toolhead.
   - Includes first-class controls for spool drying rotation (`ROTISSERIE_SWEEP` and `ROTISSERIE_SPIN`), allowing filament drying without false jam alerts or unintended advancement into the multi-material hub.
