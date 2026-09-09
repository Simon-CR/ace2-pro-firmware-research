"""Multi-Format Tag Flashing and Native Decode Verification Test

Flashes a physical NTAG spool tag (parked at Slot 1) with all supported tag formats:
  1. Anycubic Native (magic 123, version 101, binary layout)
  2. OpenSpool / Spoolman NDEF JSON
  3. FilaMan NFC (NDEF JSON with sm_id)
  4. Prusament NFC (Prusa Research text format)
  5. Creality CFS (Creality filament text format)
  6. Original tag restore (from slot1_original_backup.json)

For each format:
  - Writes pages 4-39 using the RC522 passthrough
  - Verifies written bytes via RF read-back
  - Clears cached slot record (op 8)
  - Triggers live MCU FILAMENT_IDENTIFY
  - Queries Moonraker /printer/objects/query?ace_instance_0
  - Asserts that on-chip native_tag_decoder.c correctly decoded all fields!
"""
import sys
import time
import json
import binascii
import urllib.request
import urllib.parse

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_reader import Ace, PCD_TRANSCEIVE, BitFramingReg  # noqa: E402

B = "http://10.49.9.130:7125"
TxModeReg, RxModeReg = 0x12, 0x13
SLOT = 1
READER = 0


def _post(script, timeout=30):
    data = urllib.parse.urlencode({"script": script}).encode()
    urllib.request.urlopen(urllib.request.Request(
        B + "/printer/gcode/script", data=data), timeout=timeout).read()


def _get(path, timeout=15):
    return json.load(urllib.request.urlopen(B + path, timeout=timeout))["result"]


def prepare(a):
    a.wake()
    sel = a.batch([(6, 0)])
    a.batch([(1, BitFramingReg, 0x00)])
    tx = a.batch([(0, TxModeReg)])
    rx = a.batch([(0, RxModeReg)])
    tx0 = tx[0] if tx else 0
    rx0 = rx[0] if rx else 0
    a.batch([(1, TxModeReg, (tx0 | 0x80) & 0xFF)])
    a.batch([(1, RxModeReg, (rx0 | 0x80) & 0xFF)])
    return rx0


def read_all_pages(a, first=4, last=39):
    """Read all pages in 4-page chunks (fast, ~1.5s)."""
    out = {}
    page = first
    while page <= last:
        a.batch([(6, 0)])
        status, bits, rx = a.frame([0x30, page], cmd=PCD_TRANSCEIVE, rx=16)
        if not any(rx):
            page += 4
            continue
        for i in range(4):
            if page + i <= last:
                out[page + i] = bytes(rx[i * 4 : (i + 1) * 4])
        page += 4
    return out


def write_page_raw(a, page, data4, rx0):
    assert len(data4) == 4
    a.batch([(1, RxModeReg, (rx0 & ~0x80) & 0xFF)])
    ops = [
        (2, 0, 0xA2),
        (2, 1, page),
        (2, 2, data4[0]),
        (2, 3, data4[1]),
        (2, 4, data4[2]),
        (2, 5, data4[3]),
        (3, 6, PCD_TRANSCEIVE)
    ]
    a.batch(ops)
    time.sleep(0.015)
    a.batch([(1, RxModeReg, (rx0 | 0x80) & 0xFF)])
    a.batch([(6, 0)])


def write_pages(a, page_dict, rx0):
    """Smart differential write with verification and retries."""
    current = read_all_pages(a, 4, 39)
    diff_pages = [p for p in sorted(page_dict.keys()) if current.get(p) != page_dict[p]]
    print(f"Writing {len(diff_pages)} pages that changed (skipping {len(page_dict) - len(diff_pages)} identical)...")
    for page in diff_pages:
        write_page_raw(a, page, page_dict[page], rx0)
        
    verify_current = read_all_pages(a, 4, 39)
    failed = [p for p in page_dict.keys() if verify_current.get(p) != page_dict[p]]
    if failed:
        print(f"Retrying {len(failed)} pages: {failed}...")
        for page in failed:
            write_page_raw(a, page, page_dict[page], rx0)
        verify_current = read_all_pages(a, 4, 39)
        failed = [p for p in page_dict.keys() if verify_current.get(p) != page_dict[p]]
        if failed:
            raise RuntimeError(f"Pages failed to write after retry: {failed}")
    return True


def verify_pages(a, page_dict):
    current = read_all_pages(a, 4, 39)
    for p, exp in page_dict.items():
        act = current.get(p)
        if act != exp:
            raise RuntimeError(f"Page {p} mismatch! Expected {exp.hex()}, read {act.hex() if act else 'None'}")
    return True


def trigger_identify_and_get_slot(a, slot=SLOT):
    """Clear cached record on MCU and trigger live hardware FILAMENT_IDENTIFY."""
    # Op 8: Clear cached tag record in MCU SRAM for this slot
    a.batch([(8, slot)])
    time.sleep(0.1)

    # Issue live FILAMENT_IDENTIFY
    _post(f"ACE_RAW_CMD T={slot} CMD=FILAMENT_IDENTIFY INDEX={slot}")
    time.sleep(0.8)

    # Query Moonraker for ace_instance_0 status
    st = _get("/printer/objects/query?ace_instance_0")
    slots = st["status"]["ace_instance_0"]["slots"]
    return slots[slot]


# --- Payload Builders ---

def make_anycubic_payload():
    """Build authentic Anycubic Native format pages."""
    pages = {p: bytes(4) for p in range(4, 40)}
    # Page 4: Magic 123 / Version 101
    pages[4] = bytes([123, 0, 101, 0])
    # Page 5: SKU "SM99"
    pages[5] = b"SM99"
    # Page 10-12: Brand "Anycubic\0"
    pages[10] = b"Anyc"
    pages[11] = b"ubic"
    pages[12] = b"\x00\x00\x00\x00"
    # Page 15: Material "PLA\0"
    pages[15] = b"PLA\x00"
    # Page 20: Color ABGR Red (#FF0000 -> A=255, B=0, G=0, R=255)
    pages[20] = bytes([255, 0, 0, 255])
    # Page 24: Temp min=205, max=225
    pages[24] = bytes([205, 0, 225, 0])
    # Page 29: Bed min=50, max=60
    pages[29] = bytes([50, 0, 60, 0])
    # Page 30: Diameter 175
    pages[30] = bytes([175, 0, 0, 0])
    # Page 31: 1000 grams
    pages[31] = bytes([232, 3, 0, 0])
    return pages


def make_ndef_json_payload(json_str):
    """Pack JSON string into standard NDEF TLV record spanning pages 4-39."""
    json_bytes = json_str.encode("utf-8")
    type_bytes = b"application/json"
    
    # NDEF Record: MB=1, ME=1, CF=0, SR=1, IL=0, TNF=0x02 (0xD2)
    # [0xD2, len(type), len(payload), type..., payload...]
    ndef_record = bytearray([0xD2, len(type_bytes), len(json_bytes)])
    ndef_record += type_bytes
    ndef_record += json_bytes
    
    # TLV: 0x03, len(record), record..., 0xFE (terminator)
    tlv = bytearray([0x03, len(ndef_record)]) + ndef_record + bytearray([0xFE])
    
    # Pad to 36 pages (144 bytes)
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
    """Pack plain text payload into pages 4-39."""
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
    """Load original dump JSON into page dict."""
    with open(filepath, "r") as f:
        data = json.load(f)
    pages = {}
    for p_str, hex_val in data["pages"].items():
        if hex_val:
            pages[int(p_str)] = binascii.unhexlify(hex_val)
    return pages


def run_tests():
    print("=" * 70)
    print("STARTING MULTI-FORMAT RFID TAG FLASH & NATIVE DECODE TEST")
    print("=" * 70)

    a = Ace(reader=READER, slot=SLOT)
    rx0 = prepare(a)
    print(f"Slot {SLOT} initialized on Reader {READER}. Base RxMode: 0x{rx0:02X}")

    backup_path = __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0] + "/slot1_original_backup.json"
    original_pages = load_original_dump(backup_path)
    print(f"Loaded {len(original_pages)} original pages from backup.")

    test_cases = [
        {
            "name": "1. Anycubic Native (Binary Magic 123/101)",
            "pages": make_anycubic_payload(),
            "expected": {
                "sku": "SM99",
                "material": "PLA",
                "color": [255, 0, 0],  # Red
                "temp_min": 205,
                "temp_max": 225,
            }
        },
        {
            "name": "2. OpenSpool / Spoolman NDEF JSON",
            "pages": make_ndef_json_payload(
                '{"protocol":"openspool","version":"1.0","type":"PETG","color_hex":"#00FF00","brand":"Polymaker","temp_min":230,"temp_max":250,"bed_min":70,"bed_max":85}'
            ),
            "expected": {
                "material": "PETG",
                "color": [0, 255, 0],  # Green
                "temp_min": 230,
                "temp_max": 250,
            }
        },
        {
            "name": "3. FilaMan NFC (NDEF JSON with sm_id)",
            "pages": make_ndef_json_payload(
                '{"sm_id":"88","brand":"FilaMan","material":"ABS","color_hex":"#0088FF","temp_min":240,"temp_max":260,"bed_min":95,"bed_max":105}'
            ),
            "expected": {
                "sku": "SM88",
                "material": "ABS",
                "color": [0, 136, 255],  # Azure Blue
                "temp_min": 240,
                "temp_max": 260,
            }
        },
        {
            "name": "4. Prusament NFC (Text format)",
            "pages": make_text_payload("Prusament PLA Galaxy Purple #800080\n"),
            "expected": {
                "sku": "PRUSA-PLA",
                "material": "PLA",
                "color": [128, 0, 128],  # Purple
                "temp_min": 205,
                "temp_max": 225,
            }
        },
        {
            "name": "5. Creality CFS (Text format)",
            "pages": make_text_payload("Creality Hyper PLA White #FFFFFF\n"),
            "expected": {
                "sku": "CFS-Hyper PLA",
                "material": "Hyper PLA",
                "color": [255, 255, 255],  # White
                "temp_min": 190,
                "temp_max": 230,
            }
        }
    ]

    results = []

    for tc in test_cases:
        print("\n" + "-" * 60)
        print(f"TESTING: {tc['name']}")
        print("-" * 60)
        
        # 1. Flash pages
        t0 = time.time()
        print(f"Writing {len(tc['pages'])} pages...")
        write_pages(a, tc["pages"], rx0)
        t_write = time.time() - t0
        print(f"Write completed in {t_write:.2f}s. Verifying EEPROM...")
        
        # 2. Verify RF readback
        verify_pages(a, tc["pages"])
        print("EEPROM read-back verification: 100% MATCH!")
        
        # 3. Trigger native on-chip decode
        print("Triggering native on-chip FILAMENT_IDENTIFY...")
        slot_info = trigger_identify_and_get_slot(a, SLOT)
        print(f"Moonraker slot {SLOT} telemetry received:")
        print(f"  SKU:           {slot_info.get('sku')}")
        print(f"  Material:      {slot_info.get('material')}")
        print(f"  Color [R,G,B]: {slot_info.get('color')}")
        ext_temp = slot_info.get("extruder_temp", {})
        print(f"  Extruder Temp: min={ext_temp.get('min')}, max={ext_temp.get('max')}")
        
        # 4. Assertions
        exp = tc["expected"]
        passed = True
        err_msg = ""
        
        if "sku" in exp and slot_info.get("sku") != exp["sku"]:
            passed = False
            err_msg += f"SKU mismatch (got {slot_info.get('sku')}, exp {exp['sku']}); "
        if "material" in exp and slot_info.get("material") != exp["material"]:
            passed = False
            err_msg += f"Material mismatch (got {slot_info.get('material')}, exp {exp['material']}); "
        if "color" in exp and slot_info.get("color") != exp["color"]:
            passed = False
            err_msg += f"Color mismatch (got {slot_info.get('color')}, exp {exp['color']}); "
        if "temp_min" in exp and ext_temp.get("min") != exp["temp_min"]:
            passed = False
            err_msg += f"Temp min mismatch (got {ext_temp.get('min')}, exp {exp['temp_min']}); "
        if "temp_max" in exp and ext_temp.get("max") != exp["temp_max"]:
            passed = False
            err_msg += f"Temp max mismatch (got {ext_temp.get('max')}, exp {exp['temp_max']}); "
            
        if passed:
            print(f"RESULT: PASS [Native MCU decoded {tc['name']} successfully!]")
            results.append((tc["name"], "PASS", ""))
        else:
            print(f"RESULT: FAIL [{err_msg}]")
            results.append((tc["name"], "FAIL", err_msg))

    # --- RESTORATION STEP ---
    print("\n" + "=" * 60)
    print("RESTORING ORIGINAL TAG DATA FROM BACKUP")
    print("=" * 60)
    print(f"Writing {len(original_pages)} original pages...")
    write_pages(a, original_pages, rx0)
    verify_pages(a, original_pages)
    print("Original tag EEPROM restored and verified!")
    
    # Re-identify original
    slot_info = trigger_identify_and_get_slot(a, SLOT)
    print("Restored slot status:")
    print(f"  SKU:      {slot_info.get('sku')}")
    print(f"  Material: {slot_info.get('material')}")
    print(f"  Color:    {slot_info.get('color')}")
    
    print("\n" + "=" * 70)
    print("SUMMARY OF RESULTS:")
    all_ok = True
    for name, status, err in results:
        mark = "OK" if status == "PASS" else "FAIL"
        print(f"  [{mark}] {name} {err}")
        if status != "PASS":
            all_ok = False
    print("=" * 70)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run_tests())
