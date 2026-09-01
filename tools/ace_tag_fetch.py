"""Fetch the raw tag image that V1.1.3X caches for a non-Anycubic tag, and identify its format.

Read-only. Moves nothing: the image is already in the firmware's page buffer, put there by
rawtag_stub.s at the moment the tag was identified.

HOW THE IMAGE GETS THERE. The firmware decodes exactly one layout - page 4 must begin
7B 00 65 00 - and parses every field at a fixed offset after that. A tag in any other layout
still selects and reads perfectly, then gets parsed at Anycubic's offsets and returns garbage
as SUCCESS. rawtag_stub.s intercepts one instruction BEFORE that parse: Anycubic tags branch
into the original code untouched, and anything else has its raw image copied to 0x20000704 and
is reported as **version 0x0202**. So 0x0202 in an identify response means "this tag is not
Anycubic-format, and its bytes are waiting for you".

WHY op 9 AND NOT op 4. op 4 masks its offset to 6 bits and starts at BUF+64, so it can only
reach bytes 64..127 - which is exactly the range that does NOT hold the sku, brand and material
pages. op 9 takes the full 8-bit offset. That was the op7/op4 defect; op 9 is its fix.

    python ace_tag_fetch.py --slot 1
    python ace_tag_fetch.py --slot 1 --json tag.json
"""
import argparse
import binascii
import json
import re
import sys
import time
import urllib.parse
import urllib.request

B = "http://10.49.9.130:7125"
IMAGE_LEN = 144          # pages 4..39 - all the firmware's own bulk read (op 7) can reach
PAGE_CAP = 225           # NTAG216's last user page; the walk stops when the tag stops answering
RAW_SENTINEL = 0x0202


def _post(script, timeout=90):
    urllib.request.urlopen(urllib.request.Request(
        B + "/printer/gcode/script?script=" + urllib.parse.quote(script),
        method="POST"), timeout=timeout).read()


def _store(count=400):
    r = urllib.request.urlopen(B + "/server/gcode_store?count=%d" % count, timeout=25)
    return json.load(r)["result"]["gcode_store"]


def packed(reader, op, arg1, arg2=0):
    return 0x80000000 | (reader << 24) | (op << 16) | ((arg1 & 0xFF) << 8) | (arg2 & 0xFF)


def _op(reader, op, arg1, arg2=0):
    """One passthrough op, returning the byte the firmware puts in `code`."""
    import re as _re
    idx = packed(reader, op, arg1, arg2)
    t = time.time()
    _post("ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX=%d" % idx)
    time.sleep(0.6)
    for g in _store(60):
        if g["time"] < t - 0.3:
            continue
        m = _re.search(r"\{'index': (\d+)\}.*?'code': (\d+)", g["message"])
        if m and int(m.group(1)) == idx:
            return int(m.group(2))
    return None


def read_image(reader, length=IMAGE_LEN, batch=24):
    """op 9 each byte of the page buffer. Batched, then matched by index in the reply text."""
    out = {}
    for start in range(0, length, batch):
        idxs = [packed(reader, 9, off) for off in range(start, min(start + batch, length))]
        t = time.time()
        _post("\n".join("ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX=%d\nG4 P8" % i for i in idxs))
        time.sleep(0.45 + 0.02 * len(idxs))
        for g in _store():
            if g["time"] < t - 0.3:
                continue
            m = re.search(r"\{'index': (\d+)\}.*?'code': (\d+)", g["message"])
            if not m:
                continue
            idx, code = int(m.group(1)), int(m.group(2))
            if idx in idxs:
                out[(idx >> 8) & 0xFF] = code & 0xFF
    return bytes(out.get(i, 0) for i in range(length)), len(out)


def read_all_pages(reader, first=4, last=PAGE_CAP):
    """Read every page the tag will give us, and let the parser decide what it needs.

    Deliberately NOT bounded by the firmware's 144-byte bulk read. op 7 stops at page 39 because
    that is all an NTAG213 holds, but tags are bigger than that and the useful field may sit
    anywhere. Measured on this machine: a real OpenSpool tag declares an NDEF TLV of 0xAF = 175
    bytes, and everything up to "max_temp" fits in the first 144 while the IDENTITY does not:

        ..."max_temp":"220","spool_id":26,"sm_id":26}

    A reader that stops where op 7 stops therefore recovers the colour, material and
    temperatures and misses the one field that says which spool it is. Guessing a larger fixed
    bound just moves the cliff, so this walks until the tag stops answering - which is the tag
    telling us where its memory ends rather than us assuming it.

    One NTAG READ returns 16 bytes (four pages). Re-SELECT before each one: without it the tag
    stops answering after the first transceive while the RX region still holds the previous
    reply, which is indistinguishable from a successful read of the wrong page. The giveaway is
    the same 16 bytes repeating every four pages.
    """
    out = bytearray()
    for page in range(first, last + 1, 4):
        _op(reader, 6, 0)
        _op(reader, 2, 0, 0x30)
        _op(reader, 2, 1, page)
        _op(reader, 3, 2, 0x0C)
        vals = [_op(reader, 9, 64 + i) for i in range(16)]
        if not any(v for v in vals if v):
            break                      # end of memory, or the tag left the field
        out.extend(v or 0 for v in vals)
    return bytes(out)


def sniff(img):
    """Name the layout from the bytes themselves."""
    if img[:4] == b"\x7b\x00\x65\x00":
        return "anycubic", "page 4 magic 123 / version 101"
    text = img.decode("latin-1", "replace")
    if img[:1] == b"\x03":                      # NDEF TLV
        low = text.lower()
        if "openspool" in low:
            return "openspool", "NDEF TLV + openspool protocol marker"
        if "application/json" in text:
            return "ndef-json", "NDEF TLV + application/json MIME record"
        return "ndef", "NDEF TLV, record type not recognised"
    if "{" in text and '"' in text:
        return "json?", "no NDEF TLV, but JSON-looking content"
    return "unknown", "no recognised signature"


def extract_json(img):
    """Pull the first balanced {...} out of the image, tolerating trailing padding."""
    text = img.decode("latin-1", "replace")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, required=True)
    ap.add_argument("--json", help="write the image and any parsed fields here")
    ap.add_argument("--live", action="store_true",
                    help="SELECT the tag and bulk-read it into the buffer first, instead of "
                         "using whatever the last automatic read left there. Needs the tag "
                         "lined up with the antenna; does not move the spool.")
    args = ap.parse_args()

    reader = args.slot // 2
    print("slot %d -> reader %d" % (args.slot, reader))

    if args.live:
        # op 6 SELECT then op 7 bulk page read. This is the deterministic route once the tag is
        # physically lined up with the coil: it does not depend on the ACE choosing to re-scan,
        # which it will not do while it holds a cached decode - and it does not clear that cache
        # on eject either. op 7 returns 144 (0x90) as a BYTE COUNT, not a status.
        sel = _op(reader, 6, 0)
        cnt = _op(reader, 7, 0)
        print("SELECT -> %s   bulk page read -> %s bytes" % (sel, cnt))
        if sel not in (0, None):
            print("  SELECT did not succeed: 3 = no tag in the field, 4 = two tags in range.")
            print("  Line the tag up with the antenna and retry.")
            return 2
        if not cnt or cnt < 140:
            print("  the read returned too few bytes - the tag moved out of the field")
            return 2

    if args.live:
        # Live: walk the whole tag. No 144-byte special case, no second code path.
        img = read_all_pages(reader)
        got = len(img)
        print("read %d bytes (pages 4..%d) straight from the tag" % (got, 4 + got // 4 - 1))
    else:
        img, got = read_image(reader)
        print("recovered %d/%d bytes from the page buffer" % (got, IMAGE_LEN))
    fmt, why = sniff(img)
    print("format: %s (%s)\n" % (fmt, why))

    for off in range(0, len(img), 16):
        chunk = img[off:off + 16]
        printable = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        print("  %3d  %-32s |%s|" % (4 + off // 4, binascii.hexlify(chunk).decode(), printable))

    parsed = extract_json(img)
    if parsed:
        print("\nparsed JSON:")
        for k in sorted(parsed):
            print("   %-14s %r" % (k, parsed[k]))
        # SKU is the fast identity path: same on both faces of a spool, and it IS the spool
        # number - so it resolves without any backend call and works with FilaMan unreachable.
        sku = parsed.get("sku") or parsed.get("SKU")
        if sku:
            print("\n   -> SKU %r : resolve directly to that spool, no backend lookup needed" % sku)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"slot": args.slot, "format": fmt, "bytes_recovered": got,
                       "image_hex": binascii.hexlify(img).decode(), "parsed": parsed}, fh, indent=2)
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
