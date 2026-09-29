#!/usr/bin/env python3
"""Build clean baseline ACE2-Open V1.1.61O firmware from stock Anycubic V1.1.31 image.

This script applies the full V1.1.61O feature set (Native Tag Decoder, UID stub, RC522 stub,
Rawtag stub, Cache stub, Extract stub, Pageread gate hook, Dryroll gate hook, dual-grab unlock,
and version reporting V1.1.61O) with the telemetry status hook at address 0x0800B868 stripped
out / disabled.

Disabling the HOOK_STATUS (0x0800B868) leaves stock Anycubic instructions (05 28 90 60:
cmp r0, #5; str r0, [r2, #8]) in place, preventing device status byte mutation (bits 0x40/0x80)
and maintaining clean compatibility with standard host parsers.

Usage:
    python3 build_61o_baseline.py [--base ACE2_V1.1.31_20260306.bin] [--out ACE2-Open-V1.1.61O-baseline.bin]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

BASE_ADDR = 0x08008000
BASE_MD5 = "79fb22e7914bae1dc75ac91b30739c19"
BASE_SIZE = 71592
MAGIC = bytes([0x61, 0xA5, 0x63, 0x5A, 0x65, 0xA5, 0x32, 0x5A])

HERE = os.path.dirname(os.path.abspath(__file__))


def crc16_kermit(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def main():
    parser = argparse.ArgumentParser(description="Build clean baseline ACE2-Open V1.1.61O firmware.")
    parser.add_argument("--base", default=os.path.join(HERE, "ACE2_V1.1.31_20260306.bin"),
                        help="Path to stock base V1.1.31 firmware image")
    parser.add_argument("--out", default=os.path.join(HERE, "ACE2-Open-V1.1.61O-baseline.bin"),
                        help="Output path for baseline 61O binary")
    parser.add_argument("--patch-out", default=os.path.join(HERE, "patch_61o_baseline.json"),
                        help="Output path for baseline 61O patch spec JSON")
    args = parser.parse_args()

    if not os.path.exists(args.base):
        sys.exit(f"Base firmware not found: {args.base}")

    base = open(args.base, "rb").read()
    if len(base) != BASE_SIZE or hashlib.md5(base).hexdigest() != BASE_MD5:
        sys.exit(f"Base image mismatch. Expected {BASE_SIZE} bytes and MD5 {BASE_MD5}.")
    if base[-8:] != MAGIC:
        sys.exit("Base image does not end with IAP magic trailer.")

    # Retrieve V1.1.61O patch specification from git commit 65f2c160fcceef4ff7d5c911ac2600f9b4453bb0
    raw_spec = subprocess.check_output(
        ["git", "show", "65f2c160fcceef4ff7d5c911ac2600f9b4453bb0:firmware/patch.json"],
        cwd=HERE
    )
    spec_61o = json.loads(raw_spec)

    body = bytearray(base[:-8])
    body += bytes.fromhex(spec_61o["appended_hex"])

    baseline_hooks = []
    omitted_hooks = []

    for h in spec_61o["hooks"]:
        addr = h["addr"]
        # Strip out / disable HOOK_STATUS at 0x0800B868
        if addr == "0x0800B868":
            omitted_hooks.append(h)
            continue
        off = h["file_offset"]
        expect = bytes.fromhex(h["expect_hex"])
        if body[off:off + len(expect)] != expect:
            sys.exit(f"Hook site {addr} (offset {off}) did not match expected instructions {h['expect_hex']}")
        body[off:off + len(expect)] = bytes.fromhex(h["replace_hex"])
        baseline_hooks.append(h)

    for p in spec_61o["pokes"]:
        off = p["file_offset"]
        body[off:off + len(bytes.fromhex(p["bytes_hex"]))] = bytes.fromhex(p["bytes_hex"])

    out = bytes(body) + MAGIC
    with open(args.out, "wb") as f:
        f.write(out)

    baseline_spec = {
        "name": "ACE2-Open-Baseline",
        "base_of": "Anycubic ACE 2 Pro V1.1.31",
        "base_md5": BASE_MD5,
        "base_size": BASE_SIZE,
        "version_string": spec_61o["version_string"],
        "appended_hex": spec_61o["appended_hex"],
        "hooks": baseline_hooks,
        "pokes": spec_61o["pokes"],
        "omitted_hooks": omitted_hooks,
        "result_size": len(out),
        "result_crc16": crc16_kermit(out),
        "result_md5": hashlib.md5(out).hexdigest(),
        "result_sha256": hashlib.sha256(out).hexdigest(),
        "note": "Clean baseline V1.1.61O build with telemetry status hook 0x0800B868 disabled/stripped out."
    }

    with open(args.patch_out, "w", encoding="utf-8") as f:
        json.dump(baseline_spec, f, indent=1)

    print(f"Generated baseline 61O firmware: {args.out}")
    print(f"Size: {len(out)} bytes")
    print(f"CRC16/Kermit: 0x{crc16_kermit(out):04X}")
    print(f"MD5: {hashlib.md5(out).hexdigest()}")
    print(f"SHA256: {hashlib.sha256(out).hexdigest()}")
    print(f"Omitted telemetry status hook at: {[h['addr'] for h in omitted_hooks]}")
    print(f"Wrote baseline patch spec: {args.patch_out}")


if __name__ == "__main__":
    main()
