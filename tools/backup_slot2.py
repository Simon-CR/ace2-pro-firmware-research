import sys
import json
import binascii
import os

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_direct import AceDirect

def main():
    ace = AceDirect(reader=1, slot=2)
    rx0 = ace.prepare()
    print("Reading Slot 2 original tag pages 4..39...")
    pages = ace.read_pages(4, 39)
    
    dump = {
        "slot": 2,
        "reader": 1,
        "rx0": rx0,
        "pages": {str(p): binascii.hexlify(pages[p]).decode() if pages.get(p) else None for p in sorted(pages.keys())}
    }
    
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slot2_original_backup.json")
    with open(out_path, "w") as f:
        json.dump(dump, f, indent=2)
    print(f"Backed up {len(pages)} pages to {out_path}:")
    for p in sorted(pages.keys())[:10]:
        print(f"  p{p:02d}: {dump['pages'][str(p)]}")
    ace.close()

if __name__ == "__main__":
    main()
