"""Host driver over the v2 passthrough: staged frames through the firmware's own transceive.

ops: 0 read reg | 1 write reg | 2 store TX byte | 3 transceive | 4 read RX byte | 5 rx bit length
Staging buffer 0x20000704: +0 TX(64) | +64 RX(64) | +128 rx bit length.
"""
import json
import time
import urllib.parse
import urllib.request

B = "http://10.49.9.130:7125"
CommandReg, ComIrqReg, ErrorReg, Status2Reg = 0x01, 0x04, 0x06, 0x08
FIFODataReg, FIFOLevelReg, ControlReg, BitFramingReg = 0x09, 0x0A, 0x0C, 0x0D
TxControlReg, VersionReg = 0x14, 0x37
PCD_TRANSCEIVE, PCD_AUTHENT = 0x0C, 0x0E


def _post(script, timeout=180):
    urllib.request.urlopen(urllib.request.Request(
        B + "/printer/gcode/script?script=" + urllib.parse.quote(script), method="POST"), timeout=timeout).read()


def _get(path, timeout=25):
    return json.load(urllib.request.urlopen(B + path, timeout=timeout))["result"]


class Ace:
    def __init__(self, reader=1, slot=2):
        self.reader, self.slot = reader, slot

    def _idx(self, op, a1, a2=0):
        return 0x80000000 | (self.reader << 24) | (op << 16) | ((a1 & 0x3F) << 8) | (a2 & 0xFF)

    def batch(self, ops, dwell=12):
        script, n = "", 0
        for o in ops:
            script += "ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX=%d\nG4 P%d\n" % (self._idx(*o), dwell)
            n += 1
        t = time.time()
        _post(script)
        time.sleep(0.4 + 0.03 * n)
        out = []
        for g in _get("/server/gcode_store?count=%d" % (n * 3 + 12))["gcode_store"]:
            m = g["message"]
            if g["time"] > t - 0.2 and "FILAMENT_IDENTIFY {'index': " in m and "->" in m and "'code': " in m:
                idx = int(m.split("{'index': ")[1].split("}")[0])
                if idx & 0x80000000:
                    out.append(int(m.split("'code': ")[1].split(",")[0]))
        return out

    def wake(self):
        _post("ACE_RAW_CMD T=%d CMD=FILAMENT_IDENTIFY INDEX=%d" % (self.slot, self.slot))
        time.sleep(1.2)

    def scan_off(self):
        _post("ACE_RAW_CMD T=%d CMD=SET_RFID_ENABLE INDEX=%d ENABLE=0" % (self.slot, self.reader * 2))
        time.sleep(0.6)

    def scan_on(self):
        _post("ACE_RAW_CMD T=%d CMD=SET_RFID_ENABLE INDEX=%d ENABLE=1" % (self.slot, self.reader * 2))
        time.sleep(0.6)

    def frame(self, data, cmd=PCD_TRANSCEIVE, rx=16):
        """Stage data, transceive via the firmware's own helper, read the reply."""
        ops = [(2, i, b) for i, b in enumerate(data)]
        ops.append((3, len(data), cmd))
        res = self.batch(ops)
        status = res[-1] if res else None
        tail = self.batch([(5, 0)] + [(4, i) for i in range(rx)])
        bits = tail[0] if tail else 0
        return status, bits, tail[1:]


def crc_a(data):
    crc = 0x6363
    for b in data:
        b ^= crc & 0xFF
        b = (b ^ (b << 4)) & 0xFF
        crc = ((crc >> 8) ^ (b << 8) ^ (b << 3) ^ (b >> 4)) & 0xFFFF
    return [crc & 0xFF, (crc >> 8) & 0xFF]


if __name__ == "__main__":
    a = Ace(reader=1, slot=2)
    a.wake()
    print("regs:", ["0x%02X" % v for v in a.batch([(0, VersionReg), (0, TxControlReg), (0, Status2Reg)])])
    for label, data in [("WUPA", [0x52]), ("REQA", [0x26]),
                        ("READ p4", [0x30, 0x04] + crc_a([0x30, 0x04]))]:
        st, bits, rx = a.frame(data, rx=18)
        nz = [x for x in rx if x]
        print("%-8s status=%s bits=%s rx=%s" % (label, st, bits, bytes(rx).hex() if nz else "(empty)"))
