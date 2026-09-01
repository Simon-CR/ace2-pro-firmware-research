"""Dump a complete NTAG image from an ACE 2 Pro bay, and identify the tag's format.

Read-only. Issues RF frames through the V1.1.3W RC522 passthrough and never moves filament.

WHY THIS EXISTS. The firmware only decodes ONE layout: a tag is accepted if page 4 begins
7B 00 65 00 (magic 123, version 101), and everything after that is parsed POSITIONALLY. Any
other tag - OpenSpool, FilaMan's own writer, Creality, OpenPrintTag - still selects and still
reads, then fails the page-4 gate and falls through to the positional parser, which returns
code 0 with garbage that merely looks structured. So the firmware cannot tell us what a foreign
tag says; the host has to read the bytes itself and decide.

WHY IT DOES NOT NEED THE SPOOL TO ROTATE. A tag is only in the antenna's field while it sweeps
past, so a cold stationary read usually fails (192 polls over 45s of hand rotation, all FAILED).
But when the ACE has just returned ANY decode for a slot - including a rejected garbage one -
the tag was necessarily in the field to produce it, and the reader stays powered for ~7.85s
after a scan and re-arms on each identify. Dumping immediately after a decode therefore needs no
rotation at all. That is what makes host-side format support practical rather than a 600mm
spool-turning ritual on every load.

ANTICOLLISION IS NOT OPTIONAL (docs/04-tag-operations.md rule 0). Slots are paired onto one
reader AND one antenna - the identify path throws away the low index bit, (index << 1) & ~2 - so
slots 0+1 share a coil and 2+3 share a coil. With two tags in range you get code 4, or worse a
clean read OF THE WRONG TAG with nothing to indicate it. This script always reports the UID it
selected so the caller can tell which tag answered.

    python ace_tag_dump.py --slot 1
    python ace_tag_dump.py --slot 1 --json out.json
"""
import argparse
import binascii
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_reader import Ace, PCD_TRANSCEIVE, BitFramingReg, Status2Reg  # noqa: E402

TxModeReg, RxModeReg = 0x12, 0x13
PAGE_FIRST, PAGE_LAST = 4, 39


def prepare(a):
    """Power the reader, SELECT the tag, and turn CRC on in both directions.

    Frames are then sent WITHOUT trailing CRC bytes - the reader appends and verifies them.
    Sending a manual CRC while CRCEn is set produces a malformed frame and total silence, which
    reads exactly like "no tag present" and is very easy to misdiagnose.
    """
    a.wake()
    sel = a.batch([(6, 0)])
    a.batch([(1, BitFramingReg, 0x00)])
    tx = a.batch([(0, TxModeReg)])
    rx = a.batch([(0, RxModeReg)])
    tx0 = tx[0] if tx else 0
    rx0 = rx[0] if rx else 0
    a.batch([(1, TxModeReg, (tx0 | 0x80) & 0xFF)])
    a.batch([(1, RxModeReg, (rx0 | 0x80) & 0xFF)])
    return sel[0] if sel else None


def read_pages(a, first=PAGE_FIRST, last=PAGE_LAST):
    """One NTAG READ returns 16 bytes (four pages), so step by four."""
    out = {}
    page = first
    while page <= last:
        # RE-SELECT BEFORE EVERY READ. Without it the tag stops answering after the first
        # transceive and op 4 hands back the PREVIOUS reply still sitting in the RX region -
        # which looks like a successful read of the wrong page. The giveaway is the same 16
        # bytes repeating every four pages, with the page address apparently never advancing.
        # The same re-SELECT rule is documented for the write-then-verify path.
        a.batch([(6, 0)])
        status, bits, rx = a.frame([0x30, page], cmd=PCD_TRANSCEIVE, rx=16)
        # THE DATA DECIDES, NOT THE STATUS. op 3's return is not a 0-means-success code - a
        # healthy page-4 READ comes back with status 238 and a full RX buffer. Gating on
        # "status == 0" threw away every good read and reported a live tag as unreadable.
        if not any(rx):
            out[page] = None
            page += 4
            continue
        for i in range(4):
            if page + i <= last:
                out[page + i] = bytes(rx[i * 4:i * 4 + 4])
        page += 4
    return out


def sniff(pages):
    """Name the layout from its own bytes, without trusting the firmware's verdict."""
    p4 = pages.get(4) or b""
    if p4[:4] == b"\x7b\x00\x65\x00":
        return "anycubic", "page 4 magic 123 / version 101"
    blob = b"".join(v for v in (pages.get(p) for p in sorted(pages)) if v)
    # NDEF on an NTAG starts with a TLV: 0x03 <len>, then the record header.
    if p4[:1] == b"\x03":
        text = blob.decode("latin-1", "replace")
        if "openspool" in text.lower():
            return "openspool", "NDEF TLV + openspool protocol marker"
        if "application/json" in text:
            return "ndef-json", "NDEF TLV + application/json MIME record"
        return "ndef", "NDEF TLV, record type not recognised"
    if not any(pages.values()):
        return "unreadable", "no page returned data"
    return "unknown", "no known signature at page 4"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, required=True, help="bay 0-3")
    ap.add_argument("--json", help="write the dump here")
    ap.add_argument("--probe", action="store_true",
                    help="only report whether a tag is in the field; exit 0 if it is. "
                         "Cheap enough to call between rotation steps while hunting the tag.")
    args = ap.parse_args()

    # The identify path drops the low index bit, so bays pair onto one reader/antenna.
    reader = args.slot // 2
    a = Ace(reader=reader, slot=args.slot)
    print("slot %d -> reader %d (shares its antenna with slot %d)"
          % (args.slot, reader, args.slot ^ 1))

    sel = prepare(a)
    st2 = a.batch([(0, Status2Reg)])
    print("SELECT status=%s  Status2=0x%02X" % (sel, st2[0] if st2 else 0))
    if sel == 4:
        print("\ncode 4 = ANTICOLLISION: more than one tag is in this coil's field.")
        print("Nothing read. Rotate the other bay's spool until its tag leaves the field,")
        print("or cover it, then retry - a read taken now could silently be the WRONG tag.")
        return 2

    if args.probe:
        # A tag in the field answers a page-4 READ. SELECT status alone is not enough: a stale
        # cached record can make SELECT look plausible when nothing is actually in range.
        status, bits, rx = a.frame([0x30, 0x04], cmd=PCD_TRANSCEIVE, rx=16)
        live = bool(any(rx))
        print("PROBE: %s (select=%s read_status=%s)"
              % ("TAG IN FIELD" if live else "nothing in field", sel, status))
        return 0 if live else 1

    pages = read_pages(a)
    got = sum(1 for v in pages.values() if v)
    fmt, why = sniff(pages)
    print("pages %d-%d: %d/%d readable" % (PAGE_FIRST, PAGE_LAST, got, len(pages)))
    print("format: %s (%s)\n" % (fmt, why))

    for p in sorted(pages):
        v = pages[p]
        if v:
            printable = "".join(chr(c) if 32 <= c < 127 else "." for c in v)
            print("  page %2d  %s  |%s|" % (p, binascii.hexlify(v).decode(), printable))

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"slot": args.slot, "reader": reader, "format": fmt,
                       "captured": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "pages": {str(p): (binascii.hexlify(v).decode() if v else None)
                                 for p, v in pages.items()}}, fh, indent=2)
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
