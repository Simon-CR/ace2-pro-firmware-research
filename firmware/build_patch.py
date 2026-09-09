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
    python3 build_patch.py --base ACE2_V1.1.31_20260306.bin --out ACE2-Open-V1.1.46.bin
"""
import argparse
import hashlib
import json
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
HOOK_RAWTAG = 0x0800E842     # add.w lr,sp,#140    -- cmd 68's live identification hook
HOOK_RAWCACHE = 0x0800FE3C   # add.w lr,r5,#136    -- the BACKGROUND reader's positional parse.
HOOK_PAGEREAD = 0x0800E228   # movw r1,#0x704 -- the page-read copy/return epilogue.
HOOK_EXTRACT = 0x0800FE36    # mov r6,r0 ; cmp r0,#140  -- background worker decode hook.

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
    "memcpy": 0x08008AA8,
    "resume": 0x0800E846,         # stock cmd 68 positional parse resume
    "cache_resume": 0x0800FE40,   # rawtag_cache_stub resume
    "pageread_resume": 0x0800E22C,# mid-epilogue, after the displaced movw
    "pageread_fail": 0x0800E23C,  # "mov r0, sl" -- stock failed-read return
    "extract_resume": 0x0800FE3A, # rawtag_extract_stub Anycubic/failure resume
    "scan_exit": 0x0800FE98,      # background scan success exit (bypasses Anycubic parse)
}
VERSION_STRING = b"V1.1.60O\x00"  # Production Multi-Format + Bambu RFID
                               # Trailing 'O' ensures multiACE auto-detects open firmware build.

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


def assemble(src, addr, tmp, extra_objs=None):
    lds = "ENTRY(_start)\nSECTIONS {\n  . = 0x%08X;\n  .text : { *(.text*) *(.rodata*) }\n" % addr
    for k, v in SYMS.items():
        lds += "  %s = 0x%08X;\n" % (k, v)
    lds += "}\n"
    open(os.path.join(tmp, "l.ld"), "w").write(lds)
    subprocess.run(["arm-none-eabi-as", "-mthumb", "-mcpu=cortex-m3", src,
                    "-o", os.path.join(tmp, "s.o")], check=True)
    objs = [os.path.join(tmp, "s.o")]
    if extra_objs:
        objs.extend(extra_objs)
    subprocess.run(["arm-none-eabi-ld", "-T", os.path.join(tmp, "l.ld")] +
                    objs + ["-o", os.path.join(tmp, "s.elf"),
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
    ap.add_argument("--out", default="ACE2-Open-V1.1.52O.bin")
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

    # 1. Compile native_tag_decoder.c standalone
    decoder_c = os.path.join(HERE, "native_tag_decoder.c")
    decoder_obj = os.path.join(args.tmp, "decoder.o")
    subprocess.run(["arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3", "-Os",
                    "-ffreestanding", "-fno-builtin", "-fno-tree-loop-distribute-patterns", "-nostdlib",
                    "-c", decoder_c, "-o", decoder_obj], check=True)

    # 2. Link decoder binary as standalone text section at decoder_addr
    decoder_addr = BASE_ADDR + len(body)
    dec_ld = "ENTRY(decode_native_tag)\nSECTIONS {\n  . = 0x%08X;\n  .text : { *(.text*) *(.rodata*) }\n" % decoder_addr
    for k, v in SYMS.items():
        dec_ld += "  %s = 0x%08X;\n" % (k, v)
    dec_ld += "}\n"
    open(os.path.join(args.tmp, "dec.ld"), "w").write(dec_ld)
    subprocess.run(["arm-none-eabi-ld", "-T", os.path.join(args.tmp, "dec.ld"),
                    decoder_obj, "-o", os.path.join(args.tmp, "dec.elf"),
                    "--defsym", "_start=0"], check=True)
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary",
                    os.path.join(args.tmp, "dec.elf"), os.path.join(args.tmp, "dec.bin")], check=True)
    decoder_bin = open(os.path.join(args.tmp, "dec.bin"), "rb").read()
    body += decoder_bin

    # 3. Read symbols from dec.elf and register into SYMS
    nm_out = subprocess.check_output(["arm-none-eabi-nm", os.path.join(args.tmp, "dec.elf")]).decode()
    for line in nm_out.strip().split("\n"):
        parts = line.split()
        if len(parts) == 3:
            addr_str, typ, sym = parts
            if sym in ("decode_native_tag", "decode_cmd68_tag", "decode_cmd68_uid_tag", "delay_ms"):
                SYMS[sym] = int(addr_str, 16)

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

    pageread_addr = BASE_ADDR + len(body)
    pageread_stub = assemble(os.path.join(HERE, "pageread_gate_stub.s"), pageread_addr, args.tmp)
    body += pageread_stub

    # Hook installations:
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

    # 3-byte extend read pokes:
    for addr, want, new in ((0x0800E220, 0x7C, 0xAC),   # cmp r7,#124 -> #172 : 12 iterations
                            (0x0800E216, 0x88, 0x38),   # cbnz r0 fail-target -> 0x800e228
                            (0x0800E21C, 0x0E, 0x04)):   # bne  fail-target -> 0x800e228
        o = addr - BASE_ADDR
        if body[o] != want:
            sys.exit("extend-read poke 0x%08X: expected 0x%02X found 0x%02X" % (addr, want, body[o]))
        body[o] = new

    # Dual-grab unlock in sub_800F698 (NOP sibling busy check and RFID busy check):
    # 0x0800F6F8: bpl.n 0x800f6c4 (e4 d5) -> nop (00 bf)
    # 0x0800F724: beq.n 0x800f6c4 (ce d0) -> nop (00 bf)
    for addr, want, new in ((0x0800F6F8, bytes([0xE4, 0xD5]), bytes([0x00, 0xBF])),
                            (0x0800F724, bytes([0xCE, 0xD0]), bytes([0x00, 0xBF]))):
        o = addr - BASE_ADDR
        if bytes(body[o:o + len(want)]) != want:
            sys.exit("dual-grab unlock poke 0x%08X: expected %s found %s"
                     % (addr, want.hex(), bytes(body[o:o + len(want)]).hex()))
        body[o:o + len(new)] = new

    o = HOOK_PAGEREAD - BASE_ADDR     # movw r1,#0x704 (40 f2 04 71)
    if bytes(body[o:o + 4]) != bytes([0x40, 0xF2, 0x04, 0x71]):
        sys.exit("page-read gate hook site does not match the expected instructions")
    body[o:o + 4] = thumb_bw(HOOK_PAGEREAD, pageread_addr)

    # Note: HOOK_CMD68 (0x0800E8A2) is intentionally left stock (add.w r0, r8, #88).
    # Open-format tags branch directly to epilogue (0x0800E904) from HOOK_RAWTAG.
    # Native Anycubic tags resume stock parse and execute 0x0800E8A2 normally.

    i = body.find(b"V1.1.31\x00")
    if i < 0:
        sys.exit("version string not found")
    body[i:i + len(VERSION_STRING)] = VERSION_STRING

    out = bytes(body) + MAGIC
    open(args.out, "wb").write(out)

    # Automatically generate/update firmware/patch.json for browser-based toolchain-free flashing
    patch_path = os.path.join(HERE, "patch.json")
    hooks_spec = [
        {
            "file_offset": HOOK_RC522 - BASE_ADDR,
            "addr": "0x%08X" % HOOK_RC522,
            "expect_hex": "012092e0",
            "replace_hex": bytes(body[HOOK_RC522 - BASE_ADDR:HOOK_RC522 - BASE_ADDR + 4]).hex(),
        },
        {
            "file_offset": HOOK_UID - BASE_ADDR,
            "addr": "0x%08X" % HOOK_UID,
            "expect_hex": "062064e0",
            "replace_hex": bytes(body[HOOK_UID - BASE_ADDR:HOOK_UID - BASE_ADDR + 4]).hex(),
        },
        {
            "file_offset": HOOK_RAWTAG - BASE_ADDR,
            "addr": "0x%08X" % HOOK_RAWTAG,
            "expect_hex": "0df18c0e",
            "replace_hex": bytes(body[HOOK_RAWTAG - BASE_ADDR:HOOK_RAWTAG - BASE_ADDR + 4]).hex(),
        },
        {
            "file_offset": HOOK_RAWCACHE - BASE_ADDR,
            "addr": "0x%08X" % HOOK_RAWCACHE,
            "expect_hex": "05f1880e",
            "replace_hex": bytes(body[HOOK_RAWCACHE - BASE_ADDR:HOOK_RAWCACHE - BASE_ADDR + 4]).hex(),
        },
        {
            "file_offset": HOOK_EXTRACT - BASE_ADDR,
            "addr": "0x%08X" % HOOK_EXTRACT,
            "expect_hex": "06468c28",
            "replace_hex": bytes(body[HOOK_EXTRACT - BASE_ADDR:HOOK_EXTRACT - BASE_ADDR + 4]).hex(),
        },
        {
            "file_offset": HOOK_PAGEREAD - BASE_ADDR,
            "addr": "0x%08X" % HOOK_PAGEREAD,
            "expect_hex": "40f20471",
            "replace_hex": bytes(body[HOOK_PAGEREAD - BASE_ADDR:HOOK_PAGEREAD - BASE_ADDR + 4]).hex(),
        },
    ]

    pokes_spec = [
        {"file_offset": 0x0800E216 - BASE_ADDR, "addr": "0x0800E216", "bytes_hex": "38"},
        {"file_offset": 0x0800E21C - BASE_ADDR, "addr": "0x0800E21C", "bytes_hex": "04"},
        {"file_offset": 0x0800E220 - BASE_ADDR, "addr": "0x0800E220", "bytes_hex": "ac"},
        {"file_offset": 0x0800F6F8 - BASE_ADDR, "addr": "0x0800F6F8", "bytes_hex": "00bf"},
        {"file_offset": 0x0800F724 - BASE_ADDR, "addr": "0x0800F724", "bytes_hex": "00bf"},
        {"file_offset": i + 5, "addr": "0x%08X" % (BASE_ADDR + i + 5), "bytes_hex": VERSION_STRING[5:].hex()},
    ]

    patch_spec = {
        "name": "ACE2-Open",
        "base_of": "Anycubic ACE 2 Pro V1.1.31",
        "base_md5": BASE_MD5,
        "base_size": BASE_SIZE,
        "version_string": VERSION_STRING.rstrip(b"\x00").decode("ascii"),
        "appended_hex": bytes(body[BASE_SIZE - 8:]).hex(),
        "hooks": hooks_spec,
        "pokes": pokes_spec,
        "result_size": len(out),
        "result_crc16": crc16_kermit(out),
        "note": "Contains only code written for this project plus the offsets it is applied at. No Anycubic firmware is included."
    }
    with open(patch_path, "w", encoding="utf-8") as f:
        json.dump(patch_spec, f, indent=1)

    print("decoder     %4d bytes at 0x%08X" % (len(decoder_bin), decoder_addr))
    print("uid stub    %4d bytes at 0x%08X" % (len(uid_stub), uid_addr))
    print("rc522 stub  %4d bytes at 0x%08X" % (len(rc_stub), rc_addr))
    print("rawtag stub %4d bytes at 0x%08X" % (len(raw_stub), raw_addr))
    print("cache stub  %4d bytes at 0x%08X" % (len(cache_stub), cache_addr))
    print("extract stub%4d bytes at 0x%08X" % (len(extract_stub), extract_addr))
    print("pageread    %4d bytes at 0x%08X" % (len(pageread_stub), pageread_addr))
    print("image       %d bytes, crc16/kermit 0x%04X" % (len(out), crc16_kermit(out)))
    print("magic last 8 bytes: %s  %s" % (out[-8:].hex(), "OK" if out[-8:] == MAGIC else "WRONG"))
    print("reports version: %s" % VERSION_STRING.rstrip(b"\x00").decode("ascii"))
    print("wrote %s" % patch_path)
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()
