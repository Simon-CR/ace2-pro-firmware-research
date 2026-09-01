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
IMAGE_LEN = 144          # pages 4..39
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
    args = ap.parse_args()

    reader = args.slot // 2
    print("slot %d -> reader %d" % (args.slot, reader))

    img, got = read_image(reader)
    print("recovered %d/%d bytes" % (got, IMAGE_LEN))
    fmt, why = sniff(img)
    print("format: %s (%s)\n" % (fmt, why))

    for off in range(0, IMAGE_LEN, 16):
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
