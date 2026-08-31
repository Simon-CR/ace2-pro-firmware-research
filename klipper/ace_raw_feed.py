# Raw ACE 2 protocol sender for experiments. Diagnostic only. Added 2026-08-28.
#   ACE_RAW_FEED T=<tool> MODE=<0..3> [SPEED=10] [LENGTH=0]   -> FEED_OR_ROLLBACK
#   ACE_RAW_STOP T=<tool>                                     -> STOP_FEED_OR_ROLLBACK
#   ACE_RAW_CMD T=<tool> CMD=<name> [KEY=value ...]            -> any command; index defaults to T's slot
# FEED_OR_ROLLBACK modes: 0 feed, 1 rollback, 2 feed-assist, 3 rollback-assist (measured).
# Mode 3 is hakimio's FEED_MODE_UNWIND_ASSIST, not a discovery here; the mainline ACEPRO driver
# has no reverse assist, which is the reason this command exists.
# Assist modes ignore SPEED and LENGTH, and the device raises ASSIST_ERROR ~1 s after the toolhead
# stops pulling -- issue ACE_RAW_STOP within about a second, not within the MCU's 4 s backstop.
import json
import logging
from .ace.config import get_instance_from_tool, get_local_slot


class AceRawFeed:
    def __init__(self, config):
        self.printer = config.get_printer()
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('ACE_RAW_FEED', self.cmd_raw_feed,
                               desc="Send FEED_OR_ROLLBACK with an explicit mode. T= MODE= [SPEED=] [LENGTH=]")
        gcode.register_command('ACE_RAW_STOP', self.cmd_raw_stop,
                               desc="Send STOP_FEED_OR_ROLLBACK. T=")
        gcode.register_command('ACE_RAW_CMD', self.cmd_raw_cmd,
                               desc="Send any ACE command by name. T= CMD= [KEY=value ...]")

    def _inst_slot(self, gcmd):
        tool = gcmd.get_int('T')
        manager = self.printer.lookup_object('ace')
        idx = get_instance_from_tool(tool)
        if idx < 0 or idx >= len(manager.instances):
            raise gcmd.error("ACE_RAW: tool T%d is not on an ACE instance" % tool)
        return manager.instances[idx], get_local_slot(tool, idx)

    def _send(self, gcmd, inst, command, params):
        # Any failure building the frame (unknown command name, bad params) must surface as a
        # command error. Letting a plain exception escape makes Klipper declare an internal
        # error and SHUT THE PRINTER DOWN -- which is exactly what an unsupported CMD= did.
        try:
            request = inst.protocol._build_command_request(command, params)
        except Exception as e:
            raise gcmd.error("ACE_RAW: cannot build %s: %s" % (command, e))

        def cb(response):
            msg = "ACE_RAW %s %s -> %s" % (command, params, response)
            logging.info(msg)
            gcmd.respond_info(msg)
        inst.send_request(request, cb)
        gcmd.respond_info("ACE_RAW sent %s %s" % (command, params))

    def cmd_raw_feed(self, gcmd):
        inst, slot = self._inst_slot(gcmd)
        mode = gcmd.get_int('MODE', minval=0, maxval=7)
        speed = gcmd.get_int('SPEED', 10, minval=1, maxval=120)
        length = gcmd.get_int('LENGTH', 0, minval=0, maxval=3000)
        self._send(gcmd, inst, "FEED_OR_ROLLBACK",
                   {"index": slot, "speed": speed, "length": length, "mode": mode})

    def cmd_raw_stop(self, gcmd):
        inst, slot = self._inst_slot(gcmd)
        self._send(gcmd, inst, "STOP_FEED_OR_ROLLBACK", {"index": slot})

    def cmd_raw_cmd(self, gcmd):
        inst, slot = self._inst_slot(gcmd)
        command = gcmd.get('CMD').upper()
        # Blocked deliberately. Grouped by why, because the reasons differ:
        #
        #  motion  - drives filament with no print interlock. MOTOR_TEST (77) is the worst:
        #            it shares FEED_OR_ROLLBACK's worker, ignores printer status, and will
        #            drive a lane the firmware has already flagged as jammed or tangled.
        #  heat    - SET_DRY_POWER writes the heater triac duty DIRECTLY, with no state gate,
        #            no temperature reference, no timer and nothing that will ever turn it
        #            off. SET_PTC_TEMP installs a PID target with NO upper bound and zeroes
        #            the setpoint, which also disarms the thermistor fault shutdown. Use the
        #            driver's dryer macros (cmd 11 DRYING), which are properly clamped.
        #  flash   - SET_MATERIAL_NAME / SET_SLOT_STATUS commit the whole settings block to
        #            flash on EVERY call, even when nothing changed: two 2KB page erases per
        #            command, ~10k cycle endurance. Calling these in a loop or per toolchange
        #            destroys the settings pages.
        #  config  - LINEAR_KEY_CALIBRATE writes filament-detector thresholds that PERSIST
        #            across reboot and OTA, from a single instantaneous ADC sample, accepted
        #            mid-print. A plausible-but-wrong calibration is effectively permanent.
        BLOCKED = {
            "FEED_OR_ROLLBACK": "motion", "UPDATE_SPEED": "motion", "MOTOR_TEST": "motion",
            "DRYING": "heat", "SET_DRY_TEMP": "heat", "SET_DRY_POWER": "heat",
            "SET_PTC_TEMP": "heat",
            "SET_MATERIAL_NAME": "flash wear", "SET_SLOT_STATUS": "flash wear",
            "LINEAR_KEY_CALIBRATE": "config", "SET_FEED_CHECK": "config",
            "ASSIGN_DEVICE_ID": "config",
            "SET_VALVE": "actuator", "SET_OUTPUT": "actuator", "FLASH_LED": "actuator",
        }
        if command in BLOCKED:
            raise gcmd.error("ACE_RAW_CMD: %s is blocked (%s). See the comment in "
                             "ace_raw_feed.py for why." % (command, BLOCKED[command]))
        params = {}
        for k, v in gcmd.get_command_parameters().items():
            if k in ("T", "CMD"):
                continue
            key = k.lower()
            lv = str(v).strip().lower()
            if lv in ("true", "false"):
                params[key] = (lv == "true")
            elif key == "enable":
                params[key] = bool(int(lv))
            elif lv.lstrip("-").isdigit():
                params[key] = int(lv)
            else:
                params[key] = v
        params.setdefault("index", slot)
        self._send(gcmd, inst, command, params)


def load_config(config):
    return AceRawFeed(config)
