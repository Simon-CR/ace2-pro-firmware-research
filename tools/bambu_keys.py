"""Derive the MIFARE Classic sector keys for a Bambu spool tag from its UID.

Scheme is public (Bambu-Research-Group/RFID-Tag-Guide): HKDF-SHA256 with the tag UID as salt
and a published master key as input keying material; the first 96 bytes of output are the 16
KeyA values (6 bytes per sector). Nothing here talks to hardware -- it is pure derivation, so
it can be prepared before the firmware passthrough exists.

Usage: bambu_keys.py [uid_hex]     (defaults to the UID our patched ACE read from lane 3)
"""
import hashlib
import hmac
import sys

MASTER = bytes.fromhex("9a759cf2c4f7caff222cb9769b41bc96")
INFO_A = b"RFID-A\0"


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()          # extract
    out, t, counter = b"", b"", 1                                # expand
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]


def keys_for_uid(uid: bytes):
    # HKDF(ikm=uid, salt=MASTER, info="RFID-A") -- per Bambu-Research-Group/RFID-Tag-Guide
    # deriveKeys.py: HKDF(uid, 6, master, SHA256, 16, context=b"RFID-A").
    # I originally had ikm/salt swapped, which yields entirely different (wrong) keys.
    material = hkdf_sha256(uid, MASTER, INFO_A, 6 * 16)
    return [material[i * 6:(i + 1) * 6] for i in range(16)]


if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 else "899334FC"
    uid = bytes.fromhex(raw)
    print("UID: %s (%d bytes)" % (uid.hex().upper(), len(uid)))
    print("master key (public):", MASTER.hex())
    print()
    for i, k in enumerate(keys_for_uid(uid)):
        print("  sector %2d  KeyA = %s" % (i, k.hex().upper()))
    print()
    print("Bambu tag layout (from the public tag guide) once authenticated:")
    print("  block 1: material variant id, filament type")
    print("  block 2: filament type detail")
    print("  block 4: colour RGBA, spool weight, filament diameter")
    print("  block 5: nozzle temp min/max, bed temp, drying params")
    print("  block 6/8/9/10: production info, extra colours, length")
