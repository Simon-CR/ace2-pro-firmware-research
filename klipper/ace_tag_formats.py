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
    """The first balanced {...} that actually PARSES, tolerating the NDEF header and padding.

    Every candidate "{" is tried, not just the first one. An NDEF TLV is 0x03 followed by a
    LENGTH BYTE, and that length byte can itself be 0x7B - which is "{". A payload that happens
    to be 123 bytes long therefore puts a false opening brace two bytes into the image, and
    anchoring on the first "{" then counts braces from the wrong place and never closes. Caught
    on a synthetic openprinttag tag whose body was exactly 123 bytes; nothing about it is
    synthetic, it would do the same on a real tag of that length.
    """
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
                        break          # this "{" did not start a real object - try the next
        start = text.find("{", start + 1)
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


def _repair_json(img):
    """Best-effort parse of a TRUNCATED JSON object.

    Not a nicety - a structural necessity. The firmware reads pages 4..39 = 144 bytes, and a
    real FilaMan/OpenSpool message is 177: the closing brace sits at ~byte 176, so the head can
    be recovered PERFECTLY and still never contain a complete object. Measured on this machine -
    every field up to "max_temp" is present in the head; the brace and the identity are not.

    Truncates back to the last complete "key": value pair and closes the object. Fields that
    were cut off mid-value are dropped, never guessed.
    """
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
    """Identity, by every route in order of preference. Returns (spool_id, how, backed).

    `spools` is FilaMan's spool list when the backend is reachable, or None when it is not.
    `backed` says whether the answer was confirmed against the backend, which is what decides
    whether the caller may trust the backend's fields over the tag's.

    THREE SCENARIOS, ALL OF WHICH MUST WORK, FOR EVERY FORMAT:

      1. SKU matched against the backend. The tag carries the spool number, and the backend
         confirms that spool exists. Best case: authoritative data, and the number itself needed
         no network to obtain.
      2. UID matched against the backend. The tag carries no spool number - a stock Anycubic or
         Bambu tag, say - so identity comes from the UID, checked against BOTH rfid_uid and the
         previous_tag custom field. Either face of a two-sided spool must land on the same spool.
      3. Neither, or no backend at all. The tag's own fields still render the lane: colour,
         material, temperatures. A tag that identifies a spool the backend has never heard of is
         the same case - the number is real, it just cannot be confirmed.

    A SKU that the backend does not recognise deliberately does NOT fall through to the UID: the
    tag says which spool it is, and quietly binding a different one because a lookup missed would
    be worse than saying so.

    R6 - the UID is matched against EVERY spool (both rfid_uid and the previous_tag custom field)
    and ALL distinct spool ids that match are collected. More than one distinct id means the UID
    is shared across spools in the backend, and there is no honest way to pick: it refuses rather
    than binding the first one it happened to see.
    """
    sid = spool_from_record(rec)
    if sid is not None:
        if spools is None:
            return sid, "sku %r (backend unreachable - unconfirmed)" % rec.get("sku"), False
        for s in spools:
            if s.get("id") == sid:
                return sid, "sku %r" % rec.get("sku"), True
        return sid, "sku %r (no such spool in the backend)" % rec.get("sku"), False

    uid = normalise_uid(rec.get("uid"))
    if not uid:
        return None, "no spool number and no uid on this tag", False
    if spools is None:
        return None, "uid %s (backend unreachable)" % uid, False
    matches = {}                       # distinct spool id -> how it matched
    for s in spools:
        mid = s.get("id")
        if mid is None:
            continue
        if normalise_uid(s.get("rfid_uid")) == uid:
            matches.setdefault(mid, "rfid_uid %s" % uid)
        elif normalise_uid((s.get("custom_fields") or {}).get("previous_tag")) == uid:
            matches.setdefault(mid, "previous_tag %s" % uid)
    ids = sorted(matches)
    if len(ids) > 1:
        return None, "uid %s ambiguous: spools %s - refusing to guess" % (uid, ids), False
    if len(ids) == 1:
        return ids[0], matches[ids[0]], True
    return None, "uid %s not known to the backend" % uid, False


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
