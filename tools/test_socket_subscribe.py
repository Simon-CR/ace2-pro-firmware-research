import socket
import json
import time

def main():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect("/home/simon/printer_data/comms/klippy.sock")
    
    # Subscribe to gcode output
    sub = {"id": 1, "method": "gcode/subscribe_output", "params": {"response_template": {"method": "gcode_resp"}}}
    s.sendall(json.dumps(sub).encode("utf-8") + b"\x03")
    
    idx = 2147876864  # op 6 (SELECT)
    cmd = {"id": 2, "method": "gcode/script", "params": {"script": f"ACE_RAW_CMD T=0 CMD=FILAMENT_IDENTIFY INDEX={idx}"}}
    s.sendall(json.dumps(cmd).encode("utf-8") + b"\x03")
    
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 3.0:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
        for part in buf.split(b"\x03"):
            if part.strip():
                try:
                    data = json.loads(part.decode("utf-8"))
                    print("Received:", data)
                    resp_str = data.get("params", {}).get("response", "")
                    if "'code': " in resp_str:
                        code = int(resp_str.split("'code': ")[1].split(",")[0].split("}")[0])
                        print(f"SUCCESS: Extracted code = 0x{code:02X} ({code})")
                        s.close()
                        return
                except Exception:
                    pass
    s.close()

if __name__ == "__main__":
    main()
