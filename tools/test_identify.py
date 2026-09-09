import socket
import json
import time
import urllib.request

def send_gcode(script):
    sock_path = "/home/simon/printer_data/comms/klippy.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    s.settimeout(2.0)
    msg = {"id": 1, "method": "gcode/script", "params": {"script": script}}
    s.sendall(json.dumps(msg).encode("utf-8") + b"\x03")
    buf = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\x03" in buf:
                print("Socket received:", buf.decode("utf-8", "replace"))
                break
    except Exception as e:
        print("Recv exception or timeout:", e)
    s.close()

def main():
    t0 = time.time()
    send_gcode("ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX=2147483648")
    time.sleep(0.5)
    r = json.loads(urllib.request.urlopen("http://127.0.0.1:7125/server/gcode_store?count=5").read().decode("utf-8"))
    print(f"Current time: {time.time()}, t0: {t0}")
    for item in r["result"]["gcode_store"]:
        print(f"[{item['type']}] {item['time']}: {item['message']}")

if __name__ == "__main__":
    main()
