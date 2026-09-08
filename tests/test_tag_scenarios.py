"""Every tag format must resolve by all three routes. Run this file to check.

The three scenarios, which have to hold for EVERY format:

  1. SKU matched against the backend       - the tag names its spool, the backend confirms it
  2. UID matched against the backend       - the tag has no spool number, identity is the UID,
                                             checked against BOTH rfid_uid and previous_tag
  3. Tag data only                         - no backend reachable, or the backend has never
                                             heard of this spool; the lane still renders

No hardware and no network: images are built here, and the "backend" is a list of spool dicts
shaped exactly like FilaMan's /api/v1/spools response, including the three mutually incompatible
UID spellings that really coexist in one instance.
"""
import binascii
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "klipper"))
import ace_tag_formats as f  # noqa: E402


# --- a backend that looks like the real one ------------------------------------------------
SPOOLS = [
    {"id": 26, "rfid_uid": "04A27C70C52A81",
     "custom_fields": {"previous_tag": "0424D34FC92A81"},
     "filament": {"designation": "Ecofil - Dark Brown"}},
    {"id": 22, "rfid_uid": "04:AB:DD:4F:C9:2A:81", "custom_fields": {},
     "filament": {"designation": "PLA+ Bone White"}},
    {"id": 7, "rfid_uid": "16C74AC7C93E4614A67B31C5EA5DF8ED", "custom_fields": {},
     "filament": {"designation": "White (30106)"}},
]


def uid_pages(uid_hex):
    """Pages 0-3 carrying a 7-byte UID, with a correct BCC0 so the check passes."""
    u = binascii.unhexlify(uid_hex)
    bcc0 = 0x88 ^ u[0] ^ u[1] ^ u[2]
    return bytes(u[0:3]) + bytes([bcc0]) + bytes(u[3:7]) + b"\x00" * 8


def anycubic_image(sku="SM24"):
    """sku=None leaves the field blank - a stock tag carrying no spool number."""
    img = bytearray(144)
    img[0:4] = b"\x7b\x00\x65\x00"
    if sku:
        img[4:4 + len(sku)] = sku.encode()
    img[24:33] = b"Bambu Lab"
    img[44:47] = b"PLA"
    img[64:68] = bytes([0xFF, 0x59, 0xD9, 0xF7])
    img[80:84] = (200).to_bytes(2, "little") + (220).to_bytes(2, "little")
    return bytes(img)


def ndef_image(payload, marker=b"application/json"):
    body = marker + json.dumps(payload).encode()
    return bytes([0x03, len(body) & 0xFF]) + body + b"\xfe"


FORMATS = {
    "anycubic":      lambda sku: anycubic_image("SM%d" % sku if sku else None),
    "openspool":     lambda sku: ndef_image({"protocol": "openspool", "version": "1.0",
                                             "type": "PLA", "color_hex": "#4B2A17",
                                             "brand": "Filaments.CA", "min_temp": "200",
                                             "max_temp": "220",
                                             **({"spool_id": sku, "sm_id": sku} if sku else {})}),
    "filaman":       lambda sku: ndef_image({"protocol": "filaman", "type": "PETG",
                                             "brand": "Filaments.CA", "color_hex": "112233",
                                             **({"sku": str(sku)} if sku else {})}),
    "openprinttag":  lambda sku: ndef_image({"protocol": "openprinttag", "material": "ABS",
                                             "manufacturer": "Generic", "colour": "FF8800",
                                             **({"spool": sku} if sku else {})}),
    "ndef-json":     lambda sku: ndef_image({"type": "TPU", "brand": "NoName",
                                             "color": "00FF00",
                                             **({"spoolId": sku} if sku else {})}),
}

results = []


def check(label, cond, detail=""):
    results.append((label, cond, detail))
    print("  %-4s %-52s %s" % ("ok" if cond else "FAIL", label, detail))


print("SCENARIO 1 - SKU on the tag, spool exists in the backend")
for name, build in FORMATS.items():
    rec = f.parse(build(26))
    sid, how, backed = f.resolve(rec, SPOOLS)
    check("%s: sku -> spool 26, confirmed" % name,
          sid == 26 and backed, "%s / %s" % (rec["format"], how))

print("\nSCENARIO 2 - no SKU on the tag, identity from the UID")
for name, build in FORMATS.items():
    img = uid_pages("04A27C70C52A81") + build(None)
    rec = f.parse(img[16:])                       # the parser sees the data pages
    rec["uid"] = f.uid_from_image(img)            # the UID comes from pages 0-1
    sid, how, backed = f.resolve(rec, SPOOLS)
    check("%s: rfid_uid -> spool 26" % name, sid == 26 and backed, how)

print("\nSCENARIO 2b - the OTHER face of the same spool (previous_tag)")
for name, build in FORMATS.items():
    img = uid_pages("0424D34FC92A81") + build(None)
    rec = f.parse(img[16:])
    rec["uid"] = f.uid_from_image(img)
    sid, how, backed = f.resolve(rec, SPOOLS)
    check("%s: previous_tag -> spool 26" % name, sid == 26 and backed, how)

print("\nSCENARIO 2c - UID spellings that differ from the backend's")
for spelling in ("04:AB:DD:4F:C9:2A:81", "04abdd4fc92a81", "04-AB-DD-4F-C9-2A-81"):
    rec = {"sku": None, "uid": spelling}
    sid, how, backed = f.resolve(rec, SPOOLS)
    check("uid %-22s -> spool 22" % spelling, sid == 22 and backed, how)

print("\nSCENARIO 3a - backend unreachable: tag data must still render")
for name, build in FORMATS.items():
    rec = f.parse(build(26))
    sid, how, backed = f.resolve(rec, None)
    usable = bool(rec.get("material")) and bool(rec.get("color"))
    check("%s: renders offline, sku kept, unconfirmed" % name,
          sid == 26 and not backed and usable,
          "material=%r colour=%r" % (rec.get("material"), rec.get("color")))

print("\nSCENARIO 3b - spool number the backend has never heard of")
for name, build in FORMATS.items():
    rec = f.parse(build(999))
    sid, how, backed = f.resolve(rec, SPOOLS)
    check("%s: reports 999 unconfirmed, does NOT bind another" % name,
          sid == 999 and not backed, how)

print("\nSCENARIO 3c - nothing identifying at all")
for name, build in FORMATS.items():
    rec = f.parse(build(None))
    sid, how, backed = f.resolve(rec, SPOOLS)
    usable = bool(rec.get("material"))
    check("%s: unresolved but still renders" % name,
          sid is None and not backed and usable, how)

print("\nBAMBU - UID only, no readable pages (MIFARE crypto)")
rec = {"format": "bambu", "sku": None, "uid": "16C74AC7C93E4614A67B31C5EA5DF8ED",
       "material": None, "color": None}
sid, how, backed = f.resolve(rec, SPOOLS)
check("bambu: uid -> spool 7", sid == 7 and backed, how)
sid, how, backed = f.resolve(rec, None)
check("bambu: unresolved with no backend, honestly", sid is None and not backed, how)

print("\nSCENARIO 3d - Multi-vendor on-tag data extraction when backend is unavailable")
# 1. Bambu Lab on-tag metadata extraction
bambu_raw = b"GFA01" + b"\x00" * 27
b_rec = f.parse(bambu_raw)
b_sid, b_how, b_backed = f.resolve(b_rec, None)
check("bambu: brand on tag preserved", b_rec.get("brand") == "Bambu Lab", b_rec.get("brand"))
check("bambu: material on tag preserved", b_rec.get("material") == "PLA Matte", b_rec.get("material"))
check("bambu: name formatted from tag", b_rec.get("name") == "Bambu Lab PLA Matte", b_rec.get("name"))
check("bambu: temps on tag preserved", b_rec.get("temp_min") == 190 and b_rec.get("temp_max") == 230,
      f"{b_rec.get('temp_min')}-{b_rec.get('temp_max')}")

# 2. Creality CFS on-tag metadata extraction
creality_raw = b"CR-PLA:#FF0000:210:60:batch99"
c_rec = f.parse(creality_raw)
c_sid, c_how, c_backed = f.resolve(c_rec, None)
check("creality: brand preserved", c_rec.get("brand") == "Creality", c_rec.get("brand"))
check("creality: material preserved", c_rec.get("material") == "PLA", c_rec.get("material"))
check("creality: color preserved", c_rec.get("color") == "FF0000", c_rec.get("color"))
check("creality: name formatted", c_rec.get("name") == "Creality PLA", c_rec.get("name"))

# 3. Prusament on-tag metadata extraction
prusa_raw = b"Prusament PETG #00FF88 240C"
p_rec = f.parse(prusa_raw)
p_sid, p_how, p_backed = f.resolve(p_rec, None)
check("prusament: brand preserved", p_rec.get("brand") == "Prusament", p_rec.get("brand"))
check("prusament: material preserved", p_rec.get("material") == "PETG", p_rec.get("material"))
check("prusament: color preserved", p_rec.get("color") == "00FF88", p_rec.get("color"))
check("prusament: name formatted", p_rec.get("name") == "Prusament PETG", p_rec.get("name"))

bad = [r for r in results if not r[1]]
print("\n%d checks, %d failed" % (len(results), len(bad)))
raise SystemExit(1 if bad else 0)

