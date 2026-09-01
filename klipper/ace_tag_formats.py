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
import json
import re
import struct

ANYCUBIC_MAGIC = b"\x7b\x00\x65\x00"      # u16 123 (magic), u16 101 (version)
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


def identify(image):
    """Name the layout from the bytes. Returns (format, why)."""
    img = bytes(image)
    if img[:4] == ANYCUBIC_MAGIC:
        return "anycubic", "page 4 magic 123 / version 101"
    if img[:1] == b"\x03":                      # NDEF TLV: 0x03 <len> <record...>
        text = img.decode("latin-1", "replace")
        low = text.lower()
        if "openspool" in low:
            return "openspool", "NDEF TLV + openspool protocol marker"
        if "filaman" in low:
            return "filaman", "NDEF TLV + filaman marker"
        if "openprinttag" in low or "opt" in low and '"opt' in low:
            return "openprinttag", "NDEF TLV + openprinttag marker"
        if "application/json" in text:
            return "ndef-json", "NDEF TLV + application/json MIME record"
        return "ndef", "NDEF TLV, record type not recognised"
    if b"{" in img and b'"' in img:
        return "json", "JSON-looking content with no NDEF TLV"
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
        "diameter": (_u16(img, 104) / 100.0) or None,
        "total_g": _u32(img, 108) or None,
    }
    return rec


def _first_json(img):
    """The first balanced {...}, tolerating the NDEF header before and padding after."""
    text = bytes(img).decode("latin-1", "replace")
    start = text.find("{")
    if start < 0:
        return None
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
                    return None
    return None


# The JSON families (OpenSpool, FilaMan's writer, OpenPrintTag) differ mainly in spelling.
_JSON_KEYS = {
    # Real FilaMan/OpenSpool tag, read off the machine 2026-09-01:
    #   {"protocol":"openspool","version":"1.0","type":"PLA","color_hex":"4B2A17",
    #    "brand":"Filaments.CA","min_temp":"200","max_temp":"220","spool_id":26,"sm_id":26}
    # Both spool_id and sm_id carry the spool number, and both sit BEYOND the 144-byte bulk
    # read - so a reader that stops at page 39 gets everything except the identity.
    "sku": ("sku", "SKU", "spool_id", "spoolId", "spool", "sm_id"),
    "brand": ("brand", "manufacturer", "vendor", "make"),
    "material": ("type", "material", "filament_type", "material_type"),
    "color": ("color_hex", "color", "colour", "hex", "color_hex_1"),
    "temp_min": ("min_temp", "temp_min", "nozzle_min", "extruder_min"),
    "temp_max": ("max_temp", "temp_max", "nozzle_max", "extruder_max"),
    "bed_min": ("bed_min", "min_bed_temp", "bed_temp_min"),
    "bed_max": ("bed_max", "max_bed_temp", "bed_temp_max"),
    "diameter": ("diameter", "filament_diameter"),
    "total_g": ("weight", "total_weight", "spool_weight", "net_weight"),
}


def _parse_json_family(img):
    obj = _first_json(img)
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
    """Raw tag bytes -> one normalised record, whatever wrote the tag.

    Always returns a dict carrying `format`; every other key may be None. A partial record is
    still useful: it renders the lane offline when the backend is unreachable, which is the whole
    point of reading the tag rather than only trusting the backend.
    """
    img = bytes(image)
    fmt, why = identify(img)
    rec = {"format": fmt, "why": why, "sku": None, "brand": None, "material": None,
           "color": None, "temp_min": None, "temp_max": None, "bed_min": None,
           "bed_max": None, "diameter": None, "total_g": None}
    if fmt == "anycubic":
        rec.update(_parse_anycubic(img))
    elif fmt in ("openspool", "filaman", "openprinttag", "ndef-json", "ndef", "json"):
        parsed = _parse_json_family(img)
        if parsed:
            rec.update(parsed)
    rec["format"] = fmt
    rec["why"] = why
    return rec


def spool_from_record(rec):
    """The spool number, if the tag carries it. Returns int or None.

    Accepts the SM<n> form Anycubic-layout tags use and a bare number, which is what a JSON tag
    writes when the field is literally the spool id. Deliberately does NOT accept FM<n>: that is
    a FilaMan ARTICLE number, not a spool - one lane carried FM1676 while its spool was 22, so
    matching it would bind the wrong spool silently.
    """
    sku = rec.get("sku")
    if sku is None:
        return None
    s = str(sku).strip()
    m = SKU_RE.match(s)
    if m:
        return int(m.group(1))
    if s.isdigit():
        return int(s)
    return None


def resolve(rec, spools=None):
    """Best identity available: (spool_id, how). `spools` is FilaMan's spool list, if reachable.

    SKU first - no network, same on both faces of a spool, works with the backend down. UID only
    as a fallback, matched against BOTH rfid_uid and the previous_tag custom field, because a
    spool tagged on both faces keeps its older UID there and either face must resolve to the same
    spool.
    """
    sid = spool_from_record(rec)
    if sid is not None:
        return sid, "sku %r" % rec.get("sku")
    uid = normalise_uid(rec.get("uid"))
    if uid and spools:
        for s in spools:
            if normalise_uid(s.get("rfid_uid")) == uid:
                return s.get("id"), "rfid_uid"
            if normalise_uid((s.get("custom_fields") or {}).get("previous_tag")) == uid:
                return s.get("id"), "previous_tag"
    return None, "unresolved"


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    dump = os.path.join(here, "..", "data", "anycubic_ntag_dump.json")
    pages = json.load(open(dump))
    def _tob(v):
        # saved dumps carry pages either as hex strings or as byte lists
        return binascii.unhexlify(v) if isinstance(v, str) else bytes(v)
    img = b"".join(_tob(pages[str(p)]) for p in range(4, 40) if str(p) in pages)

    rec = parse(img)
    ok = True
    expect = {"format": "anycubic", "sku": "SM24", "brand": "Bambu Lab", "material": "PLA",
              "color": "F7D959", "temp_min": 200, "temp_max": 220, "bed_min": 50,
              "bed_max": 60, "diameter": 1.75, "total_g": 1000}
    for k, v in expect.items():
        got = rec.get(k)
        flag = "ok " if got == v else "FAIL"
        if got != v:
            ok = False
        print("  %-4s %-10s expected %-12r got %r" % (flag, k, v, got))
    sid, how = resolve(rec)
    print("  %-4s resolve    -> spool %r via %s" % ("ok " if sid == 24 else "FAIL", sid, how))
    ok = ok and sid == 24

    # A JSON tag must not be mistaken for Anycubic, and must still yield a spool via sku.
    ndef = b"\x03\x2aapplication/json" + json.dumps(
        {"protocol": "openspool", "version": "1.0", "sku": "26",
         "type": "PLA", "brand": "Filaments.CA", "color_hex": "#4B2A17"}).encode()
    ndef = ndef.ljust(144, b"\x00")
    jrec = parse(ndef)
    jsid, jhow = resolve(jrec)
    for label, got, want in (("format", jrec["format"], "openspool"),
                             ("material", jrec["material"], "PLA"),
                             ("color", jrec["color"], "4B2A17"),
                             ("spool", jsid, 26)):
        flag = "ok " if got == want else "FAIL"
        if got != want:
            ok = False
        print("  %-4s json.%-8s expected %-12r got %r" % (flag, label, want, got))

    print("\n%s" % ("ALL PASS" if ok else "FAILURES ABOVE"))
    raise SystemExit(0 if ok else 1)
