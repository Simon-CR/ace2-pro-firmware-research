#!/usr/bin/env python3
"""Apply ACE2-Open to a stock V1.1.31 image without needing an ARM toolchain.

patch.json holds only OUR assembled bytes and their offsets -- no Anycubic firmware is
distributed with this repository. You supply the base image.

Usage:
    python3 apply_patch.py --base ACE2_V1.1.31_20260306.bin --out ACE2-Open.bin
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAGIC = bytes([0x61, 0xA5, 0x63, 0x5A, 0x65, 0xA5, 0x32, 0x5A])


def crc16_kermit(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", default="ACE2-Open.bin")
    ap.add_argument("--spec", default=os.path.join(HERE, "patch.json"))
    args = ap.parse_args()

    spec = json.load(open(args.spec))
    img = bytearray(open(args.base, "rb").read())

    md5 = hashlib.md5(img).hexdigest()
    if md5 != spec["base_md5"] or len(img) != spec["base_size"]:
        sys.exit("base image mismatch\n  got      %d bytes md5 %s\n  expected %d bytes md5 %s\n"
                 "All offsets are specific to that exact image; refusing to continue."
                 % (len(img), md5, spec["base_size"], spec["base_md5"]))
    if bytes(img[-8:]) != MAGIC:
        sys.exit("base image does not end with the IAP magic trailer")

    # append our code before the trailer, then apply the in-place hooks
    body = img[:-8]
    body += bytes.fromhex(spec["appended_hex"])
    for h in spec["hooks"]:
        off = h["file_offset"]
        expect = bytes.fromhex(h["expect_hex"])
        if bytes(body[off:off + len(expect)]) != expect:
            sys.exit("hook site 0x%X does not contain the expected instructions" % off)
        body[off:off + len(expect)] = bytes.fromhex(h["replace_hex"])
    for p in spec["pokes"]:
        off = p["file_offset"]
        body[off:off + len(bytes.fromhex(p["bytes_hex"]))] = bytes.fromhex(p["bytes_hex"])

    out = bytes(body) + MAGIC
    open(args.out, "wb").write(out)
    print("wrote %s: %d bytes, crc16/kermit 0x%04X" % (args.out, len(out), crc16_kermit(out)))
    print("magic last 8 bytes: %s" % out[-8:].hex())
    print("expected crc from spec: 0x%04X %s" % (spec["result_crc16"],
          "MATCH" if crc16_kermit(out) == spec["result_crc16"] else "*** MISMATCH ***"))
    print("reports version: %s" % spec["version_string"])


if __name__ == "__main__":
    main()
