import sys
import binascii

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_direct import AceDirect

def main():
    ace = AceDirect(reader=1, slot=2)
    rx0 = ace.prepare()
    print(f"Prepared Reader 1, RxMode=0x{rx0:02X}")
    
    pages = ace.read_pages(0, 15)
    print("Slot 2 Tag First 16 Pages:")
    for p in range(16):
        v = pages.get(p)
        h = binascii.hexlify(v).decode() if v else "None"
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in v) if v else ""
        print(f"  Page {p:2d}: {h} |{asc}|")
    
    # Check lock bytes in page 2
    p2 = pages.get(2)
    if p2:
        lock0 = p2[2]
        lock1 = p2[3]
        print(f"\nPage 2 Static Lock Bytes: lock0=0x{lock0:02X}, lock1=0x{lock1:02X}")
        if lock0 == 0 and lock1 == 0:
            print(">>> VERDICT: TAG IS 100% UNLOCKED (WRITABLE)! <<<")
        else:
            print(f">>> WARNING: Static lock bits set! lock0={bin(lock0)}, lock1={bin(lock1)} <<<")

    ace.close()

if __name__ == "__main__":
    main()
