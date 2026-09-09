#!/usr/bin/env python3
"""pi_flash_helper.py -- Flash ACE 2 Pro MCU while safely pausing/restarting Klippy."""
import os
import sys
import time
import signal
import subprocess

def get_klippy_pid():
    try:
        out = subprocess.check_output(["pgrep", "-f", "klippy.py"]).decode().strip()
        pids = [int(p) for p in out.split() if p]
        return pids[0] if pids else None
    except Exception:
        return None

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Flash ACE 2 Pro MCU while safely pausing/restarting Klippy")
    parser.add_argument("--fw", default="/tmp/ACE2-Open-V1.1.48O.bin", help="Path to firmware binary")
    parser.add_argument("--ver", default="1.1.48", help="Firmware version string")
    parser.add_argument("--port", default="/dev/ttyACM1", help="Serial port")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run")
    args = parser.parse_args()

    dry_run = args.dry_run
    fw_path = args.fw
    if not os.path.exists(fw_path):
        sys.exit(f"Firmware binary not found: {fw_path}")

    pid = get_klippy_pid()
    print(f"Klippy PID: {pid}")

    if pid:
        print(f"Sending SIGSTOP to Klippy (PID {pid})...")
        os.kill(pid, signal.SIGSTOP)
        time.sleep(0.5)

    flash_success = False
    try:
        cmd = [
            sys.executable, "/home/simon/ace2-ota-update.py",
            args.port, fw_path,
            "--version", args.ver, "--force"
        ]
        if dry_run:
            cmd.append("--dry-run")

        print("Executing:", " ".join(cmd))
        res = subprocess.run(cmd)
        print("Return code:", res.returncode)
        flash_success = (res.returncode == 0)
    finally:
        if pid:
            if dry_run or not flash_success:
                print(f"Resuming original Klippy (PID {pid}) with SIGCONT...")
                try:
                    os.kill(pid, signal.SIGCONT)
                except Exception as e:
                    print("Error resuming Klippy:", e)
            else:
                print(f"Terminating old Klippy (PID {pid}) to let systemd restart it clean...")
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception as e:
                    print("Error killing Klippy:", e)

    return 0 if flash_success else 1

if __name__ == "__main__":
    sys.exit(main())
