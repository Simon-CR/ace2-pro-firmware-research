import socket
import json

def main():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect("/home/simon/printer_data/comms/klippy.sock")
    msg = {"id": 99, "method": "gcode/script", "params": {"script": "ACE_RAW_CMD T=1 CMD=FILAMENT_IDENTIFY INDEX=1"}}
    s.sendall(json.dumps(msg).encode("utf-8") + b"\x03")
    data = s.recv(4096)
    print("Received:", data.decode("utf-8", "replace"))
    s.close()

if __name__ == "__main__":
    main()
