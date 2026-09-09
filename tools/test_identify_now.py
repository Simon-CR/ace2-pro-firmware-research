import sys
import time
import json
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from ace_direct import AceDirect

def main():
    ace = AceDirect(reader=0, slot=1)
    print("Clearing MCU cached slot 1 record (op 8)...")
    ace.clear_cached_slot(1)
    time.sleep(0.2)
    
    print("Triggering live hardware FILAMENT_IDENTIFY on Slot 1...")
    ace.trigger_identify(1)
    time.sleep(0.8)
    ace.close()
    
    # Query Moonraker
    r = json.loads(urllib.request.urlopen("http://127.0.0.1:7125/printer/objects/query?ace_instance_0").read().decode("utf-8"))
    slot1 = r["result"]["status"]["ace_instance_0"]["slots"][1]
    print("\n--- MOONRAKER SLOT 1 LIVE TELEMETRY ---")
    print("Status:       ", slot1.get("status"))
    print("SKU:          ", slot1.get("sku"))
    print("Material:     ", slot1.get("material"))
    print("Color [R,G,B]:", slot1.get("color"))
    print("Extruder Temp:", slot1.get("extruder_temp"))
    print("Hotbed Temp:  ", slot1.get("hotbed_temp"))
    print("Diameter:     ", slot1.get("diameter"))
    print("Total (g):    ", slot1.get("total"))
    print("RFID:         ", slot1.get("rfid"))

if __name__ == "__main__":
    main()
