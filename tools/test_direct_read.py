import sys
import time
import binascii

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_direct import AceDirect

def main():
    t0 = time.time()
    ace = AceDirect(reader=0, slot=1)
    rx0 = ace.prepare()
    print(f"Connected and prepared in {time.time() - t0:.3f}s. RxMode: 0x{rx0:02X}")
    
    t1 = time.time()
    pages = ace.read_pages(4, 39)
    dt = time.time() - t1
    print(f"Read {len(pages)} pages in {dt:.3f}s ({dt/len(pages)*1000:.1f}ms/page):")
    for p in sorted(pages.keys())[:8]:
        v = pages[p]
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in v)
        print(f"  Page {p:2d}: {binascii.hexlify(v).decode()} |{ascii_str}|")
    ace.close()

if __name__ == "__main__":
    main()
