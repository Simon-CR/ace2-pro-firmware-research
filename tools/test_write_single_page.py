import sys
import time
import binascii

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_reader import Ace, PCD_TRANSCEIVE, BitFramingReg  # noqa: E402

TxModeReg, RxModeReg = 0x12, 0x13

def prepare(a):
    a.wake()
    sel = a.batch([(6, 0)])
    a.batch([(1, BitFramingReg, 0x00)])
    tx = a.batch([(0, TxModeReg)])
    rx = a.batch([(0, RxModeReg)])
    tx0 = tx[0] if tx else 0
    rx0 = rx[0] if rx else 0
    a.batch([(1, TxModeReg, (tx0 | 0x80) & 0xFF)])
    a.batch([(1, RxModeReg, (rx0 | 0x80) & 0xFF)])
    return rx0

def read_page(a, page):
    base_page = (page // 4) * 4
    offset = (page % 4) * 4
    a.batch([(6, 0)])
    status, bits, rx = a.frame([0x30, base_page], cmd=PCD_TRANSCEIVE, rx=16)
    if len(rx) < offset + 4:
        return None
    return bytes(rx[offset : offset + 4])

def write_page(a, page, data4, rx0):
    assert len(data4) == 4
    # Rule 1: Clear RxCRCEn, keep TxCRCEn
    a.batch([(1, RxModeReg, (rx0 & ~0x80) & 0xFF)])
    
    # Rule 2: Stage [0xA2, page, b0, b1, b2, b3] and transceive
    ops = [
        (2, 0, 0xA2),
        (2, 1, page),
        (2, 2, data4[0]),
        (2, 3, data4[1]),
        (2, 4, data4[2]),
        (2, 5, data4[3]),
        (3, 6, PCD_TRANSCEIVE)
    ]
    a.batch(ops)
    time.sleep(0.01) # Wait for EEPROM write (~4ms)
    
    # Restore RxCRCEn
    a.batch([(1, RxModeReg, (rx0 | 0x80) & 0xFF)])
    
    # Rule 3: Re-SELECT before verify
    a.batch([(6, 0)])

def main():
    slot = 1
    reader = slot // 2
    a = Ace(reader=reader, slot=slot)
    print(f"Connecting to Slot {slot} (Reader {reader})...")
    rx0 = prepare(a)
    print(f"Reader initialized. RxMode base: 0x{rx0:02X}")
    
    # 1. Read original page 39
    p39_orig = read_page(a, 39)
    print(f"Original page 39: {binascii.hexlify(p39_orig).decode() if p39_orig else 'None'}")
    if not p39_orig:
        print("ERROR: Could not read page 39")
        return 1
        
    # 2. Write test value 'DEAD'
    test_val = b"\x44\x45\x41\x44"
    print(f"Writing test value {binascii.hexlify(test_val).decode()} to page 39...")
    write_page(a, 39, test_val, rx0)
    
    # 3. Read back and verify
    p39_test = read_page(a, 39)
    print(f"Readback page 39: {binascii.hexlify(p39_test).decode() if p39_test else 'None'}")
    assert p39_test == test_val, f"Mismatch: expected {test_val.hex()}, got {p39_test.hex() if p39_test else None}"
    print("SUCCESS: Test value verified!")
    
    # 4. Restore original value
    print(f"Restoring original value {binascii.hexlify(p39_orig).decode()} to page 39...")
    write_page(a, 39, p39_orig, rx0)
    
    # 5. Read back and verify restoration
    p39_restored = read_page(a, 39)
    print(f"Restored page 39: {binascii.hexlify(p39_restored).decode() if p39_restored else 'None'}")
    assert p39_restored == p39_orig, f"Mismatch on restore: expected {p39_orig.hex()}, got {p39_restored.hex() if p39_restored else None}"
    print("SUCCESS: Original value restored and verified!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
