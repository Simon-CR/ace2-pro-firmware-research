# Proven extraction algorithm for the ACE firmware sm_id stub. See DESIGN.md.
def extract_sm_id(buf):
    KEY = b"sm_id"
    i = buf.find(KEY)
    if i < 0: return None
    j = i + len(KEY)
    while j < len(buf) and not (0x30 <= buf[j] <= 0x39):
        if buf[j] == 0x7D: return None      # '}' before a digit -> malformed
        j += 1
    d = j
    while d < len(buf) and 0x30 <= buf[d] <= 0x39: d += 1
    return buf[j:d].decode() if d > j else None
