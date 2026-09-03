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
HOOK_RAWTAG = 0x0800E842     # add.w lr,sp,#140    -- cmd 68's positional parse (host-initiated)
HOOK_RAWCACHE = 0x0800FE3C   # add.w lr,r5,#136    -- the BACKGROUND reader's positional parse.
                             # The only path that ever reads a tag on this machine: cmd 68
                             # answers code 3 even on lanes the ACE decodes fine by itself.
HOOK_EXTRACT = 0x0800FE36    # mov r6,r0 ; cmp r0,#140  -- just after the background page read.
                             # r5 == commit's memcpy source, so writing "SM<n>" to r5+28 rides
                             # the native pipeline to cmd-13's SKU. Reads the tag tail (past the
                             # 144-byte window) for the OpenSpool sm_id. Coexists with RAWCACHE
                             # (FE3C): non-overlapping 4-byte sites, FE3A left intact between them.
HOOK_CMD68 = 0x0800E8A2      # add.w r0,r8,#88 -- cmd-68 response, AFTER the native sku/version
                             # writes (E842 gets clobbered); sm_id inject on the live-read path.
EPILOGUE = 0x0800E904
SYMS = {
    "epilogue": EPILOGUE,
    "rc522_read_reg": 0x0800F574,
    "rc522_write_reg": 0x0800F5D0,
    "rc522_transceive": 0x0800F32C,
    "rc522_timer": 0x0800F50C,
    "rc522_flush": 0x0800F30A,
    "rfid_select": 0x0800DEB6,
    "rfid_pageread": 0x0800E18C,
    "delay_ms": 0x08013C70,
    "memcpy": 0x08008AA8,
    "resume": 0x0800E846,     # the instruction after the one rawtag_stub displaces
    "cache_resume": 0x0800FE40,  # likewise for rawtag_cache_stub
    "extract_resume": 0x0800FE3A,  # rawtag_extract_stub rejoins the original bcc.n here
    "cmd68_resume": 0x0800E8A6,   # rawtag_cmd68_stub resumes here
}
VERSION_STRING = b"V1.1.42"   # UID stub + RC522 passthrough (op 9) + raw-tag hooks + sm_id inject.
                              # Same length as V1.1.31 so the field layout is unchanged.
                              # O = two-hook; W = shipped 2026-08-28; X adds rawtag; Y = cache;
                              # Z adds the sm_id extraction+injection at HOOK_EXTRACT.

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

    raw_addr = BASE_ADDR + len(body)
    raw_stub = assemble(os.path.join(HERE, "rawtag_stub.s"), raw_addr, args.tmp)
    body += raw_stub

    cache_addr = BASE_ADDR + len(body)
    cache_stub = assemble(os.path.join(HERE, "rawtag_cache_stub.s"), cache_addr, args.tmp)
    body += cache_stub

    extract_addr = BASE_ADDR + len(body)
    extract_stub = assemble(os.path.join(HERE, "rawtag_extract_stub.s"), extract_addr, args.tmp)
    body += extract_stub

    cmd68_addr = BASE_ADDR + len(body)
    cmd68_stub = assemble(os.path.join(HERE, "rawtag_cmd68_stub.s"), cmd68_addr, args.tmp)
    body += cmd68_stub

    o = HOOK_UID - BASE_ADDR
    if bytes(body[o:o + 4]) != bytes([0x06, 0x20, 0x64, 0xE0]):
        sys.exit("UID hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_UID, uid_addr)

    o = HOOK_RC522 - BASE_ADDR
    if bytes(body[o:o + 2]) != bytes([0x01, 0x20]):
        sys.exit("RC522 hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_RC522, rc_addr)

    o = HOOK_RAWTAG - BASE_ADDR
    if bytes(body[o:o + 4]) != bytes([0x0D, 0xF1, 0x8C, 0x0E]):
        sys.exit("raw-tag hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_RAWTAG, raw_addr)

    o = HOOK_RAWCACHE - BASE_ADDR
    if bytes(body[o:o + 4]) != bytes([0x05, 0xF1, 0x88, 0x0E]):
        sys.exit("raw-tag cache hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_RAWCACHE, cache_addr)

    o = HOOK_EXTRACT - BASE_ADDR      # mov r6,r0 (4606) ; cmp r0,#140 (288c)
    if bytes(body[o:o + 4]) != bytes([0x06, 0x46, 0x8c, 0x28]):
        sys.exit("sm_id extract hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_EXTRACT, extract_addr)

    # Extend the stock page read 0x0800E18C to read pages 4-51 (12 blocks) into 0x20000704, so the
    # extract hook can find sm_id in the tail with NO RC522 re-entry. A page-39 NAK (NTAG213) is
    # tolerated by redirecting the two failure branches to the normal copy/return (0x800e228),
    # which returns the hardcoded 144 (>=140, callers still pass). ALL THREE bytes together or
    # none - the loop bound alone would turn every NTAG213 read into a hard failure. Addresses and
    # bytes byte-verified against the stock .bin (argus, 2026-09-02).
    for addr, want, new in ((0x0800E220, 0x7C, 0xAC),   # cmp r7,#124 -> #172 : 12 iterations
                            (0x0800E216, 0x88, 0x38),   # cbnz r0 fail-target -> 0x800e228
                            (0x0800E21C, 0x0E, 0x04)):   # bne  fail-target -> 0x800e228
        o = addr - BASE_ADDR
        if body[o] != want:
            sys.exit("extend-read poke 0x%08X: expected 0x%02X found 0x%02X" % (addr, want, body[o]))
        body[o] = new

    o = HOOK_CMD68 - BASE_ADDR        # add.w r0,r8,#88 (08 f1 58 00)
    if bytes(body[o:o + 4]) != bytes([0x08, 0xF1, 0x58, 0x00]):
        sys.exit("cmd-68 inject hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_CMD68, cmd68_addr)

    i = body.find(b"V1.1.31\x00")
    if i < 0:
        sys.exit("version string not found")
    body[i:i + len(VERSION_STRING)] = VERSION_STRING

    out = bytes(body) + MAGIC
    open(args.out, "wb").write(out)

    print("uid stub    %4d bytes at 0x%08X" % (len(uid_stub), uid_addr))
    print("rc522 stub  %4d bytes at 0x%08X" % (len(rc_stub), rc_addr))
    print("rawtag stub %4d bytes at 0x%08X" % (len(raw_stub), raw_addr))
    print("cache stub  %4d bytes at 0x%08X" % (len(cache_stub), cache_addr))
    print("extract stub%4d bytes at 0x%08X" % (len(extract_stub), extract_addr))
    print("cmd68 stub  %4d bytes at 0x%08X" % (len(cmd68_stub), cmd68_addr))
    print("image       %d bytes, crc16/kermit 0x%04X" % (len(out), crc16_kermit(out)))
    print("magic last 8 bytes: %s  %s" % (out[-8:].hex(), "OK" if out[-8:] == MAGIC else "WRONG"))
    print("reports version: %s" % VERSION_STRING.decode())
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()
