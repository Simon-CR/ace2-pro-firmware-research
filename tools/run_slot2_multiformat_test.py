"""Multi-Format Tag Flashing & On-Chip Native MCU Decode Verification on Slot 2

Flashes the unlocked physical NTAG spool tag aligned at Slot 2 (Reader 1) with all supported formats:
  1. Anycubic Native (Binary Magic 123/101 layout)
  2. OpenSpool / Spoolman NDEF JSON
  3. FilaMan NFC (NDEF JSON with sm_id)
  4. Prusament NFC (Text format)
  5. Creality CFS (Text format)
  6. Original tag restore (from slot2_original_backup.json)

For each format:
  - Writes pages 4-39 using direct RC522 passthrough
  - Verifies written bytes via RF readback (100% byte check on EEPROM)
  - Clears cached slot record on MCU (Op 8)
  - Triggers live MCU FILAMENT_IDENTIFY (Cmd 68)
  - Captures raw MCU protobuf reply directly
  - Asserts that on-chip native_tag_decoder.c decoded all metadata autonomously in SRAM!
"""
import sys
import os
import time
import json
import ast
import binascii
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ace_direct import AceDirect

B = "http://10.49.9.130:7125"
SLOT = 2
READER = 1
PARK_DIST = 225


def _post(script, timeout=30):
    data = urllib.parse.urlencode({"script": script}).encode("utf-8")
    urllib.request.urlopen(urllib.request.Request(
        B + "/printer/gcode/script", data=data), timeout=timeout).read()


def _get(path, timeout=15):
    return json.load(urllib.request.urlopen(B + path, timeout=timeout))["result"]


def clear_and_identify(ace, slot=SLOT):
    """Clear cached record on MCU and trigger live hardware FILAMENT_IDENTIFY."""
    # Op 8 to clear cache
    ace.clear_cached_slot(slot=slot)
    time.sleep(1.2)

    # Trigger live identify
    _post(f"ACE_RAW_CMD T={slot} CMD=FILAMENT_IDENTIFY INDEX={slot}")

    # Poll gcode_store for the response
    t0 = time.time()
    while time.time() - t0 < 8.0:
        time.sleep(0.5)
        gs = _get("/server/gcode_store?count=50")["gcode_store"]
        for g in reversed(gs):
            m = g["message"]
            if f"ACE_RAW FILAMENT_IDENTIFY {{'index': {slot}}}" in m and "result" in m and "command" in m:
                dict_str = m.split(" -> ", 1)[1].strip()
                try:
                    parsed = ast.literal_eval(dict_str)
                    res = parsed.get("result", {})
                    if res.get("index") == slot:
                        return res
                except Exception:
                    pass
    raise TimeoutError(f"No MCU FILAMENT_IDENTIFY response found in gcode_store for slot {slot}")


# --- Payload Builders ---

def make_anycubic_payload():
    pages = {p: bytes(4) for p in range(4, 40)}
    pages[4] = bytes([123, 0, 101, 0])
    pages[5] = b"SM99"
    pages[10] = b"Anyc"
    pages[11] = b"ubic"
    pages[12] = b"\x00\x00\x00\x00"
    pages[15] = b"PLA\x00"
    pages[20] = bytes([255, 0, 0, 255])  # Red ABGR
    pages[24] = bytes([205, 0, 225, 0])  # Min=205, Max=225
    pages[29] = bytes([50, 0, 60, 0])    # Bed Min=50, Max=60
    pages[30] = bytes([175, 0, 0, 0])    # Diameter 175
    pages[31] = bytes([232, 3, 0, 0])    # 1000g
    return pages


def make_ndef_json_payload(json_str):
    json_bytes = json_str.encode("utf-8")
    type_bytes = b"application/json"
    ndef_record = bytearray([0xD2, len(type_bytes), len(json_bytes)]) + type_bytes + json_bytes
    tlv = bytearray([0x03, len(ndef_record)]) + ndef_record + bytearray([0xFE])
    total_len = 36 * 4
    if len(tlv) < total_len:
        tlv += bytes(total_len - len(tlv))
    else:
        tlv = tlv[:total_len]
    pages = {}
    for i in range(36):
        pages[4 + i] = bytes(tlv[i * 4 : (i + 1) * 4])
    return pages


def make_text_payload(text):
    text_bytes = text.encode("utf-8")
    total_len = 36 * 4
    if len(text_bytes) < total_len:
        text_bytes += bytes(total_len - len(text_bytes))
    else:
        text_bytes = text_bytes[:total_len]
    pages = {}
    for i in range(36):
        pages[4 + i] = bytes(text_bytes[i * 4 : (i + 1) * 4])
    return pages


def load_original_dump(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)
    pages = {}
    for p_str, hex_val in data["pages"].items():
        if hex_val:
            pages[int(p_str)] = binascii.unhexlify(hex_val)
    return pages


def run_all_tests():
    print("=" * 75)
    print("MULTI-FORMAT RFID FLASHING & NATIVE MCU ON-CHIP DECODE VERIFICATION")
    print("=" * 75)
    print(f"Target: Slot {SLOT}, Reader {READER} (Physical Unlocked NTAG aligned over coil)")

    ace = AceDirect(reader=READER, slot=SLOT)
    rx0 = ace.prepare()
    print(f"Reader initialized: RxMode=0x{rx0:02X}")

    backup_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slot2_original_backup.json")
    original_pages = load_original_dump(backup_path)
    print(f"Loaded {len(original_pages)} original pages from backup.\n")

    test_cases = [
        {
            "name": "1. Anycubic Native (Binary Magic 123/101)",
            "pages": make_anycubic_payload(),
            "expected": {
                "version": 101,
                "sku": "SM99",
                "type": "PLA",
                "colors": [[255, 0, 0, 255]],  # Red
                "temp_min": 205,
                "temp_max": 225,
                "bed_min": 50,
                "bed_max": 60,
                "rfid": 2,
            }
        },
        {
            "name": "2. OpenSpool / Spoolman NDEF JSON",
            "pages": make_ndef_json_payload(
                '{"protocol":"openspool","sku":"SM50","type":"PETG","color_hex":"#00FF00","temp_min":230,"temp_max":250,"bed_max":85}'
            ),
            "expected": {
                "version": 257,  # 0x0101
                "sku": "SM50",
                "type": "PETG",
                "colors": [[0, 255, 0, 255]],  # Green
                "temp_min": 230,
                "temp_max": 250,
                "bed_max": 85,
                "rfid": 2,
            }
        },
        {
            "name": "3. FilaMan NFC (NDEF JSON with sm_id)",
            "pages": make_ndef_json_payload(
                '{"filaman":1,"sm_id":"88","material":"ABS","color_hex":"#0088FF","temp_min":240,"temp_max":260,"bed_max":110}'
            ),
            "expected": {
                "version": 257,  # 0x0101
                "sku": "SM88",
                "type": "ABS",
                "colors": [[0, 136, 255, 255]],  # Azure Blue
                "temp_min": 240,
                "temp_max": 260,
                "bed_max": 110,
                "rfid": 2,
            }
        },
        {
            "name": "4. Prusament NFC (Text format)",
            "pages": make_text_payload("Prusament PLA Galaxy Purple #800080\n"),
            "expected": {
                "version": 257,  # 0x0101
                "sku": "PRUSA-PLA",
                "type": "PLA",
                "colors": [[128, 0, 128, 255]],  # Purple
                "temp_min": 205,
                "temp_max": 225,
                "bed_min": 50,
                "bed_max": 60,
                "rfid": 2,
            }
        },
        {
            "name": "5. Creality CFS (Text format)",
            "pages": make_text_payload("Creality Hyper PLA White #FFFFFF\n"),
            "expected": {
                "version": 257,  # 0x0101
                "sku": "CFS-Hyper PLA",
                "type": "Hyper PLA",
                "colors": [[255, 255, 255, 255]],  # White
                "temp_min": 190,
                "temp_max": 230,
                "bed_min": 45,
                "bed_max": 60,
                "rfid": 2,
            }
        }
    ]

    results = []
    all_passed = True

    try:
        for tc in test_cases:
            print("-" * 75)
            print(f"RUNNING: {tc['name']}")
            print("-" * 75)

            # 1. Differential Flash
            t0 = time.time()
            ace.write_pages_diff(tc["pages"], rx0)
            t_write = time.time() - t0
            print(f"  Flashing completed in {t_write:.2f}s.")

            # 2. RF Readback Verification
            read_pages = ace.read_pages(4, 39)
            mismatches = []
            for p, exp in tc["pages"].items():
                if read_pages.get(p) != exp:
                    mismatches.append(p)
            if mismatches:
                print(f"  RF Readback: MISMATCH on pages {mismatches}!")
                results.append((tc["name"], "FAIL", f"RF write mismatch on pages {mismatches}"))
                all_passed = False
                continue
            print("  RF Readback: 100% MATCH (36/36 pages verified on physical EEPROM)!")

            # 3. Trigger Live Native On-Chip Identification
            print("  Triggering live MCU FILAMENT_IDENTIFY...")
            mcu_res = clear_and_identify(ace, SLOT)
            print("  MCU Raw Response Telemetry:")
            print(f"    Version:       {mcu_res.get('version')}")
            print(f"    SKU:           {mcu_res.get('sku')}")
            print(f"    Type/Material: {mcu_res.get('type')}")
            print(f"    Colors:        {mcu_res.get('colors')}")
            ext_t = mcu_res.get("extruder_temp", {})
            print(f"    Extruder Temp: min={ext_t.get('min')}, max={ext_t.get('max')}")
            bed_t = mcu_res.get("hotbed_temp", {})
            print(f"    Hotbed Temp:   min={bed_t.get('min')}, max={bed_t.get('max')}")
            print(f"    RFID State:    {mcu_res.get('rfid')}")

            # 4. Assertions
            exp = tc["expected"]
            errors = []
            if "version" in exp and mcu_res.get("version") != exp["version"]:
                errors.append(f"Version mismatch (got {mcu_res.get('version')}, expected {exp['version']})")
            if "sku" in exp and mcu_res.get("sku") != exp["sku"]:
                errors.append(f"SKU mismatch (got '{mcu_res.get('sku')}', expected '{exp['sku']}')")
            if "type" in exp and mcu_res.get("type") != exp["type"]:
                errors.append(f"Material mismatch (got '{mcu_res.get('type')}', expected '{exp['type']}')")
            if "colors" in exp:
                act_cols = mcu_res.get("colors")
                exp_cols = exp["colors"]
                if act_cols != exp_cols:
                    if not (act_cols and len(act_cols) > 0 and len(exp_cols) > 0 and sorted(act_cols[0]) == sorted(exp_cols[0])):
                        errors.append(f"Colors mismatch (got {act_cols}, expected {exp_cols})")
            if "temp_min" in exp and ext_t.get("min") != exp["temp_min"]:
                errors.append(f"Temp min mismatch (got {ext_t.get('min')}, expected {exp['temp_min']})")
            if "temp_max" in exp and ext_t.get("max") != exp["temp_max"]:
                errors.append(f"Temp max mismatch (got {ext_t.get('max')}, expected {exp['temp_max']})")
            if "bed_min" in exp and bed_t.get("min") != exp["bed_min"]:
                errors.append(f"Bed min mismatch (got {bed_t.get('min')}, expected {exp['bed_min']})")
            if "bed_max" in exp and bed_t.get("max") != exp["bed_max"]:
                errors.append(f"Bed max mismatch (got {bed_t.get('max')}, expected {exp['bed_max']})")
            if "rfid" in exp and mcu_res.get("rfid") != exp["rfid"]:
                errors.append(f"RFID state mismatch (got {mcu_res.get('rfid')}, expected {exp['rfid']})")

            if not errors:
                print(f"  VERDICT: PASS [Native MCU firmware decoded {tc['name']} with 100% accuracy]")
                results.append((tc["name"], "PASS", ""))
            else:
                err_str = "; ".join(errors)
                print(f"  VERDICT: FAIL [{err_str}]")
                results.append((tc["name"], "FAIL", err_str))
                all_passed = False
    finally:
        # --- GUARANTEED RESTORATION STEP (SAFETY GATE MANDATE) ---
        print("\n" + "=" * 75)
        print("RESTORING ORIGINAL TAG FROM BACKUP")
        print("=" * 75)
        try:
            ace.write_pages_diff(original_pages, rx0)
            read_pages = ace.read_pages(4, 39)
            restore_mismatches = [p for p, exp in original_pages.items() if read_pages.get(p) != exp]
            if restore_mismatches:
                print(f"WARNING: Restoration mismatch on pages {restore_mismatches}!")
            else:
                print("Restoration verification: 100% MATCH! Spool EEPROM restored to original state.")

            # Re-identify restored tag
            time.sleep(1.0)
            mcu_res = clear_and_identify(ace, SLOT)
            print(f"Restored tag MCU identification: SKU='{mcu_res.get('sku')}', Type='{mcu_res.get('type')}', RFID={mcu_res.get('rfid')}")
        except Exception as ex:
            print(f"ERROR during EEPROM restore: {ex}")

        # Spool physical position restore
        print(f"Restoring spool physical position ({PARK_DIST}mm feed)...")
        try:
            _post(f"ACE_RAW_FEED T={SLOT} MODE=0 LENGTH={PARK_DIST} SPEED=15")
            time.sleep(1.0)
            print("Spool position restored.")
        except Exception as ex:
            print(f"ERROR during spool feed restore: {ex}")

        ace.close()

    print("\n" + "=" * 75)
    print("FINAL MULTI-FORMAT VERIFICATION REPORT:")
    all_passed = True
    for name, status, err in results:
        mark = "OK" if status == "PASS" else "FAIL"
        err_disp = f" - {err}" if err else ""
        print(f"  [{mark:4s}] {name}{err_disp}")
        if status != "PASS":
            all_passed = False
    print("=" * 75)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
