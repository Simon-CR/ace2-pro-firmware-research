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

## 4. Porting Steps for Snapmaker U1

To port this setup to the Snapmaker U1:

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
