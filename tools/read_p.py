import sys, binascii
sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from test_multiformat_flash import Ace, read_page, prepare
a = Ace(reader=0, slot=1)
prepare(a)
for p in range(4, 16):
    v = read_page(a, p)
    print(f"Page {p:2d}: {binascii.hexlify(v).decode() if v else None}")
