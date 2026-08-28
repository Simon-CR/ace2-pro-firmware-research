#!/usr/bin/env python3
"""Build ACE2-Open firmware from a stock Anycubic V1.1.31 image.

No Anycubic firmware is distributed with this repository -- you supply the base image yourself.

The image layout rule that matters (see docs/02-ota-and-iap.md): the IAP task verifies an 8-byte
magic signature in the LAST 8 BYTES of the staged image, so new code must be inserted BEFORE the
trailer and the trailer must remain last:

    patched = stock[:-8] + our_code + magic

Requires arm-none-eabi-as / ld / objcopy. If you have no toolchain, use apply_patch.py with the
pre-assembled firmware/patch.json instead.

Usage:
    python3 build_patch.py --base ACE2_V1.1.31_20260306.bin --out ACE2-Open.bin
"""
import argparse
import hashlib
import os
import struct
import subprocess
import sys

BASE_ADDR = 0x08008000
BASE_MD5 = "79fb22e7914bae1dc75ac91b30739c19"
BASE_SIZE = 71592
MAGIC = bytes([0x61, 0xA5, 0x63, 0x5A, 0x65, 0xA5, 0x32, 0x5A])

HOOK_UID = 0x0800E836        # movs r0,#6 ; b.n    -- the READFAILED exit
HOOK_RC522 = 0x0800E7DA      # movs r0,#1 ; b.n    -- the index >= 4 rejection (dead path)
EPILOGUE = 0x0800E904
SYMS = {
    "epilogue": EPILOGUE,
    "rc522_read_reg": 0x0800F574,
    "rc522_write_reg": 0x0800F5D0,
    "rc522_transceive": 0x0800F32C,
    "rfid_select": 0x0800DEB6,
    "rfid_pageread": 0x0800E18C,
    "delay_ms": 0x08013C70,
}
VERSION_STRING = b"V1.1.3O"   # ACE2-Open; same length as V1.1.31 so the field layout is unchanged

HERE = os.path.dirname(os.path.abspath(__file__))


def thumb_bw(src, dst):
    """Encode a 32-bit Thumb-2 unconditional branch (B.W)."""
    off = dst - (src + 4)
    if not -(1 << 24) <= off < (1 << 24):
        raise ValueError("branch out of range")
    off >>= 1
    s = (off >> 23) & 1
    i1 = (off >> 22) & 1
    i2 = (off >> 21) & 1
    return struct.pack("<HH",
                       0xF000 | (s << 10) | ((off >> 11) & 0x3FF),
                       0x9000 | (((~i1 & 1) ^ s) << 13) | (((~i2 & 1) ^ s) << 11) | (off & 0x7FF))


def assemble(src, addr, tmp):
    lds = "ENTRY(_start)\nSECTIONS {\n  . = 0x%08X;\n  .text : { *(.text) }\n" % addr
    for k, v in SYMS.items():
        lds += "  %s = 0x%08X;\n" % (k, v)
    lds += "}\n"
    open(os.path.join(tmp, "l.ld"), "w").write(lds)
    subprocess.run(["arm-none-eabi-as", "-mthumb", "-mcpu=cortex-m3", src,
                    "-o", os.path.join(tmp, "s.o")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-T", os.path.join(tmp, "l.ld"),
                    os.path.join(tmp, "s.o"), "-o", os.path.join(tmp, "s.elf"),
                    "--defsym", "_start=0"], check=True)
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary",
                    os.path.join(tmp, "s.elf"), os.path.join(tmp, "s.bin")], check=True)
    return open(os.path.join(tmp, "s.bin"), "rb").read()


def crc16_kermit(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="stock ACE2 V1.1.31 .bin (you supply this)")
    ap.add_argument("--out", default="ACE2-Open.bin")
    ap.add_argument("--tmp", default=".build")
    ap.add_argument("--force", action="store_true", help="proceed even if the base image is unrecognised")
    args = ap.parse_args()

    img = bytearray(open(args.base, "rb").read())
    md5 = hashlib.md5(img).hexdigest()
    if md5 != BASE_MD5 or len(img) != BASE_SIZE:
        print("base image: %d bytes md5 %s" % (len(img), md5))
        print("expected  : %d bytes md5 %s" % (BASE_SIZE, BASE_MD5))
        if not args.force:
            sys.exit("Refusing to patch an unrecognised image. All addresses are V1.1.31-specific.\n"
                     "Use --force only if you know exactly what you are doing.")

    if bytes(img[-8:]) != MAGIC:
        sys.exit("base image does not end with the expected IAP magic trailer")

    os.makedirs(args.tmp, exist_ok=True)
    body = img[:-8]

    uid_addr = BASE_ADDR + len(body)
    uid_stub = assemble(os.path.join(HERE, "uid_stub.s"), uid_addr, args.tmp)
    body += uid_stub

    rc_addr = BASE_ADDR + len(body)
    rc_stub = assemble(os.path.join(HERE, "rc522_stub.s"), rc_addr, args.tmp)
    body += rc_stub

    o = HOOK_UID - BASE_ADDR
    if bytes(body[o:o + 4]) != bytes([0x06, 0x20, 0x64, 0xE0]):
        sys.exit("UID hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_UID, uid_addr)

    o = HOOK_RC522 - BASE_ADDR
    if bytes(body[o:o + 2]) != bytes([0x01, 0x20]):
        sys.exit("RC522 hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_RC522, rc_addr)

    i = body.find(b"V1.1.31\x00")
    if i < 0:
        sys.exit("version string not found")
    body[i:i + len(VERSION_STRING)] = VERSION_STRING

    out = bytes(body) + MAGIC
    open(args.out, "wb").write(out)

    print("uid stub    %4d bytes at 0x%08X" % (len(uid_stub), uid_addr))
    print("rc522 stub  %4d bytes at 0x%08X" % (len(rc_stub), rc_addr))
    print("image       %d bytes, crc16/kermit 0x%04X" % (len(out), crc16_kermit(out)))
    print("magic last 8 bytes: %s  %s" % (out[-8:].hex(), "OK" if out[-8:] == MAGIC else "WRONG"))
    print("reports version: %s" % VERSION_STRING.decode())
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()
