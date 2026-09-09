import sys
import time
import binascii

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_direct import AceDirect

def main():
    t0 = time.time()
    print("Testing Reader 1 (Slot 2)...")
    ace = AceDirect(reader=1, slot=2)
    rx0 = ace.prepare()
    print(f"Connected and prepared in {time.time() - t0:.3f}s. RxMode: 0x{rx0:02X}")
    
    t1 = time.time()
    pages = ace.read_pages(0, 15)
    dt = time.time() - t1
    print(f"Read {len(pages)} pages in {dt:.3f}s:")
    for p in sorted(pages.keys()):
        v = pages[p]
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in v)
        print(f"  Page {p:2d}: {binascii.hexlify(v).decode()} |{ascii_str}|")
    ace.close()

if __name__ == "__main__":
    main()
