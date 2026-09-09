import sys
import time
import json
import urllib.request
import urllib.parse

B = "http://127.0.0.1:7125"

def post(script):
    urllib.request.urlopen(urllib.request.Request(
        B + "/printer/gcode/script?script=" + urllib.parse.quote(script),
        method="POST"), timeout=10).read()

def get(path):
    return json.loads(urllib.request.urlopen(B + path, timeout=10).read().decode("utf-8"))["result"]

def main():
    t0 = time.time()
    post("ACE_RAW_CMD T=1 CMD=FILAMENT_IDENTIFY INDEX=1")
    time.sleep(0.6)
    store = get("/server/gcode_store?count=15")["gcode_store"]
    for item in store:
        if item["time"] > t0 - 0.5 and "FILAMENT_IDENTIFY {'index': 1}" in item["message"] and "result" in item["message"]:
            print("MATCHED RESULT:")
            print(item["message"])
            return

if __name__ == "__main__":
    main()
