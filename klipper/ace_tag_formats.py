"""Identify and parse RFID filament tags from their raw bytes, whatever wrote them.

Pure functions, no I/O, no Klipper dependency - so it can be unit-tested against saved dumps and
reused by the command-line tools. Run this file directly to self-test against the known-good
Anycubic image.

WHY THE HOST DOES THIS. The ACE firmware decodes exactly ONE layout: page 4 must begin
7B 00 65 00, and every field after it is read at a FIXED OFFSET. A tag in any other layout still
selects and reads perfectly - it simply is not Anycubic - and the positional parser then returns
whatever bytes land at those offsets, with code 0. A real OpenSpool tag produced
sku='application/json{"' (the NDEF MIME record header), temp=28770C, and hotbed min 8804 > max
8762, reported as SUCCESS. Deciding the format from the bytes themselves is the only thing that
cannot be fooled that way.

V1.1.3X's rawtag_stub.s hands the host those bytes; this module turns them into one record
regardless of who wrote the tag, which is what makes a Creality spool behave like an Anycubic one.

IDENTITY, IN PRIORITY ORDER. `sku` first: both faces of a spool carry the same SKU, it IS the
spool number, it needs no backend call, and it still works with FilaMan unreachable. UID lookup
is the fallback and is genuinely worse - FilaMan has no server-side tag search, and it stores UIDs
in three mutually incompatible formats, so any UID comparison must normalise first.
"""

import binascii
import hashlib
import hmac
import json
import re
import struct

ANYCUBIC_MAGIC = b"\x7b\x00\x65\x00"      # u16 123 (magic), u16 101 (version)
BAMBU_SALT = bytes([0x9a, 0x75, 0x9c, 0xf2, 0xc4, 0xf7, 0xca, 0xff, 0x22, 0x2c, 0xb9, 0x76, 0x9b, 0x41, 0xbc, 0x96])
SKU_RE = re.compile(r"^SM(\d{1,7})$", re.I)


def normalise_uid(uid):
    """Strip separators and case so UIDs written by different tools can be compared.

    Real data from one FilaMan instance, all three in the same table:
        04A27C70C52A81                      bare hex
        04:AB:DD:4F:C9:2A:81                colon-separated
        16C74AC7C93E4614A67B31C5EA5DF8ED    16 bytes
    Comparing these raw finds nothing and reports "no such spool" for a spool that is right there.
    """
    if not uid:
        return ""
    return re.sub(r"[^0-9A-Fa-f]", "", str(uid)).upper()


def _cstr(buf, off, length):
    """A fixed-width NUL-padded field, as Anycubic writes them."""
    raw = bytes(buf[off:off + length])
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def _u16(buf, off):
    return struct.unpack_from("<H", bytes(buf), off)[0] if off + 2 <= len(buf) else 0


def _u32(buf, off):
    return struct.unpack_from("<I", bytes(buf), off)[0] if off + 4 <= len(buf) else 0


def uid_from_image(image, first_page=0):
    """The tag's UID, when the image starts at page/block 0.

    Two on-tag layouts carry the UID, and each has its own check byte:

      7-byte NTAG cascade, UID split across the first two pages by a check byte -
        page 0:  UID0 UID1 UID2 BCC0        BCC0 = 0x88 ^ UID0 ^ UID1 ^ UID2
        page 1:  UID3 UID4 UID5 UID6
      4-byte Mifare Classic manufacturer block, UID then a single check byte -
        block 0: UID0 UID1 UID2 UID3 BCC    BCC  = UID0 ^ UID1 ^ UID2 ^ UID3
      (a real Bambu block 0 is 89 93 34 FC D2 08 04 00 ... -> UID 899334FC, BCC 0xD2).

    DISAMBIGUATION. Both check bytes are validated; the layout is decided by which one holds,
    trying 7-byte FIRST. A genuine 7-byte page 0 satisfies the cascade BCC, so it is returned
    before the 4-byte test can fire; the 4-byte test only runs when the cascade BCC fails, which
    is the case for a Mifare 4-byte block (its byte 3 is the last UID byte, not a cascade BCC).
    The residual risk is a 4-byte block whose bytes happen to satisfy the cascade BCC (~1/256) -
    accepted here, as the contract fixes the 7-byte-first order.

    Returns "" when neither check byte validates (or the image does not start at page 0), because
    the firmware's own read starts at page 4 and the UID is simply not in it - not a failure, just
    nothing host-side to recover from a normal identify.
    """
    img = bytes(image)
    if first_page != 0:
        return ""
    if len(img) >= 8 and (0x88 ^ img[0] ^ img[1] ^ img[2]) == img[3]:
        return "".join("%02X" % b for b in img[0:3] + img[4:8])   # 7-byte cascade
    if len(img) >= 5 and (img[0] ^ img[1] ^ img[2] ^ img[3]) == img[4]:
        return "".join("%02X" % b for b in img[0:4])              # 4-byte Mifare Classic
    return ""


def identify(image):
    """Name the layout from the bytes. Returns (format, why)."""
    img = bytes(image)
    if img[:4] == ANYCUBIC_MAGIC:
        return "anycubic", "page 4 magic 123 / version 101"
    text = img.decode("latin-1", "replace")
    low = text.lower()
    if img[:1] == b"\x03":                      # NDEF TLV: 0x03 <len> <record...>
        if "openspool" in low:
            return "openspool", "NDEF TLV + openspool protocol marker"
        if "filaman" in low:
            return "filaman", "NDEF TLV + filaman marker"
        if "openprinttag" in low or ("opt" in low and '"opt' in low):
            return "openprinttag", "NDEF TLV + openprinttag marker"
        if "application/json" in text:
            return "ndef-json", "NDEF TLV + application/json MIME record"
        return "ndef", "NDEF TLV, record type not recognised"
    if b"{" in img and b'"' in img:
        if "openspool" in low:
            return "openspool", "JSON openspool protocol marker"
        if "openprinttag" in low or "opentag" in low:
            return "openprinttag", "JSON openprinttag marker"
        return "json", "JSON-looking content with no NDEF TLV"
    # Creality CFS tag signature (Sector 1 Blocks 4-6)
    if any(k in low for k in ("creality", "cr-pla", "cr-petg", "cr-abs", "cr-tpu", "hyper pla", "hyper-pla", "ender-pla")):
        return "creality", "Creality CFS sector signature detected"
    # Bambu Lab MIFARE Classic signature (tray_info_idx GFA/GFB/GFG/GFS/GFN/GFU or Bambu text)
    if any(k in low for k in ("bambu", "bambulab", "bambu lab")) or re.search(r"\bGF[ABCGNSU][0-9]{2}\b", text):
        return "bambu", "Bambu Lab MIFARE tag signature detected"
    if len(img) >= 32:
        b1_prefix = img[16:21].decode("ascii", "replace")
        if re.match(r"^GF[ABCGNSU][0-9]{2}$", b1_prefix):
            return "bambu", "Bambu Lab tray_info_idx in Block 1"
    # Prusament tag signature
    if "prusament" in low or "prusa research" in low:
        return "prusament", "Prusament RFID signature detected"
    if len(img) >= 5 and (img[0] ^ img[1] ^ img[2] ^ img[3]) == img[4] and any(img[:4]):
        return "mifare-classic", "4-byte Mifare Classic block 0 (BCC verified)"
    if not any(img):
        return "blank", "all zeroes"
    return "unknown", "no recognised signature"


def _parse_anycubic(img):
    """Anycubic's fixed layout, offsets confirmed against a real tag (SM24, Bambu Lab PLA).

        off   0  u16 magic 123, u16 version 101
        off   4  sku      (20)   'SM24'
        off  24  brand    (20)   'Bambu Lab'
        off  44  material (20)   'PLA'
        off  64  u32 colour, stored so the bytes read A,B,G,R
        off  80  u16 extruder min, u16 extruder max     200 / 220
        off 100  u16 bed min,      u16 bed max           50 / 60
        off 104  u16 diameter x100                      175 -> 1.75mm
        off 108  u32 total grams                        1000
    """
    packed = _u32(img, 64)
    rec = {
        "sku": _cstr(img, 4, 20),
        "brand": _cstr(img, 24, 20),
        "material": _cstr(img, 44, 20),
        "color": "%02X%02X%02X" % ((packed >> 24) & 0xFF, (packed >> 16) & 0xFF,
                                   (packed >> 8) & 0xFF) if packed else None,
        "temp_min": _u16(img, 80) or None,
        "temp_max": _u16(img, 82) or None,
        "bed_min": _u16(img, 100) or None,
        "bed_max": _u16(img, 102) or None,
        "diameter": _u16(img, 104) / 100.0 if _u16(img, 104) else None,
        "total_g": _u32(img, 108) or None,
    }
    return rec


def _parse_creality(img):
    """Parse Creality CFS tag payload (Sector 1 ASCII string or colon-separated fields)."""
    text = bytes(img).decode("latin-1", "replace")
    rec = {"brand": "Creality", "material": None, "color": None, "temp_min": None,
           "temp_max": None, "bed_min": None, "bed_max": None}
    low = text.lower()
    if "hyper" in low and "pla" in low:
        rec["material"] = "Hyper PLA"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 190, 230, 45, 60
    elif "cr-pla" in low or "pla" in low:
        rec["material"] = "PLA"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 190, 230, 45, 60
    elif "cr-petg" in low or "petg" in low:
        rec["material"] = "PETG"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 230, 250, 70, 85
    elif "cr-abs" in low or "abs" in low:
        rec["material"] = "ABS"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 240, 260, 90, 110
    elif "cr-tpu" in low or "tpu" in low:
        rec["material"] = "TPU"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 210, 230, 30, 60

    color_match = re.search(r"#?([0-9A-Fa-f]{6})\b", text)
    if color_match:
        rec["color"] = color_match.group(1).upper()
    return rec


def bambu_kdf(uid_bytes, sector=0):
    """Derive Bambu MIFARE Classic Key A and Key B for a given sector using HKDF-SHA256."""
    uid_b = bytes(uid_bytes)[:4]
    prk = hmac.new(BAMBU_SALT, uid_b, hashlib.sha256).digest()
    sec_byte = bytes([int(sector) & 0xFF])
    key_a = hmac.new(prk, b"RFID-A\0" + sec_byte, hashlib.sha256).digest()[:6]
    key_b = hmac.new(prk, b"RFID-B\0" + sec_byte, hashlib.sha256).digest()[:6]
    return key_a, key_b


BAMBU_CATALOG = {
    # PLA Family
    "GFA00": {"material": "PLA Basic", "temp_min": 190, "temp_max": 230, "bed_min": 45, "bed_max": 60},
    "GFA01": {"material": "PLA Matte", "temp_min": 190, "temp_max": 230, "bed_min": 45, "bed_max": 60},
    "GFA02": {"material": "PLA Metal", "temp_min": 190, "temp_max": 230, "bed_min": 45, "bed_max": 60},
    "GFA03": {"material": "PLA Silk", "temp_min": 200, "temp_max": 240, "bed_min": 45, "bed_max": 60},
    "GFA05": {"material": "PLA Tough", "temp_min": 190, "temp_max": 230, "bed_min": 45, "bed_max": 60},
    "GFA07": {"material": "PLA Galaxy", "temp_min": 190, "temp_max": 230, "bed_min": 45, "bed_max": 60},
    "GFA08": {"material": "PLA Aero", "temp_min": 220, "temp_max": 250, "bed_min": 45, "bed_max": 60},
    "GFA09": {"material": "PLA Marble", "temp_min": 190, "temp_max": 230, "bed_min": 45, "bed_max": 60},
    "GFA50": {"material": "PLA-CF", "temp_min": 210, "temp_max": 240, "bed_min": 45, "bed_max": 60},
    # PETG Family
    "GFG00": {"material": "PETG Basic", "temp_min": 230, "temp_max": 260, "bed_min": 70, "bed_max": 80},
    "GFG01": {"material": "PETG Translucent", "temp_min": 230, "temp_max": 260, "bed_min": 70, "bed_max": 80},
    "GFG50": {"material": "PETG-CF", "temp_min": 240, "temp_max": 270, "bed_min": 70, "bed_max": 85},
    # ABS / ASA Family
    "GFB00": {"material": "ABS", "temp_min": 240, "temp_max": 270, "bed_min": 90, "bed_max": 100},
    "GFB01": {"material": "ASA", "temp_min": 250, "temp_max": 280, "bed_min": 90, "bed_max": 100},
    "GFB02": {"material": "PC", "temp_min": 260, "temp_max": 290, "bed_min": 90, "bed_max": 110},
    # TPU Family
    "GFS00": {"material": "TPU 95A", "temp_min": 220, "temp_max": 240, "bed_min": 35, "bed_max": 50},
    "GFS01": {"material": "TPU 95A HF", "temp_min": 220, "temp_max": 240, "bed_min": 35, "bed_max": 50},
    # Engineering / Carbon Fiber
    "GFN03": {"material": "PA6-CF", "temp_min": 280, "temp_max": 300, "bed_min": 90, "bed_max": 110},
    "GFN05": {"material": "PAHT-CF", "temp_min": 280, "temp_max": 300, "bed_min": 90, "bed_max": 110},
    "GFC00": {"material": "PC", "temp_min": 260, "temp_max": 290, "bed_min": 90, "bed_max": 110},
    # Support
    "GFU01": {"material": "Support for PLA", "temp_min": 190, "temp_max": 230, "bed_min": 45, "bed_max": 60},
    "GFU02": {"material": "Support for PA/PET", "temp_min": 260, "temp_max": 280, "bed_min": 80, "bed_max": 90},
}


def _parse_bambu(img):
    """Parse Bambu Lab tag data from MIFARE Classic blocks or raw image."""
    raw = bytes(img)
    text = raw.decode("latin-1", "replace")
    rec = {
        "brand": "Bambu Lab",
        "material": None,
        "color": None,
        "temp_min": None,
        "temp_max": None,
        "bed_min": None,
        "bed_max": None,
        "diameter": 1.75,
        "total_g": 1000,
        "tray_info_idx": None,
    }

    # 1. Check for tray_info_idx in text or Block 1 (bytes 16..31)
    idx_match = re.search(r"\b(GF[ABCGNSU][0-9]{2})\b", text)
    tray_idx = None
    if idx_match:
        tray_idx = idx_match.group(1)
    elif len(raw) >= 32:
        cand = raw[16:21].decode("ascii", "replace")
        if re.match(r"^GF[ABCGNSU][0-9]{2}$", cand):
            tray_idx = cand

    if tray_idx:
        rec["tray_info_idx"] = tray_idx
        if tray_idx in BAMBU_CATALOG:
            cat = BAMBU_CATALOG[tray_idx]
            rec["material"] = cat["material"]
            rec["temp_min"] = cat["temp_min"]
            rec["temp_max"] = cat["temp_max"]
            rec["bed_min"] = cat["bed_min"]
            rec["bed_max"] = cat["bed_max"]

    # 2. Binary sector mapping (Sector 1 Blocks 4, 5, 6)
    b4_off = None
    if len(raw) >= 112:
        b4_off = 64
    elif len(raw) >= 48 and not any(raw[:4]):
        b4_off = 0

    if b4_off is not None:
        b4 = _cstr(raw, b4_off, 16)
        if b4 and any(c.isalnum() for c in b4):
            rec["material"] = b4

        # Block 5: Weight (0:2), RGBA (2:6), Diameter (6:8)
        b5_off = b4_off + 16
        w = _u16(raw, b5_off)
        if 200 <= w <= 5000:
            rec["total_g"] = w
        r, g, b = raw[b5_off + 2], raw[b5_off + 3], raw[b5_off + 4]
        if (r, g, b) != (0, 0, 0) or raw[b5_off + 5] != 0:
            rec["color"] = "%02X%02X%02X" % (r, g, b)
        d = _u16(raw, b5_off + 6)
        if 100 <= d <= 300:
            rec["diameter"] = d / 100.0

        # Block 6: Nozzle min (0:2), Nozzle max (2:4), Bed min (4:6), Bed max (6:8)
        b6_off = b4_off + 32
        tmin = _u16(raw, b6_off)
        tmax = _u16(raw, b6_off + 2)
        bmin = _u16(raw, b6_off + 4)
        bmax = _u16(raw, b6_off + 6)
        if 150 <= tmin <= 350 and 150 <= tmax <= 350 and tmin <= tmax:
            rec["temp_min"] = tmin
            rec["temp_max"] = tmax
        if 20 <= bmin <= 130 and 20 <= bmax <= 130 and bmin <= bmax:
            rec["bed_min"] = bmin
            rec["bed_max"] = bmax

    # 3. Fallback to ASCII keyword matching if still unknown
    if not rec["material"]:
        low = text.lower()
        if "pla matte" in low: rec["material"] = "PLA Matte"
        elif "pla basic" in low: rec["material"] = "PLA Basic"
        elif "pla silk" in low: rec["material"] = "PLA Silk"
        elif "pla tough" in low: rec["material"] = "PLA Tough"
        elif "pla-cf" in low: rec["material"] = "PLA-CF"
        elif "pla" in low: rec["material"] = "PLA"
        elif "petg-cf" in low: rec["material"] = "PETG-CF"
        elif "petg" in low: rec["material"] = "PETG Basic"
        elif "abs" in low: rec["material"] = "ABS"
        elif "asa" in low: rec["material"] = "ASA"
        elif "tpu" in low: rec["material"] = "TPU 95A"
        elif "pc" in low: rec["material"] = "PC"
        elif "pa-cf" in low or "pa6-cf" in low: rec["material"] = "PA6-CF"

    return rec


def _parse_prusament(img):
    """Parse Prusament NFC tag payload."""
    text = bytes(img).decode("latin-1", "replace")
    rec = {
        "brand": "Prusament",
        "material": None,
        "color": None,
        "temp_min": None,
        "temp_max": None,
        "bed_min": None,
        "bed_max": None,
        "diameter": 1.75,
        "total_g": 1000,
    }
    low = text.lower()
    if "pc blend" in low:
        rec["material"] = "PC Blend"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 265, 285, 100, 115
    elif "pvb" in low:
        rec["material"] = "PVB"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 205, 225, 65, 75
    elif "petg" in low:
        rec["material"] = "PETG"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 240, 260, 80, 90
    elif "asa" in low:
        rec["material"] = "ASA"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 255, 270, 105, 115
    elif "pla" in low:
        rec["material"] = "PLA"
        rec["temp_min"], rec["temp_max"], rec["bed_min"], rec["bed_max"] = 205, 225, 50, 60

    color_match = re.search(r"#?([0-9A-Fa-f]{6})\b", text)
    if color_match:
        rec["color"] = color_match.group(1).upper()
    return rec


# JSON keys in the wild across OpenSpool, FilaMan, Spoolman NFC, OpenPrintTag:
_JSON_KEYS = {
    "sku": ("sku", "SKU", "spool_id", "spoolId", "spool", "sm_id", "spoolman_id", "id"),
    "brand": ("brand", "manufacturer", "vendor"),
    "material": ("material", "type", "filament_type"),
    "color": ("color", "color_hex", "colour", "hex"),
    "temp_min": ("temp_min", "min_temp", "extruder_min", "print_temp_min"),
    "temp_max": ("temp_max", "max_temp", "extruder_max", "print_temp_max"),
    "bed_min": ("bed_min", "bed_temp_min"),
    "bed_max": ("bed_max", "bed_temp_max"),
    "diameter": ("diameter", "filament_diameter"),
    "total_g": ("total_g", "weight", "total_weight", "spool_weight"),
}


def _first_json(img):
    """Parse the first valid JSON object in a byte sequence."""
    text = bytes(img).decode("latin-1", "replace")
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def _repair_json(img):
    text = bytes(img).decode("latin-1", "replace")
    start = text.find("{")
    while start >= 0:
        best = None
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            elif ch == "," and depth == 1:
                try:
                    best = json.loads(text[start:i] + "}")
                except ValueError:
                    pass
        if best:
            return best
        start = text.find("{", start + 1)
    return None


def _parse_json_family(img):
    obj = _first_json(img)
    if obj is None:
        obj = _repair_json(img)
        if obj is not None:
            obj["_truncated"] = True
    if obj is None:
        return None
    lowered = {str(k).lower(): v for k, v in obj.items()}
    rec = {}
    for field, names in _JSON_KEYS.items():
        for n in names:
            if n.lower() in lowered and lowered[n.lower()] not in (None, ""):
                rec[field] = lowered[n.lower()]
                break
        else:
            rec[field] = None
    if isinstance(rec.get("color"), str):
        rec["color"] = rec["color"].lstrip("#").upper() or None
    rec["_raw_json"] = obj
    return rec


def parse(image):
    """Raw tag bytes -> one normalised record, whatever wrote the tag."""
    img = bytes(image)
    fmt, why = identify(img)
    rec = {"format": fmt, "why": why, "sku": None, "brand": None, "material": None,
           "color": None, "temp_min": None, "temp_max": None, "bed_min": None,
           "bed_max": None, "diameter": None, "total_g": None}
    if fmt == "anycubic":
        rec.update(_parse_anycubic(img))
    elif fmt == "creality":
        rec.update(_parse_creality(img))
    elif fmt == "bambu":
        rec.update(_parse_bambu(img))
    elif fmt == "prusament":
        rec.update(_parse_prusament(img))
    elif fmt in ("mifare-classic", "bambu-uid", "raw-uid"):
        uid = "".join("%02X" % b for b in img[0:4])
        rec.update({
            "uid": uid,
            "sku": uid,
            "brand": "Generic",
            "material": "Unknown",
            "color": "808080",
            "temp_min": None,
            "temp_max": None,
            "bed_min": None,
            "bed_max": None,
        })
    elif fmt in ("openspool", "filaman", "openprinttag", "ndef-json", "ndef", "json"):
        parsed = _parse_json_family(img)
        if parsed:
            rec.update(parsed)
    rec["format"] = fmt
    rec["why"] = why
    return rec


def ensure_basic_metadata(rec):
    """Guarantees basic renderable metadata (material, color, brand, temps) even for
    unregistered, unconfirmed, or raw-UID spools missing from the database.

    Ensures Scenario 3 ('unconfirmed / raw tag info') always provides an honest visual
    representation preserving whatever pertinent info (brand, material, color, temps)
    is present on the tag without inventing fake materials or colors.
    """
    out = dict(rec or {})
    uid = normalise_uid(out.get("uid"))
    brand = out.get("brand")
    material = out.get("material")
    color = out.get("color")
    color_name = out.get("color_name")
    sku = out.get("sku")

    if not brand:
        out["brand"] = "Generic"
    if not material:
        out["material"] = "Unknown"
    if not color:
        out["color"] = "808080"
    if not color_name:
        out["color_name"] = ""

    # Name construction: prioritize real on-tag metadata; never mask known brand/material
    if not out.get("name"):
        has_real_brand = out["brand"] and out["brand"].lower() != "generic"
        has_real_material = out["material"] and out["material"].lower() != "unknown"

        if has_real_brand and has_real_material:
            if color_name:
                out["name"] = f"{out['brand']} {out['material']} ({color_name})"
            elif sku and not str(sku).startswith("0x") and len(str(sku)) < 12 and str(sku) != uid:
                out["name"] = f"{out['brand']} {out['material']} #{sku}"
            else:
                out["name"] = f"{out['brand']} {out['material']}"
        elif has_real_material:
            if color_name:
                out["name"] = f"{out['material']} ({color_name})"
            else:
                out["name"] = f"{out['material']}"
        elif has_real_brand:
            out["name"] = f"{out['brand']} ({uid})" if uid else f"{out['brand']}"
        else:
            out["name"] = f"Unidentified Tag ({uid})" if uid else "Unidentified Tag"

    return out


def uid_matches(tag_uid, candidate_uid):
    """Robust ISO 14443-3 Type A UID comparison.

    Handles:
      1. Exact match after normalization (strip punctuation/spaces, uppercase).
      2. Cascade Level 1 truncation: An NTAG or MIFARE 7-byte tag returning its 4-byte
         anticollision frame (starts with 0x88 Cascade Tag, e.g. 8804ABDD) matches
         the full 7-byte UID (04ABDD4FC92A81) whose first 3 bytes (04ABDD) align.
      3. Reverse comparison if either side carries the cascade tag or truncated bytes.
    """
    t = normalise_uid(tag_uid)
    c = normalise_uid(candidate_uid)
    if not t or not c:
        return False
    if t == c:
        return True

    # 8-char hex starting with 88 = Cascade Level 1 CT + 3 UID bytes (e.g. 88 04 AB DD)
    if len(t) == 8 and t.startswith("88"):
        prefix = t[2:]  # 6 hex chars = 3 bytes
        if len(c) >= 6 and c.startswith(prefix):
            return True
    if len(c) == 8 and c.startswith("88"):
        prefix = c[2:]
        if len(t) >= 6 and t.startswith(prefix):
            return True

    # Truncated 4-byte read of 7-byte UID without 88 prefix
    if len(t) == 8 and len(c) >= 14 and c.startswith(t):
        return True
    if len(c) == 8 and len(t) >= 14 and t.startswith(c):
        return True

    return False


def spool_from_record(rec):
    """The spool number, if the tag carries it. Returns int or None.

    Accepts the SM<n> form Anycubic-layout tags use and a bare number, which is what a JSON tag
    writes when the field is literally the spool id. Also accepts spool_id, spoolId, sm_id,
    spoolman_id, id. Deliberately does NOT accept FM<n>: that is a FilaMan ARTICLE number,
    not a spool - one lane carried FM1676 while its spool was 22, so matching it would bind the
    wrong spool silently.
    """
    if not isinstance(rec, dict):
        return None
    for key in ("sku", "SKU", "spool_id", "spoolId", "spool", "sm_id", "spoolman_id", "id"):
        val = rec.get(key)
        if val is None:
            continue
        s = str(val).strip()
        m = SKU_RE.match(s)
        if m:
            return int(m.group(1))
        if s.isdigit() and int(s) > 0:
            return int(s)
    return None


def resolve(rec, spools=None):
    """Identity, by every route in order of preference. Returns (spool_id, how, backed).

    `spools` is FilaMan's spool list when the backend is reachable, or None when it is not.
    `backed` says whether the answer was confirmed against the backend, which is what decides
    whether the caller may trust the backend's fields over the tag's.

    THREE SCENARIOS, ALL OF WHICH MUST WORK, FOR EVERY FORMAT:

      1. SKU / Spool ID matched against the backend. The tag carries the spool number (e.g. Anycubic
         SM<n>, OpenSpool sm_id, Spoolman spool_id), and the backend confirms that spool exists.
      2. UID matched against the backend. The tag carries no embedded spool number - a Bambu,
         stock Anycubic, or raw NTAG sticker - so identity comes from the UID, checked against
         rfid_uid, rfid_uid_2, previous_tag, tag, and spoolman_extra.nfc_spool_uuid.
      3. Neither, or no backend at all. The tag's own fields still render the lane: colour,
         material, temperatures. An unconfirmed tag is rendered honestly as 'Unidentified Tag (<uid>)'.

    R6 - the UID is matched against EVERY spool and ALL distinct spool ids that match are collected.
    More than one distinct id means the UID is shared across spools in the backend, and there is no
    honest way to pick: it refuses rather than binding the first one it happened to see.
    """
    res = None
    sid = spool_from_record(rec)
    if sid is not None:
        if spools is None:
            res = (sid, "sku %r (backend unreachable - unconfirmed)" % (rec.get("sku") or sid), False)
        else:
            for s in spools:
                if s.get("id") == sid:
                    return sid, "sku %r" % (rec.get("sku") or sid), True
            res = (sid, "sku %r (no such spool in the backend)" % (rec.get("sku") or sid), False)

    if res is None:
        uid = normalise_uid(rec.get("uid"))
        if not uid:
            res = (None, "no spool number and no uid on this tag", False)
        elif spools is None:
            res = (None, "uid %s (backend unreachable)" % uid, False)
        else:
            matches = {}                       # distinct spool id -> how it matched
            for s in spools:
                mid = s.get("id")
                if mid is None:
                    continue

                cand_rfid = s.get("rfid_uid")
                cand_rfid2 = s.get("rfid_uid_2")
                cf = s.get("custom_fields") or {}
                cand_prev = cf.get("previous_tag")
                cand_tag = cf.get("tag")
                cand_nfc = (cf.get("spoolman_extra") or {}).get("nfc_spool_uuid")
                cand_nfc_id = cf.get("nfc_id")

                if cand_rfid and uid_matches(uid, cand_rfid):
                    matches.setdefault(mid, "rfid_uid %s" % normalise_uid(cand_rfid))
                elif cand_rfid2 and uid_matches(uid, cand_rfid2):
                    matches.setdefault(mid, "rfid_uid_2 %s" % normalise_uid(cand_rfid2))
                elif cand_prev and uid_matches(uid, cand_prev):
                    matches.setdefault(mid, "previous_tag %s" % normalise_uid(cand_prev))
                elif cand_tag and uid_matches(uid, cand_tag):
                    matches.setdefault(mid, "custom_tag %s" % normalise_uid(cand_tag))
                elif cand_nfc and uid_matches(uid, cand_nfc):
                    matches.setdefault(mid, "nfc_spool_uuid %s" % normalise_uid(cand_nfc))
                elif cand_nfc_id and uid_matches(uid, cand_nfc_id):
                    matches.setdefault(mid, "nfc_id %s" % normalise_uid(cand_nfc_id))

            ids = sorted(matches)
            if len(ids) > 1:
                res = (None, "uid %s ambiguous: spools %s - refusing to guess" % (uid, ids), False)
            elif len(ids) == 1:
                return ids[0], matches[ids[0]], True
            else:
                res = (None, "uid %s not known to the backend" % uid, False)

    if isinstance(rec, dict):
        rec.update(ensure_basic_metadata(rec))
    return res


def _ndef_tlv_length(img):
    """(declared payload length, payload start offset) for an NDEF TLV, or None.

    An NDEF-message TLV is 0x03 then a length: one byte for 0x00..0xFE, or the 0xFF escape
    followed by a two-byte big-endian length. Returns None when the framing is not present.
    """
    if len(img) < 2 or img[0] != 0x03:
        return None
    if img[1] == 0xFF:
        if len(img) < 4:
            return None
        return (img[2] << 8) | img[3], 4
    return img[1], 2


def ndef_is_intact(image):
    """True iff an NDEF/JSON image is structurally coherent, not a torn/spliced buffer.

    The raw op-9 walk takes ~2.88s and a background scan can splice it, handing back a buffer
    whose halves came from different reads. Three checks catch that: it must start with the NDEF
    TLV tag 0x03, the TLV's declared payload length must FIT inside the buffer we were handed (a
    spliced/short read declares more than it delivers), and the payload must still parse as JSON
    (whole, or repaired back to its last complete field). A bare-JSON image with no 0x03 TLV
    framing is treated as not-intact - safe, since the caller then renders from the tag fields
    rather than trusting the image for a bind.
    """
    img = bytes(image)
    parsed = _ndef_tlv_length(img)
    if parsed is None:
        return False
    length, start = parsed
    if length <= 0 or start + length > len(img):
        return False
    payload = img[start:start + length]
    return _first_json(payload) is not None or _repair_json(payload) is not None


def image_is_intact(image):
    """The single gate instance.py calls to decide "is this raw image safe to trust".

    Dispatches by format: an Anycubic-magic image has its own fixed-layout integrity and is
    trusted; an NDEF/JSON image is checked by ndef_is_intact; anything blank or unrecognised is
    not trusted. Pure - no I/O, safe to call on the Klipper side before applying a raw image.
    """
    img = bytes(image)
    if img[:4] == ANYCUBIC_MAGIC:
        return True
    fmt, _why = identify(img)
    if fmt in ("openspool", "filaman", "openprinttag", "ndef-json", "ndef", "json"):
        return ndef_is_intact(img)
    if fmt in ("bambu", "creality", "prusament"):
        return len(img) >= 16 and any(img)
    return False


if __name__ == "__main__":
    import os

    results = []

    def check(label, got, want):
        good = got == want
        print("  %-4s %-26s want %-20r got %r" % ("ok " if good else "FAIL", label, want, got))
        results.append(good)

    # --- Anycubic positional layout, from an inline fixture (no data file needed) ---
    b = bytearray(144)
    b[0:4] = ANYCUBIC_MAGIC
    b[4:8] = b"SM24"
    b[24:33] = b"Bambu Lab"
    b[44:47] = b"PLA"
    b[64:68] = struct.pack("<I", 0xF7D959FF)   # stored A,B,G,R -> reads R=F7 G=D9 B=59
    b[80:82] = struct.pack("<H", 200)
    b[82:84] = struct.pack("<H", 220)
    b[100:102] = struct.pack("<H", 50)
    b[102:104] = struct.pack("<H", 60)
    b[104:106] = struct.pack("<H", 175)
    b[108:112] = struct.pack("<I", 1000)
    arec = parse(bytes(b))
    for k, v in (("format", "anycubic"), ("sku", "SM24"), ("brand", "Bambu Lab"),
                 ("material", "PLA"), ("color", "F7D959"), ("temp_min", 200),
                 ("temp_max", 220), ("bed_min", 50), ("bed_max", 60),
                 ("diameter", 1.75), ("total_g", 1000)):
        check("anycubic.%s" % k, arec.get(k), v)
    check("anycubic.resolve", resolve(arec)[0], 24)

    # --- Optional: the same parse against the real saved dump, only when present ---
    here = os.path.dirname(os.path.abspath(__file__))
    dump = os.path.join(here, "..", "data", "anycubic_ntag_dump.json")
    if os.path.exists(dump):
        pages = json.load(open(dump))
        def _tob(v):
            return binascii.unhexlify(v) if isinstance(v, str) else bytes(v)
        dimg = b"".join(_tob(pages[str(p)]) for p in range(4, 40) if str(p) in pages)
        drec = parse(dimg)
        check("dump.sku", drec.get("sku"), "SM24")
        check("dump.color", drec.get("color"), "F7D959")
        check("dump.resolve", resolve(drec)[0], 24)
    else:
        print("  skip anycubic dump (../data/anycubic_ntag_dump.json absent)")

    # --- A JSON/NDEF tag must not be mistaken for Anycubic, and still yields a spool via sku ---
    ndef = b"\x03\x2aapplication/json" + json.dumps(
        {"protocol": "openspool", "version": "1.0", "sku": "26",
         "type": "PLA", "brand": "Filaments.CA", "color_hex": "#4B2A17"}).encode()
    ndef = ndef.ljust(144, b"\x00")
    jrec = parse(ndef)
    check("json.format", jrec["format"], "openspool")
    check("json.material", jrec["material"], "PLA")
    check("json.color", jrec["color"], "4B2A17")
    check("json.spool", resolve(jrec)[0], 26)

    # --- R3: 4-byte UID from a real Bambu Mifare Classic block 0 ---
    # Bytes 89 93 34 FC -> UID "899334FC"; byte 4 (0xD2) is the BCC and equals XOR(0x89..0xFC),
    # which confirms byte 1 is 0x93. (The build brief's "893934FC" is a transcription slip - its
    # own cited XOR uses 0x93, and 0x39 would make the BCC 0x78, not the 0xD2 in the dump.)
    bambu0 = bytes([137, 147, 52, 252, 210, 8, 4, 0, 5, 225, 214, 83, 195, 200, 189, 144])
    check("uid.4byte", uid_from_image(bambu0, first_page=0), "899334FC")
    # Mifare Classic block 0 parse directly yields basic metadata for unconfirmed spools
    brec = parse(bambu0)
    check("mifare.format", brec["format"], "mifare-classic")
    check("mifare.material", brec["material"], "Unknown")
    check("mifare.brand", brec["brand"], "Generic")
    check("mifare.color", brec["color"], "808080")
    meta_checked = ensure_basic_metadata({"sku": None, "format": "unknown"})
    check("ensure.meta.brand", meta_checked["brand"], "Generic")
    check("ensure.meta.material", meta_checked["material"], "Unknown")

    # Creality CFS tag format
    creality_payload = b"CR-PLA:#FFFFFF:210:60:batch123"
    crec = parse(creality_payload)
    check("creality.format", crec["format"], "creality")
    check("creality.brand", crec["brand"], "Creality")
    check("creality.material", crec["material"], "PLA")
    check("creality.color", crec["color"], "FFFFFF")
    creality_meta = ensure_basic_metadata(crec)
    check("creality.meta.name", creality_meta["name"], "Creality PLA")

    # Bambu Lab tag tests
    # 1. Bambu HKDF-SHA256 key derivation test
    b_uid = bytes([0x89, 0x93, 0x34, 0xFC])
    ka0, kb0 = bambu_kdf(b_uid, sector=0)
    check("bambu.kdf.ka0", ka0.hex(), "a116197896d7")
    check("bambu.kdf.kb0", kb0.hex(), "fbd6ae80f204")

    # 2. Bambu Lab catalog lookup from tray_info_idx (e.g. GFG00 -> PETG Basic)
    bambu_tray = b"GFA00" + b"\x00" * 27
    bambu_rec = parse(bambu_tray)
    check("bambu.catalog.format", bambu_rec["format"], "bambu")
    check("bambu.catalog.brand", bambu_rec["brand"], "Bambu Lab")
    check("bambu.catalog.material", bambu_rec["material"], "PLA Basic")
    check("bambu.catalog.temp_min", bambu_rec["temp_min"], 190)
    check("bambu.catalog.temp_max", bambu_rec["temp_max"], 230)
    bambu_meta = ensure_basic_metadata(bambu_rec)
    check("bambu.meta.name", bambu_meta["name"], "Bambu Lab PLA Basic")

    # 3. Bambu Lab full MIFARE Sector 0 + Sector 1 mock image
    bambu_img = bytearray(112)
    bambu_img[0:4] = b_uid
    bambu_img[4] = 0xD2 # BCC
    bambu_img[16:21] = b"GFA01" # Block 1: tray_info_idx PLA Matte
    bambu_img[64:73] = b"PLA Matte" # Block 4: detailed material string
    # Block 5: weight 1000g (0xE803), color RGBA FF6A00FF (Orange), diameter 1.75 (175 = 0xAF00)
    bambu_img[80:82] = struct.pack("<H", 1000)
    bambu_img[82:86] = bytes([0xFF, 0x6A, 0x00, 0xFF])
    bambu_img[86:88] = struct.pack("<H", 175)
    # Block 6: Nozzle 190/230, Bed 45/60
    bambu_img[96:98] = struct.pack("<H", 190)
    bambu_img[98:100] = struct.pack("<H", 230)
    bambu_img[100:102] = struct.pack("<H", 45)
    bambu_img[102:104] = struct.pack("<H", 60)

    bambu_full_rec = parse(bytes(bambu_img))
    check("bambu.full.format", bambu_full_rec["format"], "bambu")
    check("bambu.full.brand", bambu_full_rec["brand"], "Bambu Lab")
    check("bambu.full.material", bambu_full_rec["material"], "PLA Matte")
    check("bambu.full.color", bambu_full_rec["color"], "FF6A00")
    check("bambu.full.diameter", bambu_full_rec["diameter"], 1.75)
    check("bambu.full.total_g", bambu_full_rec["total_g"], 1000)
    check("bambu.full.temp_min", bambu_full_rec["temp_min"], 190)
    check("bambu.full.temp_max", bambu_full_rec["temp_max"], 230)
    bambu_full_meta = ensure_basic_metadata(bambu_full_rec)
    check("bambu.full.meta.name", bambu_full_meta["name"], "Bambu Lab PLA Matte")

    # 4. Prusament RFID tag format
    prusa_payload = b"Prusament PETG #FF7A00 250C"
    prec = parse(prusa_payload)
    check("prusament.format", prec["format"], "prusament")
    check("prusament.brand", prec["brand"], "Prusament")
    check("prusament.material", prec["material"], "PETG")
    check("prusament.color", prec["color"], "FF7A00")
    prusa_meta = ensure_basic_metadata(prec)
    check("prusament.meta.name", prusa_meta["name"], "Prusament PETG")

    # Cascade Level 1 UID prefix matching (e.g. 8804ABDD against 04:AB:DD:4F:C9:2A:81)
    check("cascade1.match", uid_matches("8804ABDD", "04:AB:DD:4F:C9:2A:81"), True)
    check("cascade1.mismatch", uid_matches("8804ABDD", "04:FF:DD:4F:C9:2A:81"), False)

    # 7-byte NTAG cascade still works (UID 04A27C70C52A81, BCC0 = 0x88^04^A2^7C = 0x52)
    ntag = bytes([0x04, 0xA2, 0x7C, 0x52, 0x70, 0xC5, 0x2A, 0x81])
    check("uid.7byte", uid_from_image(ntag, first_page=0), "04A27C70C52A81")
    check("uid.garbage", uid_from_image(bytes([0, 1, 2, 3, 4, 5, 6, 7]), first_page=0), "")
    check("uid.page!=0", uid_from_image(bambu0, first_page=4), "")

    # --- R6/scenario-2: resolve a UID against a mock spools list (rfid_uid and previous_tag) ---
    spools = [
        {"id": 10, "rfid_uid": "04:A2:7C:70:C5:2A:81", "custom_fields": {}},
        {"id": 26, "rfid_uid": "", "custom_fields": {"previous_tag": "89 93 34 FC"}},
    ]
    r1 = resolve({"sku": None, "uid": "04A27C70C52A81"}, spools)
    check("resolve.rfid_uid.id", r1[0], 10)
    check("resolve.rfid_uid.backed", r1[2], True)
    check("resolve.rfid_uid.how", r1[1], "rfid_uid 04A27C70C52A81")
    r2 = resolve({"sku": None, "uid": "899334FC"}, spools)
    check("resolve.previous_tag.id", r2[0], 26)
    check("resolve.previous_tag.backed", r2[2], True)
    check("resolve.previous_tag.how", r2[1], "previous_tag 899334FC")
    r3 = resolve({"sku": None, "uid": "DEADBEEF"}, spools)
    check("resolve.uid.unknown.id", r3[0], None)
    check("resolve.uid.unknown.backed", r3[2], False)

    # --- R6: two spools share one UID -> refuse, never pick the first ---
    ambig = [
        {"id": 7, "rfid_uid": "DEADBEEF", "custom_fields": {}},
        {"id": 9, "rfid_uid": "", "custom_fields": {"previous_tag": "DE:AD:BE:EF"}},
    ]
    ar = resolve({"sku": None, "uid": "DE AD BE EF"}, ambig)
    check("ambiguous.id", ar[0], None)
    check("ambiguous.backed", ar[2], False)
    check("ambiguous.reason", ("ambiguous" in ar[1]) and ("[7, 9]" in ar[1]), True)
    # The SAME spool matching on BOTH fields is one distinct id, not an ambiguity
    dup = [{"id": 5, "rfid_uid": "CAFE", "custom_fields": {"previous_tag": "CA:FE"}}]
    dr = resolve({"sku": None, "uid": "CAFE"}, dup)
    check("dedup.id", dr[0], 5)
    check("dedup.backed", dr[2], True)
    # spools given but empty -> reachable-but-not-known; spools None -> unreachable
    check("empty.not_known", "not known" in resolve({"sku": None, "uid": "CAFE"}, [])[1], True)
    check("none.unreachable", "unreachable" in resolve({"sku": None, "uid": "CAFE"}, None)[1], True)

    # --- R5: image_is_intact on intact vs torn NDEF, plus anycubic/blank/unknown ---
    fbody = json.dumps({"protocol": "openspool", "version": "1.0", "sku": "26", "type": "PLA",
                        "brand": "Filaments.CA", "color_hex": "4B2A17"}).encode()
    payload = b"application/json" + fbody
    intact = (bytes([0x03, len(payload)]) + payload + b"\xfe").ljust(200, b"\x00")
    check("intact.ndef", image_is_intact(intact), True)
    torn = bytes([0x03, len(payload)]) + payload[: len(payload) // 2]   # declared > delivered
    check("torn.ndef", image_is_intact(torn), False)
    check("intact.anycubic", image_is_intact(bytes(b)), True)
    check("intact.blank", image_is_intact(bytes(16)), False)
    check("intact.unknown", image_is_intact(b"\x99" * 16), False)

    ok = all(results)
    print("\n%s  (%d/%d checks passed)" % ("ALL PASS" if ok else "FAILURES ABOVE",
                                           sum(results), len(results)))
    raise SystemExit(0 if ok else 1)
