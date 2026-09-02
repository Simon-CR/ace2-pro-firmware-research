"""Rotate a lane's spool until its tag sits in the antenna's field, then leave it there.

A tag rides on the spool and the antenna is fixed in the bay, so a tag is only readable while it
sweeps past the coil. A cold stationary read is nearly useless - a known-good tag was polled 192
times over 45 seconds and failed every time, simply because it was not in front of the coil at
those instants. One revolution of a 1 kg spool is roughly 600 mm of filament, so a full search
has to be prepared to move about that far.

Motion uses ACE_RAW_FEED, the same primitive the dry-roller uses: it is ASYNCHRONOUS, so the
Klipper gcode queue stays free and the probe between steps is not queued behind the move. A
blocking move would serialise the probes and defeat the whole search.

DIRECTION. MODE=0 IS FEED. MODE=1 IS ROLLBACK. Getting this backwards is not a typo, it is the
difference between winding onto the spool and driving 700mm into the shared path, so it is worth
being explicit: ace_raw_feed.py names them ("0 feed, 1 rollback, 2 feed-assist, 3 rollback-assist"),
protocol_ace2.py builds mode 0 for FeedFilament and mode 1 for UnwindFilament, and the retract
paths in instance.py use mode 1. Even = forward, odd = backward.

Every search step is therefore MODE=1, winding filament back onto its own spool and AWAY from the
hub, so an overshoot cannot reach the shared path. The accumulated distance is given back with a
single MODE=0 at the end. The spool turns either way, so searching in the safe direction sweeps
the tag past the antenna exactly as well as feeding would - the direction costs nothing.

STALENESS. The RX region holds whatever the firmware's last scan left in it, so a buffer full of
plausible-looking bytes is NOT evidence a tag answered. Two different pages are read and their
results must DIFFER before a tag is declared found.

ANTICOLLISION. Bays pair onto one reader and one antenna - the identify path drops the low index
bit, (index << 1) & ~2 - so 0+1 share a coil and 2+3 share a coil. If the neighbour bay also
carries a tag, a read here can silently be the WRONG tag. Pass --expect-uid to pin it, or check
the reported UID against what you expect.

    python ace_tag_park.py --slot 1
    python ace_tag_park.py --slot 1 --budget 600 --step 25
"""
import argparse
import binascii
import sys
import time
import urllib.parse
import json
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_reader import Ace, PCD_TRANSCEIVE, BitFramingReg, B  # noqa: E402


def raw_feed(slot, mode, length, speed=15):
    """One asynchronous rotation step through the driver's own raw feed command."""
    script = "ACE_RAW_FEED T=%d MODE=%d LENGTH=%d SPEED=%d" % (slot, mode, length, speed)
    urllib.request.urlopen(urllib.request.Request(
        B + "/printer/gcode/script?script=" + urllib.parse.quote(script),
        method="POST"), timeout=60).read()


def lane_safe(slot):
    """True while the lane still holds its filament. The abort condition, not a formality.

    A rollback search unwinds the lane toward its own gate, and the failure that matters is
    unthreading it - pulling the tail back past the gate, after which nothing can feed it again
    without hands. slot status leaving "ready", or the buffer no longer reporting the lane
    inserted, are the sensor-backed signs of that, so the search stops on either.
    """
    try:
        r = urllib.request.urlopen(
            B + "/printer/objects/query?ace_instance_0&ace_buffer_watch", timeout=10)
        st = json.load(r)["result"]["status"]
        slots = st.get("ace_instance_0", {}).get("slots", [])
        if slot >= len(slots) or slots[slot].get("status") != "ready":
            return False, "slot %d status is %r, not 'ready'" % (
                slot, slots[slot].get("status") if slot < len(slots) else None)
        ins = st.get("ace_buffer_watch", {}).get("inserted")
        if isinstance(ins, list) and slot < len(ins) and not ins[slot]:
            return False, "buffer no longer reports lane %d inserted" % slot
        return True, ""
    except Exception as e:
        return False, "could not read lane state (%s)" % e


def live_read(a):
    """Two pages, and they must differ. Returns the page-4 bytes, or None."""
    a.batch([(6, 0)])
    _, _, rx4 = a.frame([0x30, 0x04], cmd=PCD_TRANSCEIVE, rx=16)
    a.batch([(6, 0)])
    _, _, rx20 = a.frame([0x30, 0x14], cmd=PCD_TRANSCEIVE, rx=16)
    if not any(rx4) or bytes(rx4) == bytes(rx20):
        return None
    return bytes(rx4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, required=True)
    ap.add_argument("--step", type=int, default=25, help="mm of rollback per probe")
    ap.add_argument("--budget", type=int, default=600,
                    help="mm of total rollback before giving up. 600 is one revolution of a 1kg "
                         "spool AND the dry-roller's own default_range for a lane whose gate "
                         "boundary has never been measured - do not exceed it without a "
                         "measured ace_dryroll_range for this lane.")
    ap.add_argument("--speed", type=int, default=15)
    ap.add_argument("--no-restore", action="store_true",
                    help="leave the spool where the tag was found (the default restores it)")
    args = ap.parse_args()

    reader = args.slot // 2
    a = Ace(reader=reader, slot=args.slot)
    print("slot %d -> reader %d (shares its antenna with slot %d)"
          % (args.slot, reader, args.slot ^ 1))

    a.wake()
    a.batch([(1, BitFramingReg, 0x00)])

    moved = 0
    found = None
    while moved <= args.budget:
        found = live_read(a)
        if found:
            break
        ok, why = lane_safe(args.slot)
        if not ok:
            print("")
            print("STOPPING: %s" % why)
            print("Rolled %dmm so far. Restore with: ACE_RAW_FEED T=%d MODE=0 LENGTH=%d SPEED=%d"
                  % (moved, args.slot, moved, args.speed))
            return 2
        raw_feed(args.slot, 1, args.step, args.speed)
        moved += args.step
        # The move is asynchronous, so wait for the filament to actually travel before probing.
        time.sleep(max(0.6, args.step / float(args.speed) + 0.35))
        print("  rolled %4dmm ... no tag yet" % moved)

    if found:
        print("\nTAG PARKED after %dmm of rollback" % moved)
        print("  page 4: %s" % binascii.hexlify(found).decode())
        print("\nThe reader stays powered ~7.85s after a scan and re-arms on each identify,")
        print("so dump it now:  python ace_tag_dump.py --slot %d --json tag.json" % args.slot)
    else:
        print("\nNo tag found within %dmm (about one full revolution)." % args.budget)
        print("Either this spool carries no tag, or the neighbour bay's tag is colliding.")

    if moved and not args.no_restore and not found:
        print("restoring %dmm" % moved)
        raw_feed(args.slot, 0, moved, args.speed)
    elif moved and found and not args.no_restore:
        print("\nNOT restoring position - the tag is only readable where it now sits.")
        print("Run:  ACE_RAW_FEED T=%d MODE=0 LENGTH=%d SPEED=%d   when finished."
              % (args.slot, moved, args.speed))
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
