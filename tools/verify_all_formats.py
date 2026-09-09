"""Comprehensive Multi-Format RFID Tag Flashing and Native MCU Decode Verification

Flashes a physical NTAG spool tag (parked at Slot 1) with all supported tag formats:
  1. Anycubic Native (binary magic 123/101 layout)
  2. OpenSpool / Spoolman NDEF JSON
  3. FilaMan NFC (NDEF JSON with sm_id)
  4. Prusament NFC (Prusa Research text format)
  5. Creality CFS (Creality filament text format)
  6. Original tag restore (from slot1_original_backup.json)

For each format:
  - Writes pages 4-39 using direct RC522 passthrough via Klippy socket
  - Verifies written bytes via RF read-back
  - Clears cached slot record on MCU (op 8)
  - Triggers live MCU FILAMENT_IDENTIFY (cmd 68)
  - Captures raw MCU protobuf reply from socket stream
  - Asserts that on-chip native_tag_decoder.c correctly decoded all metadata!
"""
import sys
import time
import json
import ast
import binascii

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_direct import AceDirect

SLOT = 1
READER = 0


# --- Payload Builders ---

def make_anycubic_payload():
    pages = {p: bytes(4) for p in range(4, 40)}
    pages[4] = bytes([123, 0, 101, 0])
    pages[5] = b"SM99"
    pages[10] = b"Anyc"
    pages[11] = b"ubic"
    pages[12] = b"\x00\x00\x00\x00"
    pages[15] = b"PLA\x00"
    pages[20] = bytes([255, 0, 0, 255])  # Red in ABGR: A=255, B=0, G=0, R=255
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


class MultiFormatTester:
    def __init__(self):
        self.ace = AceDirect(reader=READER, slot=SLOT)
        self.rx0 = self.ace.prepare()

    def identify_and_capture(self):
        """Issue FILAMENT_IDENTIFY and parse raw response dict from socket."""
        # Op 8: Clear cached slot record in MCU SRAM
        self.ace.clear_cached_slot(SLOT)
        time.sleep(0.05)
        
        req_id = self.ace._next_id()
        cmd = {"id": req_id, "method": "gcode/script", "params": {"script": f"ACE_RAW_CMD T={SLOT} CMD=FILAMENT_IDENTIFY INDEX={SLOT}"}}
        self.ace.sock.sendall(json.dumps(cmd).encode("utf-8") + b"\x03")
        
        t0 = time.time()
        while time.time() - t0 < 6.0:
            if b"\x03" in self.ace.rx_buf:
                parts = self.ace.rx_buf.split(b"\x03")
                self.ace.rx_buf = parts[-1]
                for p in parts[:-1]:
                    if p.strip():
                        try:
                            msg = json.loads(p.decode("utf-8"))
                            resp_str = msg.get("params", {}).get("response", "")
                            target = f"ACE_RAW FILAMENT_IDENTIFY {{'index': {SLOT}}}"
                            if target in resp_str and "result" in resp_str and "command" in resp_str:
                                dict_str = resp_str.split(" -> ", 1)[1].strip()
                                parsed = ast.literal_eval(dict_str)
                                return parsed.get("result", {})
                        except Exception:
                            pass
            try:
                chunk = self.ace.sock.recv(4096)
                if not chunk:
                    break
                self.ace.rx_buf += chunk
            except Exception:
                break
        raise TimeoutError("Timeout waiting for MCU FILAMENT_IDENTIFY response")

    def run(self):
        print("=" * 70)
        print("MULTI-FORMAT RFID FLASH & ON-CHIP DECODE VERIFICATION")
        print("=" * 70)
        print(f"Hardware connection: Slot {SLOT}, Reader {READER}, RxMode 0x{self.rx0:02X}")

        backup_path = __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0] + "/slot1_original_backup.json"
        original_pages = load_original_dump(backup_path)
        print(f"Loaded {len(original_pages)} original pages from backup.\n")

        test_cases = [
            {
                "name": "1. Anycubic Native (Binary Magic 123/101)",
                "pages": make_anycubic_payload(),
                "expected": {
                    "sku": "SM99",
                    "type": "PLA",
                    "colors": [[255, 0, 0, 255]],  # Red
                    "temp_min": 205,
                    "temp_max": 225,
                    "rfid": 2,
                }
            },
            {
                "name": "2. OpenSpool / Spoolman NDEF JSON",
                "pages": make_ndef_json_payload(
                    '{"protocol":"openspool","version":"1.0","type":"PETG","color_hex":"#00FF00","brand":"Polymaker","temp_min":230,"temp_max":250,"bed_min":70,"bed_max":85}'
                ),
                "expected": {
                    "type": "PETG",
                    "colors": [[0, 255, 0, 255]],  # Green
                    "temp_min": 230,
                    "temp_max": 250,
                    "rfid": 2,
                }
            },
            {
                "name": "3. FilaMan NFC (NDEF JSON with sm_id)",
                "pages": make_ndef_json_payload(
                    '{"sm_id":"88","brand":"FilaMan","material":"ABS","color_hex":"#0088FF","temp_min":240,"temp_max":260,"bed_min":95,"bed_max":105}'
                ),
                "expected": {
                    "sku": "SM88",
                    "type": "ABS",
                    "colors": [[0, 136, 255, 255]],  # Azure Blue
                    "temp_min": 240,
                    "temp_max": 260,
                    "rfid": 2,
                }
            },
            {
                "name": "4. Prusament NFC (Text format)",
                "pages": make_text_payload("Prusament PLA Galaxy Purple #800080\n"),
                "expected": {
                    "sku": "PRUSA-PLA",
                    "type": "PLA",
                    "colors": [[128, 0, 128, 255]],  # Purple
                    "temp_min": 205,
                    "temp_max": 225,
                    "rfid": 2,
                }
            },
            {
                "name": "5. Creality CFS (Text format)",
                "pages": make_text_payload("Creality Hyper PLA White #FFFFFF\n"),
                "expected": {
                    "sku": "CFS-Hyper PLA",
                    "type": "Hyper PLA",
                    "colors": [[255, 255, 255, 255]],  # White
                    "temp_min": 190,
                    "temp_max": 230,
                    "rfid": 2,
                }
            }
        ]

        results = []

        for tc in test_cases:
            print("-" * 65)
            print(f"RUNNING: {tc['name']}")
            print("-" * 65)

            # 1. Differential Flash
            t0 = time.time()
            self.ace.write_pages_diff(tc["pages"], self.rx0)
            t_write = time.time() - t0
            print(f"  Flashing completed in {t_write:.2f}s.")

            # 2. RF Readback Verification
            read_pages = self.ace.read_pages(4, 39)
            mismatches = []
            for p, exp in tc["pages"].items():
                if read_pages.get(p) != exp:
                    mismatches.append(p)
            if mismatches:
                print(f"  RF Readback: MISMATCH on pages {mismatches}!")
                results.append((tc["name"], "FAIL", f"RF write mismatch on pages {mismatches}"))
                continue
            print("  RF Readback: 100% MATCH (36/36 pages verified on physical EEPROM)!")

            # 3. Trigger Live Native On-Chip Identification
            print("  Triggering live MCU FILAMENT_IDENTIFY...")
            mcu_res = self.identify_and_capture()
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
            if "sku" in exp and mcu_res.get("sku") != exp["sku"]:
                errors.append(f"SKU mismatch (got '{mcu_res.get('sku')}', expected '{exp['sku']}')")
            if "type" in exp and mcu_res.get("type") != exp["type"]:
                errors.append(f"Material mismatch (got '{mcu_res.get('type')}', expected '{exp['type']}')")
            if "colors" in exp and mcu_res.get("colors") != exp["colors"]:
                errors.append(f"Colors mismatch (got {mcu_res.get('colors')}, expected {exp['colors']})")
            if "temp_min" in exp and ext_t.get("min") != exp["temp_min"]:
                errors.append(f"Temp min mismatch (got {ext_t.get('min')}, expected {exp['temp_min']})")
            if "temp_max" in exp and ext_t.get("max") != exp["temp_max"]:
                errors.append(f"Temp max mismatch (got {ext_t.get('max')}, expected {exp['temp_max']})")
            if "rfid" in exp and mcu_res.get("rfid") != exp["rfid"]:
                errors.append(f"RFID state mismatch (got {mcu_res.get('rfid')}, expected {exp['rfid']})")

            if not errors:
                print(f"  VERDICT: PASS [Native MCU decoded {tc['name']} with 100% accuracy]")
                results.append((tc["name"], "PASS", ""))
            else:
                err_str = "; ".join(errors)
                print(f"  VERDICT: FAIL [{err_str}]")
                results.append((tc["name"], "FAIL", err_str))

        # --- RESTORATION STEP ---
        print("\n" + "=" * 65)
        print("RESTORING ORIGINAL TAG FROM BACKUP")
        print("=" * 65)
        self.ace.write_pages_diff(original_pages, self.rx0)
        read_pages = self.ace.read_pages(4, 39)
        restore_mismatches = [p for p, exp in original_pages.items() if read_pages.get(p) != exp]
        if restore_mismatches:
            print(f"WARNING: Restoration mismatch on pages {restore_mismatches}!")
        else:
            print("Restoration verification: 100% MATCH! Spool EEPROM restored to original state.")

        # Re-identify restored tag
        mcu_res = self.identify_and_capture()
        print(f"Restored tag MCU identification: SKU='{mcu_res.get('sku')}', Type='{mcu_res.get('type')}', RFID={mcu_res.get('rfid')}")

        self.ace.close()

        print("\n" + "=" * 70)
        print("FINAL SUMMARY REPORT:")
        all_passed = True
        for name, status, err in results:
            mark = "OK" if status == "PASS" else "FAIL"
            err_disp = f" - {err}" if err else ""
            print(f"  [{mark:4s}] {name}{err_disp}")
            if status != "PASS":
                all_passed = False
        print("=" * 70)
        return 0 if all_passed else 1


if __name__ == "__main__":
    tester = MultiFormatTester()
    sys.exit(tester.run())
