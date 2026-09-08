"""
AceInstance: Manages a single physical ACE Pro unit.
"""

import logging
import time
import json
import copy
from .config import (
    INSTANCE_MANAGERS,
    SLOTS_PER_ACE,
    AceSlotStateMachineState,
    SENSOR_RDM,
    SENSOR_TOOLHEAD,
    FILAMENT_STATE_SPLITTER,
    FILAMENT_STATE_TOOLHEAD,
    FILAMENT_STATE_NOZZLE,
    RFID_STATE_NO_INFO,
    RFID_STATE_IDENTIFIED,
    MAX_RETRIES,
    get_tool_offset,
    get_ace_instance_and_slot_for_tool,
    create_inventory,
    create_status_dict,
    normalize_ace_slot_state,
)
from .protocol import create_protocol_adapter, normalize_protocol_name, resolve_protocol_name
from .serial_manager import AceSerialManager


# === BEGIN PURE TAG POLICY HELPERS (Worker 3) ===
# Every heat/bind decision the capture path makes is expressed here as a pure function, with no
# Klipper dependency, so the safety-critical logic can be unit-tested outside Klipper (instance.py
# itself cannot be imported without the hardware stack). The methods below CALL these; they never
# re-implement the decision inline.

NATIVE_TAG_VERSION = 101       # 0x65: Anycubic positional decode - the ONLY trusted layout (R1)
RAW_UID_SENTINEL = 0x0201      # 513: firmware handed the tag UID back as hex in result["sku"]
BAMBU_UID_SENTINEL = RAW_UID_SENTINEL  # compatibility alias
RAW_TAG_SENTINEL_V = 0x0202    # 514: non-Anycubic tag, raw image cached (== AceInstance.RAW_TAG_SENTINEL)
INJECTED_SKU_SENTINEL = 0x0203 # 515: firmware found an sm_id and wrote it to sku. The sku is
                               # authoritative; EVERY other field in the record is the stock
                               # positional parse of a tag that is not in Anycubic's layout,
                               # i.e. garbage. Firmware V1.1.42+ emits this instead of 101.
                               # Claiming 101 was a lie that only OUR host knew to discount -
                               # any other consumer would have read a heat target out of it.

# Formats whose parsed fields describe a real filament and MAY seed a heat target. Deliberately
# excludes unknown/ndef/ndef-json/blank/bambu: a generic or unproven decode renders, never heats.
_REAL_TAG_FORMATS = frozenset((
    "anycubic", "openspool", "filaman", "openprinttag", "json"))


def _normalise_uid_local(uid):
    """Strip separators and upper-case a UID for comparison, without importing anything.

    Mirrors ace_tag_formats.normalise_uid so the anticollision check still works if that module is
    momentarily unavailable; the wire handoff re-normalises in Moonraker regardless.
    """
    if not uid:
        return ""
    return "".join(c.upper() for c in str(uid) if c in "0123456789abcdefABCDEF")


def classify_tag_version(version):
    """Map a GET_FILAMENT_INFO `version` to the capture action. The R1 heater whitelist.

    Returns "native" | "bambu" | "raw" | "injected" | "foreign". Positional temp/material fields
    may be trusted ONLY for "native"; every other result routes to UID resolution, a raw-image
    fetch, or - for "injected" - the sku alone.
    """
    try:
        v = int(version or 0)
    except (TypeError, ValueError):
        return "foreign"
    if v == NATIVE_TAG_VERSION:
        return "native"
    if v in (RAW_UID_SENTINEL, BAMBU_UID_SENTINEL):
        return "uid"
    if v == RAW_TAG_SENTINEL_V:
        return "raw"
    if v == INJECTED_SKU_SENTINEL:
        return "injected"
    return "foreign"


def raw_temp_to_store(fmt, temp_min, temp_max):
    """The nozzle temp to store from a RAW-fetched tag image, or None. The R1/R3 heat gate.

    Allowed only when the tag is a RECOGNISED real format AND the value is physically plausible
    (0 < t < 500) AND, when both bounds are present, temp_min <= temp_max. Anything else -> None:
    render colour/material, but never heat off an unproven decode.
    """
    if fmt not in _REAL_TAG_FORMATS:
        return None

    def _int(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    lo, hi = _int(temp_min), _int(temp_max)
    if lo is not None and hi is not None and lo > hi:
        return None                      # inverted range: a positional-misparse tell
    t = hi if hi is not None else lo     # prefer the max, matching the historical raw path
    if t is None or not (0 < t < 500):
        return None
    return t


def anticollision_ok(uid_self, uid_neighbour):
    """Whether slot S may bind, given its paired-antenna neighbour's UID. The R2 guard.

    reader = slot // 2, so slots 0+1 and 2+3 share one antenna. If the neighbour presents the SAME
    normalised UID, the shared coil read one tag twice and the capture cannot be attributed to S
    -> refuse. An empty UID on either side is no evidence of a collision -> allow.
    """
    a = _normalise_uid_local(uid_self)
    b = _normalise_uid_local(uid_neighbour)
    return not (a and b and a == b)


def reply_index_matches(reply_index, slot_idx):
    """Whether a GET_FILAMENT_INFO reply may be attributed to the slot asked. The R2 attribution.

    Only an UNAMBIGUOUS mismatch rejects. The protocol decodes an ABSENT index field as 0
    (protocol_ace2.py `_pb_first(fields, 1, 0)`), so 0 is ambiguous - a real slot 0 or no index at
    all - and must never be treated as proof of misattribution, or a firmware that omits the field
    would falsely reject every non-zero lane. A reply with no index (None) or index 0 is trusted
    to the closure; only a POSITIVE index that differs from slot_idx is proof of another lane.
    """
    if reply_index is None:
        return True
    try:
        ri = int(reply_index)
    except (TypeError, ValueError):
        return True
    if ri <= 0:
        return True
    return ri == int(slot_idx)


def build_wire_record(rec, uid, native):
    """Assemble the Klipper->Moonraker wire payload from a parsed record. The R7 helper.

    Copies only public JSON-serialisable fields (drops `_raw_json`, `_truncated`, `why` and any
    other private key), forces `format` to a string, and stamps uid/native so the resolver sees
    exactly the contract's PARSED RECORD shape.
    """
    wire = {}
    for k, v in dict(rec or {}).items():
        if not isinstance(k, str) or k.startswith("_") or k == "why":
            continue
        wire[k] = v
    wire["format"] = str((rec or {}).get("format") or "unknown")
    wire["uid"] = uid or None
    wire["native"] = bool(native)
    return wire
# === END PURE TAG POLICY HELPERS (Worker 3) ===


def _extruder_accel(printer, default=1500.):
    """Acceleration for FORCE_MOVE, from the printer's own extruder limit.

    FORCE_MOVE defaults ACCEL to 0, and Klipper's calc_move_time treats 0 as "no ramp":

        if not accel or not dist:
            return axis_r, 0., dist / speed, speed      # accel_t = 0

    The stepper is commanded straight to cruise velocity from standstill. At the tandem
    extract's 20mm/s, with rotation_distance 47.088 and a 9:1 gear ratio, that is 229 motor
    RPM - about 12,200 steps/s - demanded instantly at 0.6A against 900mm of loaded bowden.
    A stepper cannot follow that, so it skips, and every FORCE_MOVE in this driver did it.

    Measured 2026-09-01: a 48mm tandem pull moved 23 hub-encoder pulses (~21mm). The tip needs
    ~20mm to get from post-gear back past entry, so it landed just short EVERY time - which is
    the whole "unloading a parked lane takes two presses" symptom. The second press succeeded
    because the first left the path slack, so the same instantaneous jump lost fewer steps.

    max_extrude_only_accel is the machine's own answer and it is already configured (1500).
    The ramp to 20mm/s costs 13ms and 0.13mm, so nothing here gets slower in any way that
    matters. Note calc_move_time also clamps speed to sqrt(dist * accel) - at 1500 that is
    77mm/s over a 4mm segment, well above anything this driver asks for, so no move is slowed.
    """
    try:
        extruder = printer.lookup_object("toolhead").get_extruder()
        accel = float(extruder.get_heater().get_status(0).get("max_extrude_only_accel", 0))
        if accel > 0.:
            return accel
    except Exception:
        pass
    try:
        cfg = printer.lookup_object("configfile").get_status(None)["settings"]
        accel = float(cfg.get("extruder", {}).get("max_extrude_only_accel", 0) or 0)
        if accel > 0.:
            return accel
    except Exception:
        pass
    return default


class AceInstance:
    """Manages a single physical ACE Pro unit with 4 slots."""

    # Defaults for slots that report ready but provide no metadata
    DEFAULT_MATERIAL = "Unknown"
    DEFAULT_COLOR = [0, 0, 0]
    DEFAULT_TEMP = 0

    # Feed assist restore retries: a freshly powered ACE spends time in RFID
    # identification (busy) - retry once per heartbeat (~1 Hz) up to this cap
    FEED_ASSIST_RESTORE_MAX_ATTEMPTS = 30

    # Consecutive FORBIDDEN rejections tolerated per incremental feed step
    # before aborting (FORBIDDEN = previous feed still executing; a long
    # streak means the device is genuinely refusing to feed)
    INCREMENTAL_FEED_FORBIDDEN_MAX = 5

    # Grace period (s) before a slot gear_err aborts a feed wait: _info
    # refreshes at 1 Hz, so right after starting a feed it may still hold a
    # stale error from a previous attempt (the feed command clears it on the
    # device). Firmware needs ~16-18s to declare an error, so 2s loses nothing.
    FEED_ERROR_GRACE_S = 2.0
    # A slot error must survive one full heartbeat refresh (1Hz) before it aborts a
    # load: the status cache can still hold the PREVIOUS attempt's gear_err when the
    # grace check first looks, and a single stale frame killed two healthy loads on
    # 2026-08-22. A real fault persists; a stale one is overwritten by the next beat.
    FEED_ERROR_CONFIRM_S = 1.2

    # Back-off to the entry sensor's release edge after the approach feed stops.
    # Chunked under the lane buffer's ~3mm travel, same rationale as the eject.
    RELEASE_CHUNK_MM = 2.0
    RELEASE_BACKOFF_SPEED = 20.0
    # Measured healthy back-off is ~34mm: ~10mm of buffer relax (ACE-side retraction
    # that moves no tip) + stop overshoot + hysteresis. Not releasing within this
    # means the filament is not actually moving back.
    RELEASE_BACKOFF_CAP_MM = 60.0

    # Material temperature defaults (from RFID tags)
    MATERIAL_TEMPS = {
        "PLA": 200,
        "PLA+": 210,
        "PLA Glow": 210,
        "PLA High Speed": 215,
        "PLA Marble": 205,
        "PLA Matte": 205,
        "PLA SE": 210,
        "PLA Silk": 215,
        "ABS": 240,
        "ASA": 245,
        "PETG": 235,
        "TPU": 210,
        "PVA": 185,
        "HIPS": 230,
        "PC": 260,
    }

    def __init__(
        self,
        instance_num,
        ace_config,
        printer,
        ace_enabled=True,
        protocol=None,
        active_protocol_name=None,
        serial_mgr=None,
        bus_session=None,
        target_usb_location=None,
    ):
        """
        Initialize ACE instance.

        Args:
            instance_num: Instance number (0, 1, 2, ...)
            ace_config: Configuration dict
            printer: Klipper printer object
            ace_enabled: Initial ACE Pro enabled state
            target_usb_location: Physical USB location this instance is
                bound to, as resolved from daisy-chain topology order by
                AceManager. Ignored when `serial_mgr` is already provided
                (shared-bus instances build their AceSerialManager elsewhere).
        """
        self.variables = {}
        self.SLOT_COUNT = SLOTS_PER_ACE
        self.instance_num = instance_num
        self.ace_config = ace_config  # Store for later access (e.g., rfid_temp_mode)
        self.baud = ace_config["baud"]
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object("gcode")
        self.timeout_multiplier = ace_config["timeout_multiplier"]
        self.filament_runout_sensor_name_rdm = ace_config["filament_runout_sensor_name_rdm"]
        self.filament_runout_sensor_name_nozzle = ace_config["filament_runout_sensor_name_nozzle"]
        self.feed_speed = float(ace_config["feed_speed"])
        self.retract_speed = float(ace_config["retract_speed"])
        self.total_max_feeding_length = float(ace_config["total_max_feeding_length"])
        self.parkposition_to_toolhead_length = float(ace_config["parkposition_to_toolhead_length"])
        self.toolchange_load_length = float(ace_config["toolchange_load_length"])
        self.parkposition_to_rdm_length = float(ace_config["parkposition_to_rdm_length"])
        self.rdm_overshoot_length = float(ace_config["rdm_overshoot_length"])
        self.incremental_feeding_length = float(ace_config["incremental_feeding_length"])
        self.incremental_feeding_speed = float(ace_config["incremental_feeding_speed"])
        self.extruder_feeding_length = float(ace_config["extruder_feeding_length"])
        self.extruder_feeding_speed = float(ace_config["extruder_feeding_speed"])
        self.runout_bite_length = float(ace_config["runout_bite_length"])
        self.runout_hug_length = float(ace_config["runout_hug_length"])
        self._stub_load_completed = False
        self.entry_to_gear_mm = float(ace_config.get("entry_to_gear_mm", 12.0))
        self.seat_verify_sensor = (ace_config.get("seat_verify_sensor") or "").strip()
        self.toolhead_slow_loading_speed = float(ace_config["toolhead_slow_loading_speed"])
        self.heartbeat_interval = float(ace_config["heartbeat_interval"])
        self.max_dryer_temperature = float(ace_config["max_dryer_temperature"])
        # Fast disconnect pause threshold (s); negative = auto (protocol
        # default: ACE1 30, ACE2 5), 0 disables. Resolved by the manager.
        self.disconnect_pause_timeout = float(
            ace_config.get("disconnect_pause_timeout", -1.0)
        )

        self.rfid_inventory_sync_enabled = ace_config.get("rfid_inventory_sync_enabled", True)
        self.feed_assist_active_after_ace_connect = ace_config.get(
            "feed_assist_active_after_ace_connect", True
        )

        # Not overridable per instance
        self.toolhead_full_purge_length = float(ace_config["toolhead_full_purge_length"])

        self.toolhead = None
        self._info = create_status_dict(self.SLOT_COUNT)
        # When the frame in self._info arrived. There was no way to ask this, which is why the
        # code that needs a FRESH frame had to substitute a sleep for a check - see the settle in
        # _tandem_extract. 0.0 means "no frame has ever arrived".
        self._info_at = 0.0
        # When opcode 9 (STOP_FEED_OR_ROLLBACK) was last sent to this device, and how many
        # motion commands have been issued since. Nothing recorded either, which is why the
        # question below has never been answerable. 0.0 means "no stop this session".
        self._last_stop_at = 0.0
        self._motion_since_stop = 0
        # The initialized _info reports every slot 'empty'; True once a real
        # device status has been applied (see _status_update_callback)
        self._device_status_seen = False
        self.inventory = create_inventory(self.SLOT_COUNT)
        self._feed_assist_index = -1
        # D-C, 2026-09-01. True between the mode-2 START going out and the device answering.
        # _feed_assist_index is set optimistically before the send (the ACE2 busy/deadlock
        # logic below needs it already set), so for that window the driver holds a claim the
        # device has not confirmed. get_status() reports -1 while this is set: a guard reading
        # a false NEGATIVE refuses to move, which is safe; a guard reading a false POSITIVE
        # moves the extruder against a lane the ACE may still be clamping, which is the grind.
        self._feed_assist_ack_pending = False
        # Mode 3. The firmware refuses mode 0/2 while this slot is rollback_assisting.
        self._rollback_assist_index = -1
        self.last_load_parked = False
        # Slot with a load in flight. ace_current_index only flips at load END, so the
        # single-assist invariant below would read the incoming lane's assist as stale
        # and destroy it mid-load (2026-08-22: cleared during T3's load; the purge then
        # drove the extruder against a static ACE and ground the filament).
        self._loading_slot = -1
        self._feed_assist_topology_position = None  # Track chain position (0, 1, 2...)
        self._pending_feed_assist_restore = -1  # Slot to restore after first heartbeat
        self._feed_assist_restore_attempts = 0  # Retry counter for busy-deferred restores
        self._assist_lost_streak = 0  # Consecutive heartbeats contradicting assist state (ACE2)
        self._pending_rfid_refresh = False  # Flag to refresh all RFID data after reconnect
        self._pending_rfid_refresh_slots = []  # Reconnect RFID refresh queue (throttled)
        self._last_retract_early_stopped = False  # Slot sensor confirmed empty during _retract() (or slot was already empty)
        self._dryer_active = False
        self._dryer_temperature = 0
        self._dryer_duration = 0
        self._pending_rfid_queries = set()  # Track slots with in-flight RFID queries
        # Last normalised UID observed per slot, for the R2 paired-antenna anticollision check
        # (slots 0+1 and 2+3 share one reader). Refreshed on every UID-bearing decode.
        self._slot_uid = {}
        # Fix 2: per-slot count of consecutive cross-lane (neighbour's tag) reads, so a
        # stationary lane gives up instead of re-reading forever. Reset on an accepted read.
        self._rfid_xlane_retry = {}
        # Per-reader lock-in identification owner: reader_idx (slot//2) -> the slot being
        # identified by identify_by_jog right now, or absent/-1. Serializes the shared coil
        # (slots 0+1, 2+3) and gates the autonomous read. See SHARED_READER_ARBITRATION.md.
        self._reader_id_owner = {}
        self.status_failure_threshold = max(
            1,
            int(ace_config.get("status_failure_threshold", 4)),
        )
        self._status_failure_streak = 0
        self._status_recovery_in_progress = False

        self.status_debug_logging = bool(ace_config.get("status_debug_logging", False))
        self.supervision_enabled = bool(ace_config.get("ace_connection_supervision", True))
        self.configured_protocol_name = normalize_protocol_name(
            ace_config.get("protocol", "auto")
        )
        self.protocol_name = active_protocol_name or resolve_protocol_name(self.configured_protocol_name)
        self.protocol = protocol or create_protocol_adapter(self.protocol_name)
        self.transport_spec = self.protocol.get_transport_spec()
        self.bus_session = bus_session

        self.serial_mgr = serial_mgr or AceSerialManager(
            self.gcode,
            self.reactor,
            instance_num,
            ace_enabled=ace_enabled,
            status_debug_logging=self.status_debug_logging,
            supervision_enabled=self.supervision_enabled,
            protocol=self.protocol,
            target_usb_location=target_usb_location,
        )
        self.tool_offset = get_tool_offset(self.instance_num)
        if not self.transport_spec.shared_bus:
            self.serial_mgr.set_heartbeat_callback(self._on_heartbeat_response)
        self.serial_mgr.set_on_connect_callback(self._on_ace_connect)
        self._dryer_start_logged = False  # prevent duplicate dryer start messages
        self._shared_bus_heartbeat_timer = None

    def rebind_transport(
        self,
        protocol,
        protocol_name,
        baud,
        serial_mgr=None,
        bus_session=None,
        target_usb_location=None,
    ):
        """Swap this instance's protocol/transport in place.

        Used by ``AceManager`` re-detection (``ACE_REDETECT``) to recover an
        instance that was mis-typed by an empty/incomplete startup USB scan -
        e.g. an instance frozen on ``ace1_json`` because its ACE2 RS-485 adapter
        enumerated late, leaving it unable to ever find its port.

        Only the protocol adapter, serial transport, bound bus session and baud
        change; instance identity is preserved so inventory, tool mapping,
        sensors and monitors that already reference this object stay valid.

        Callers must gate the *decision* to re-type on positive evidence
        (ACE2 ``DISCOVER_DEVICE`` confirming an unbound unit) - this method only
        performs the swap.
        """
        was_enabled = True
        if self.serial_mgr is not None:
            is_enabled = getattr(self.serial_mgr, "is_ace_pro_enabled", None)
            if callable(is_enabled):
                was_enabled = bool(is_enabled())

        self.protocol = protocol
        self.protocol_name = protocol_name
        self.transport_spec = protocol.get_transport_spec()
        self.baud = baud
        self.bus_session = bus_session
        self.serial_mgr = serial_mgr or AceSerialManager(
            self.gcode,
            self.reactor,
            self.instance_num,
            ace_enabled=was_enabled,
            status_debug_logging=self.status_debug_logging,
            supervision_enabled=self.supervision_enabled,
            protocol=self.protocol,
            target_usb_location=target_usb_location,
        )
        if not self.transport_spec.shared_bus:
            self.serial_mgr.set_heartbeat_callback(self._on_heartbeat_response)
        self.serial_mgr.set_on_connect_callback(self._on_ace_connect)

    def _prepare_request(self, request):
        """Normalize request and attach ACE2 shared-bus target when known."""
        prepared_request = self.protocol.normalize_request(request)

        # MEASURE THE POISONING WINDOW. Instrumentation only - nothing here changes behaviour.
        #
        # ace2-protocol-findings.md 8.3: after STOP_FEED_OR_ROLLBACK the device keeps answering
        # normally, but feed/retract issued in the next ~20-30s SILENTLY DO NOTHING - accepted,
        # ACKed, never executed. The admission gate is a context word at 0x0800B474, not the
        # work-state field GET_STATUS reports, so the device is honestly 'ready' throughout. That
        # makes the failure invisible to every witness on this machine: the ACK says yes, the
        # status says ready, and the hub encoder cannot tell a discarded retract from a slack
        # bowden.
        #
        # It is the leading explanation for the stalled tandem pull. A failed pull ends in
        # _stop_retract, which arms the window; the retry issues its retract inside it and is
        # discarded; the third attempt lands outside and works. That is exactly the two-press
        # pattern this driver documents and the enc +0 signature in the logs.
        #
        # WHY MEASURE RATHER THAN WAIT IT OUT. The obvious fix - block until the window expires -
        # would add up to 30s to EVERY swap, because _ensure_assists_off_for_motion issues a stop
        # immediately before the retract. The findings record the duration as "~20-30s, seen
        # twice" with the mechanism OPEN, so the cost is certain and the benefit is not. Two
        # numbers settle it and neither has ever been recorded: how long the window really is,
        # and whether the failing motion commands actually fall inside it.
        #
        # This is the single choke point - both send_request and send_high_prio_request funnel
        # through here - so it catches every site without touching any of them. It must never
        # raise: this is on the path of every command to the device.
        try:
            _cmd = str(prepared_request.get("command", "")).strip().upper()
            if _cmd == "STOP_FEED_OR_ROLLBACK":
                self._last_stop_at = self.reactor.monotonic()
                self._motion_since_stop = 0
            elif _cmd == "FEED_OR_ROLLBACK":
                self._motion_since_stop += 1
                if self._last_stop_at > 0.0:
                    _since = self.reactor.monotonic() - self._last_stop_at
                    logging.info(
                        "ace[%d]: FEED_OR_ROLLBACK issued %.1fs after the last STOP "
                        "(#%d since) - findings 8.3 puts the discard window at ~20-30s%s",
                        self.instance_num, _since, self._motion_since_stop,
                        "  <-- INSIDE THE SUSPECTED WINDOW" if _since < 30.0 else "")
        except Exception:
            logging.exception("ace: stop-window probe failed (ignored)")

        if not self.transport_spec.shared_bus or self.bus_session is None:
            return prepared_request

        command_name = str(prepared_request.get("command", "")).strip().upper()
        if command_name in {"DISCOVER_DEVICE", "ASSIGN_DEVICE_ID"}:
            return prepared_request

        device = self.bus_session.get_device_for_instance(self.instance_num)
        if device is None or device.device_id is None:
            raise RuntimeError(
                f"ACE[{self.instance_num}]: Shared-bus request '{command_name or 'UNKNOWN'}' "
                "requires an assigned target device_id"
            )

        prepared_request["target_device_id"] = device.device_id
        return prepared_request

    @property
    def manager(self):
        """Get the AceManager instance for this ACE unit."""
        return INSTANCE_MANAGERS.get(self.instance_num)

    @property
    def state(self):
        """Shortcut to the centralised :class:`PersistentState`."""
        return self.manager.state

    def _register_tool_macros(self):
        """Register T0-T3 (or T4-T7, etc.) macros for this instance."""
        try:
            for slot_idx in range(self.SLOT_COUNT):
                tool_num = self.tool_offset + slot_idx

                # Create closure to capture current slot_idx
                def make_tool_handler(idx):
                    def handler(gcmd):
                        gcmd.respond_info(
                            f"ACE: Tool change to T{tool_num} "
                            f"(slot {idx}, instance {self.instance_num})"
                        )
                    return handler

                desc = f"ACE tool macro - slot {slot_idx} of instance {self.instance_num}"
                self.gcode.register_command(f"T{tool_num}", make_tool_handler(slot_idx), desc=desc)

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Registered tool macros "
                f"T{self.tool_offset}-T{self.tool_offset + self.SLOT_COUNT - 1}"
            )
        except Exception as e:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Failed to register tool macros: {e}"
            )

    def send_request(self, request, callback):
        """Queue a normal request."""
        self.serial_mgr.send_request(
            self._prepare_request(request),
            callback,
        )

    def send_high_prio_request(self, request, callback):
        """Queue high-priority request."""
        self.serial_mgr.send_high_prio_request(
            self._prepare_request(request),
            callback,
        )

    def _send_shared_bus_heartbeat_request(self):
        """Send one targeted ACE2 status poll over shared transport."""
        if not self.transport_spec.shared_bus or not self.serial_mgr.is_connected():
            return

        request = self.protocol.build_get_status_request()
        self.send_high_prio_request(request, self._on_heartbeat_response)

    def _shared_bus_heartbeat_tick(self, eventtime):
        """Periodically poll one ACE2 logical instance on shared transport."""
        try:
            self._send_shared_bus_heartbeat_request()
        except Exception as exc:
            logging.warning(
                "ACE[%s]: Shared-bus heartbeat error: %s",
                self.instance_num,
                exc,
            )
        return eventtime + self.heartbeat_interval

    def start_shared_bus_heartbeat(self):
        """Start or refresh shared-bus status polling after bus init completes."""
        if not self.transport_spec.shared_bus:
            return

        self._send_shared_bus_heartbeat_request()
        if self._shared_bus_heartbeat_timer is None:
            self._shared_bus_heartbeat_timer = self.reactor.register_timer(
                self._shared_bus_heartbeat_tick,
                self.reactor.NOW,
            )

    def request_shared_bus_info_refresh(self):
        """Refresh device info through targeted ACE2 get_info after bus init."""
        if not self.transport_spec.shared_bus or not self.serial_mgr.is_connected():
            return

        request = self.protocol.build_get_info_request()
        self.send_high_prio_request(request, self.serial_mgr.handle_info_response)

    RFID_XLANE_MAX_RETRY = 6

    def _read_belongs_to_other_lane(self, slot_idx, sku):
        """The OTHER slot whose accepted inventory already holds this non-empty SKU, or None.

        Global (not paired-only): the shared antenna leaks a paired neighbour, and the shared
        page buffer can leak across readers. A blank SKU is no evidence. Case-folded/trimmed.
        """
        key = (sku or "").strip().upper()
        if not key:
            return None
        for other in range(self.SLOT_COUNT):
            if other == slot_idx:
                continue
            if (self.inventory[other].get("sku") or "").strip().upper() == key:
                return other
        return None

    def _handle_rfid_info_response(self, slot_idx, response):
        """Apply a get_filament_info response to the local inventory."""
        self._pending_rfid_queries.discard(slot_idx)

        if response and response.get("code") == 0 and "result" in response:
            result = response["result"]

            # Check if actual RFID tag is present (not just non-RFID spool)
            rfid_state = result.get("rfid", 0)

            # Don't overwrite manual data for non-RFID spools or empty slots.
            if rfid_state != RFID_STATE_IDENTIFIED:
                logging.info(
                    f"ACE[{self.instance_num}]: Slot {slot_idx} - No RFID tag (rfid={rfid_state}), "
                    f"skipping inventory update to preserve manual data"
                )
                return

            sku = result.get("sku", "")
            brand = result.get("brand", "")
            material = result.get("type", "")
            icon_type = result.get("icon_type")
            colors_array = result.get("colors")
            rfid_color = None
            if colors_array and len(colors_array) > 0:
                first_color = (
                    colors_array[0]
                    if isinstance(colors_array[0], (list, tuple))
                    else colors_array
                )
                if len(first_color) >= 3:
                    rfid_color = [first_color[0], first_color[1], first_color[2]]
            else:
                direct_color = result.get("color")
                if (direct_color and len(direct_color) >= 3
                        and any(component > 0 for component in direct_color[:3])):
                    rfid_color = [direct_color[0], direct_color[1], direct_color[2]]
            extruder_temp = result.get("extruder_temp", {})
            hotbed_temp = result.get("hotbed_temp", {})
            diameter = result.get("diameter")
            total = result.get("total")
            current = result.get("current")

            temp_min = extruder_temp.get("min", 0)
            temp_max = extruder_temp.get("max", 0)
            temp_mode = self.ace_config.get("rfid_temp_mode", "average")

            # Some RFID reads come back "identified" but with no actual
            # temperature/material payload (e.g. a blank/unreadable tag).
            # Only compute a usable temp when we have real data to derive it
            # from - otherwise leave any existing (e.g. manually-set) temp
            # alone instead of clobbering it with a bogus 0/default value.
            have_temp_data = True
            if temp_min > 0 or temp_max > 0:
                if temp_mode == "min" and temp_min > 0:
                    rfid_temp = temp_min
                elif temp_mode == "max" and temp_max > 0:
                    rfid_temp = temp_max
                elif temp_min > 0 and temp_max > 0:
                    rfid_temp = (temp_min + temp_max) // 2
                elif temp_max > 0:
                    rfid_temp = temp_max
                else:
                    rfid_temp = temp_min
            elif material and material in self.MATERIAL_TEMPS:
                rfid_temp = self.MATERIAL_TEMPS[material]
            else:
                rfid_temp = self.DEFAULT_TEMP
                have_temp_data = False

            # R2 ATTRIBUTION. The reply carries its own slot index (protocol field 1). Older
            # firmware may omit it; only a POSITIVE mismatch is proof this decode belongs to a
            # different lane, and it must never be written here on the closure's word alone.
            reply_index = result.get("index")
            if not reply_index_matches(reply_index, slot_idx):
                self.gcode.respond_info(
                    "ACE[%d]: Slot %d RFID reply was for slot %s - ignoring (attribution/"
                    "anticollision)" % (self.instance_num, slot_idx, reply_index))
                logging.warning("ace: slot %s rfid reply index mismatch (reply=%r) - ignored",
                                slot_idx, reply_index)
                self._pending_rfid_queries.discard(slot_idx)
                return

            # R1 HEATER WHITELIST. The firmware parses tag bytes POSITIONALLY against Anycubic's
            # layout and returns code 0 for ANY tag, so a foreign tag comes back as structured
            # garbage that merely LOOKS valid (2026-09-01: an OpenSpool tag decoded to
            # sku='application/json{"', temp=28770C, hotbed min 8804 > max 8762, and fed 28770 to
            # the heater). ONLY version 101 is a real Anycubic decode whose temp/material may be
            # trusted. Every other value routes away from the positional fields BEFORE any of them
            # can reach inv["temp"]; this replaces "trust anything that limps past the gate".
            version = int(result.get("version", 0) or 0)
            action = classify_tag_version(version)

            if action == "raw":
                # FIRMWARE-INJECTED SKU ON THE NON-ANYCUBIC PATH. If the firmware wrote a clean
                # "SM<n>" identity alongside the 0x0202 sentinel, that identity is authoritative
                # AND synchronous: bind from it and skip the op-9 raw walk entirely. That walk is
                # ~2.88s (no synchronous caller can wait on it - it is exactly what outran the
                # motion-gated identify loop), torn-prone (a firmware scan splices the R5 buffer)
                # and cross-reader-contaminated (slot 3 once returned slot 0's SM22) - binding off
                # it is the wrong-bind the design forbids. The positional siblings are OpenSpool
                # bytes misread as Anycubic fields, so they are nulled BEFORE routing to the native
                # path: R1 holds, no foreign field can reach the heater. Inert until the firmware
                # actually injects on this path (today sku is empty here -> raw fetch as before).
                _s = (sku or "").strip()
                _d = _s[2:] if _s.upper().startswith("SM") else _s
                if (1 <= len(_d) <= 7) and _d.isdigit():
                    self.gcode.respond_info(
                        "ACE[%d]: Slot %d non-Anycubic tag carries a firmware-injected sku %r - "
                        "binding from it, skipping the raw-image walk"
                        % (self.instance_num, slot_idx, _s))
                    material = None          # backend authoritative; never trust foreign bytes
                    brand = None
                    have_temp_data = False   # R1: the temp-seed below can never fire
                    rfid_temp = 0
                    hotbed_temp = None
                    action = "native"        # fall through to the proven sku bind + its guards
                else:
                    # 0x0202 with no injected identity: fetch and decode the image by format.
                    self.gcode.respond_info(
                        "ACE[%d]: Slot %d carries a non-Anycubic tag - fetching its raw image"
                        % (self.instance_num, slot_idx))
                    self._fetch_raw_tag_image(slot_idx)
                    return

            if action == "injected":
                # 0x0203: the firmware found an sm_id on a foreign tag and wrote it to sku. The
                # sku is real; the siblings are the stock positional parse of a layout this tag
                # does not use, so they are garbage BY CONSTRUCTION - not "probably wrong".
                # Trust the sku, drop everything else, and never let a temp out of here.
                _s = (sku or "").strip()
                _d = _s[2:] if _s.upper().startswith("SM") else _s
                if not ((1 <= len(_d) <= 7) and _d.isdigit()):
                    self.gcode.respond_info(
                        "ACE[%d]: Slot %d reports an injected sku %r that is not SM<n>/<n> - "
                        "treating the lane as untagged" % (self.instance_num, slot_idx, sku))
                    self._pending_rfid_queries.discard(slot_idx)
                    return
                self.gcode.respond_info(
                    "ACE[%d]: Slot %d carries a firmware-injected sku %s - binding from it "
                    "(backend authoritative for material and temperature)"
                    % (self.instance_num, slot_idx, _s))
                material = None
                brand = None
                have_temp_data = False   # R1: the temp-seed below cannot fire
                rfid_temp = 0
                hotbed_temp = None
                action = "native"        # fall through to the proven sku bind and its guards

            if action in ("uid", "bambu"):
                # 0x0201: result["sku"] IS the tag UID as hex; no usable material/temp. Route to
                # scenario-2 (UID) resolution and never seed the heater.
                self._consume_uid_tag(slot_idx, result)
                return

            if action != "native":
                # Any other version is a foreign POSITIONAL MISPARSE: every field is from the
                # wrong offsets. Trust NO field, set NO temp, and fetch the raw image so the tag
                # can be decoded by format (scenario 3 render / offline) instead.
                self.gcode.respond_info(
                    "ACE[%d]: Slot %d RFID version %s is not an Anycubic decode - trusting no "
                    "positional field, fetching its raw image"
                    % (self.instance_num, slot_idx, version))
                logging.warning("ace: slot %s foreign rfid version %r - positional fields "
                                "rejected, fetching raw image", slot_idx, version)
                self._pending_rfid_queries.discard(slot_idx)
                try:
                    self._fetch_raw_tag_image(slot_idx)
                except Exception:
                    logging.exception("ace: raw tag fetch failed for slot %s", slot_idx)
                return

            # version == 101 NATIVE decode: positional fields are trustworthy. The PLAUSIBILITY
            # GATE stays as a SECONDARY backstop - even a native decode can corrupt, and an
            # inverted hotbed range or an out-of-range nozzle temp still diverts to the raw-image
            # fetch rather than a bad heat target.
            _implausible = []
            try:
                _hb = hotbed_temp or {}
                _hb_min, _hb_max = _hb.get("min"), _hb.get("max")
                if (isinstance(_hb_min, int) and isinstance(_hb_max, int)
                        and _hb_min > _hb_max):
                    _implausible.append("hotbed min %d > max %d" % (_hb_min, _hb_max))
                if isinstance(rfid_temp, int) and not (0 <= rfid_temp <= 500):
                    _implausible.append("nozzle temp %d out of range" % rfid_temp)
                for _name, _val in (("sku", sku), ("brand", brand), ("material", material)):
                    if isinstance(_val, str) and ('"' in _val or "\n" in _val or "\r" in _val):
                        _implausible.append("%s contains quotes/control chars" % _name)
            except Exception:
                pass

            if _implausible:
                # FIRMWARE-INJECTED SKU (extract stub, V1.1.3Z+): a CLEAN "SM<n>" lands in an
                # otherwise-garbage OpenSpool record - version 101, trustworthy sku, but the
                # sibling temp/material/hotbed are OpenSpool bytes misread as Anycubic fields. That
                # is NOT a corrupt native decode: the sku is real and the backend is authoritative
                # for everything else. Detect a clean SM<n>/<n> sku; if so, trust ONLY the sku, drop
                # the garbage siblings (so the heater can NEVER be seeded from them), and fall
                # through to the normal sku bind. Otherwise reject to the raw-image render as before.
                _sku_s = (sku or "").strip()
                _sku_digits = _sku_s[2:] if _sku_s.upper().startswith("SM") else _sku_s
                _sku_clean = (1 <= len(_sku_digits) <= 7) and _sku_digits.isdigit()
                if _sku_clean:
                    self.gcode.respond_info(
                        "ACE[%d]: Slot %d version-101 record has a clean injected sku %r with "
                        "garbage sibling fields (%s) - trusting the sku only; backend authoritative "
                        "for temp/material." % (self.instance_num, slot_idx, sku,
                                                "; ".join(_implausible)))
                    material = None          # backend authoritative; never use garbage tag fields
                    brand = None
                    have_temp_data = False   # so the temp-seed below cannot set inv["temp"]=garbage
                    rfid_temp = 0
                    hotbed_temp = None
                else:
                    self.gcode.respond_info(
                        "ACE[%s]: Slot %s RFID decode REJECTED (%s) - a version-101 decode that "
                        "cannot be real. Treating the lane as untagged and fetching the raw image."
                        % (self.instance_num, slot_idx, "; ".join(_implausible)))
                    logging.warning(
                        "ace: slot %s implausible native RFID decode rejected (%s) raw sku=%r "
                        "brand=%r material=%r temp=%r hotbed=%r",
                        slot_idx, "; ".join(_implausible), sku, brand, material,
                        rfid_temp, hotbed_temp)
                    self._pending_rfid_queries.discard(slot_idx)
                    try:
                        self._fetch_raw_tag_image(slot_idx)
                    except Exception:
                        logging.exception("ace: raw tag fetch failed for slot %s", slot_idx)
                    return

            if 0 <= slot_idx < self.SLOT_COUNT:
                inv = self.inventory[slot_idx]

                # FIX 2: CROSS-LANE READ -> RE-READ AS THE SPOOL ROTATES. A native decode whose SKU
                # already belongs to ANOTHER lane is the neighbour's tag answering the shared coil,
                # not this lane's. Do NOT adopt it. Clear the rfid flag so the false->detected gate
                # reopens and the scanner re-reads on the next tick; during a load the lane's own
                # spool is turning, so its own tag arrives in the coil within a few ticks. Bounded:
                # a STATIONARY lane reads the same neighbour every time and, after a few tries,
                # gives up and stays unbound (the shim bind guard is the backstop) rather than
                # storming. No handoff happens on a rejected read, so this costs only cmd-68 reads.
                other = self._read_belongs_to_other_lane(slot_idx, sku)
                if other is not None:
                    n = self._rfid_xlane_retry.get(slot_idx, 0) + 1
                    self._rfid_xlane_retry[slot_idx] = n
                    self._pending_rfid_queries.discard(slot_idx)
                    if n <= self.RFID_XLANE_MAX_RETRY:
                        inv["rfid"] = False   # reopen the re-read gate
                        self.gcode.respond_info(
                            "ACE[%d]: Slot %d read SKU %s which is lane %d's - the shared coil "
                            "answered the neighbour. Re-reading as this lane's spool turns (%d/%d)."
                            % (self.instance_num, slot_idx, sku, other, n,
                               self.RFID_XLANE_MAX_RETRY))
                    else:
                        self.gcode.respond_info(
                            "ACE[%d]: Slot %d kept reading lane %d's tag (%s) after %d tries - its "
                            "own tag is not reaching the coil (stationary, or no own tag). Left "
                            "unbound; assign with MMU_GATE_MAP GATE=%d SPOOLID=<n> or re-load."
                            % (self.instance_num, slot_idx, other, sku, n, slot_idx))
                    return
                # A clean read of this lane's own tag - clear the retry streak.
                self._rfid_xlane_retry.pop(slot_idx, None)

                if material:
                    inv["material"] = material

                old_temp = inv.get("temp", 0)
                if have_temp_data and rfid_temp != old_temp:
                    inv["temp"] = rfid_temp
                elif not have_temp_data:
                    # Preserve existing temp (e.g. manually set via dashboard)
                    # instead of zeroing it out with no usable RFID data.
                    rfid_temp = old_temp

                if rfid_color:
                    inv["color"] = rfid_color

                if sku:
                    inv["sku"] = sku
                if brand:
                    inv["brand"] = brand
                if icon_type is not None:
                    inv["icon_type"] = icon_type
                # R1 CHOKE POINT. have_temp_data is what every carve-out clears when it
                # decides the positional fields cannot be trusted - so gate the RAW temperature
                # block on it too, not just the derived scalar. Three separate carve-outs each
                # nulled material/brand/rfid_temp/hotbed_temp and all three missed these,
                # which is the argument for gating in one place instead of remembering a list.
                if extruder_temp and have_temp_data:
                    inv["extruder_temp"] = extruder_temp
                if hotbed_temp:
                    inv["hotbed_temp"] = hotbed_temp
                if diameter is not None:
                    inv["diameter"] = diameter
                if total is not None:
                    inv["total"] = total
                if current is not None:
                    inv["current"] = current

                color_str = (
                    f"RGB({rfid_color[0]},{rfid_color[1]},{rfid_color[2]})"
                    if rfid_color else "none"
                )
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Slot {slot_idx} RFID full data -> "
                    f"sku={sku}, temp={rfid_temp}°C (min={temp_min}, max={temp_max}), "
                    f"color={color_str}, hotbed={hotbed_temp}, brand={brand}"
                )

                if self.manager:
                    self.manager._sync_inventory_to_persistent(self.instance_num, flush=False)

                # R1/R7 HANDOFF (scenario 1). A native decode's SKU is the spool number; hand the
                # record to the Moonraker resolver so it can bind a CONFIRMED spool. The local
                # temp/material set above stay (native is the one trusted layout); the resolver,
                # not this path, owns the spool-id binding decision (R8). native uses no UID (the
                # firmware read starts at page 4, past the UID pages), so no anticollision applies.
                native_record = {
                    "sku": sku or None,
                    "brand": brand or None,
                    "material": material or None,
                    "color": ("%02X%02X%02X" % (rfid_color[0], rfid_color[1], rfid_color[2]))
                    if rfid_color else None,
                    # Same choke point: never hand a consumer a temperature from a record
                    # whose positional fields we have already decided not to trust.
                    "temp_min": ((temp_min or None) if have_temp_data else None),
                    "temp_max": ((temp_max or None) if have_temp_data else None),
                    "bed_min": (hotbed_temp.get("min") or None) if hotbed_temp else None,
                    "bed_max": (hotbed_temp.get("max") or None) if hotbed_temp else None,
                    "diameter": (diameter or None),
                    "total_g": (total or None),
                    "format": "anycubic",
                }
                self._handoff_to_moonraker(slot_idx, native_record, uid="", native=True)
        else:
            msg = response.get("msg", "Unknown") if response else "No response"
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: get_filament_info failed for slot {slot_idx}: {msg}"
            )

    def _consume_uid_tag(self, slot_idx, result):
        """Scenario 2: a raw UID tag whose UID the firmware handed back as hex in result['sku'].

        No material or temperature is trusted.
        The UID is normalised, checked against the paired-antenna neighbour (R2), recorded for the
        neighbour's own future check, and handed to the Moonraker resolver for UID-based binding.
        Never seeds the heater. Never raises into Klipper.
        """
        try:
            self._pending_rfid_queries.discard(slot_idx)
            raw_uid = result.get("sku") or ""
            try:
                from . import ace_tag_formats as fmts
                uid = fmts.normalise_uid(raw_uid)
            except Exception:
                uid = _normalise_uid_local(raw_uid)
            if not uid:
                self.gcode.respond_info(
                    "ACE[%d]: Slot %d raw tag carried no readable UID - lane left untagged"
                    % (self.instance_num, slot_idx))
                return
            if not self._anticollision_clear(slot_idx, uid):
                return
            record = {
                "sku": None, "brand": None, "material": None, "color": None,
                "temp_min": None, "temp_max": None, "bed_min": None, "bed_max": None,
                "diameter": None, "total_g": None, "format": "uid",
            }
            self.gcode.respond_info(
                "ACE[%d]: Slot %d raw UID tag %s - resolving by UID (no temp/material trusted)"
                % (self.instance_num, slot_idx, uid))
            self._handoff_to_moonraker(slot_idx, record, uid=uid, native=False)
        except Exception:
            logging.exception("ace: raw uid consumption failed for slot %s", slot_idx)
            self._pending_rfid_queries.discard(slot_idx)

    _consume_bambu_uid_tag = _consume_uid_tag

    def _anticollision_clear(self, slot_idx, uid):
        """R2 paired-antenna guard. slots 0+1 and 2+3 share one reader (slot // 2). If the paired
        neighbour last presented the SAME normalised UID, this capture cannot be attributed to
        slot_idx (the shared coil read one tag twice) - refuse and say so. Records slot_idx's UID
        for the neighbour's check. Returns True when binding may proceed. Fails CLOSED.
        """
        try:
            neighbour = slot_idx ^ 1
            neigh_uid = self._slot_uid.get(neighbour, "")
            self._slot_uid[slot_idx] = uid
            if not anticollision_ok(uid, neigh_uid):
                reader = slot_idx // 2
                self.gcode.respond_info(
                    "ACE[%d]: Slot %d and its paired neighbour %d (reader %d) both read UID %s - "
                    "the shared antenna cannot say which lane holds the tag. Refusing to bind; "
                    "assign it explicitly with MMU_GATE_MAP GATE=%d SPOOLID=<n>."
                    % (self.instance_num, slot_idx, neighbour, reader, uid, slot_idx))
                logging.warning("ace: slot %s anticollision refusal - neighbour %s shares UID %s",
                                slot_idx, neighbour, uid)
                return False
            return True
        except Exception:
            logging.exception("ace: anticollision check failed for slot %s", slot_idx)
            return False

    def _handoff_to_moonraker(self, slot_idx, record, uid, native):
        """Fire-and-forget the parsed tag to Moonraker's `ace_resolve_tag` for identity
        resolution and binding (R7/R8). Moonraker owns the spool-id decision; this only reports.

        Mirrors the call_remote_method convention already used for `filaman_report_usage`. MUST
        NEVER raise into Klipper: a bare exception in a driver callback shuts the printer down
        (it has here before), so every failure is swallowed and logged, degrading to offline/none.
        """
        try:
            wire = build_wire_record(record, uid, native)
            self.printer.lookup_object("webhooks").call_remote_method(
                "ace_resolve_tag",
                instance=self.instance_num, slot=slot_idx, record=wire,
                uid=(uid or ""), native=bool(native))
            logging.info("ace: slot %s handed off to Moonraker resolver (native=%s uid=%r "
                         "sku=%r fmt=%s)", slot_idx, bool(native), uid or "",
                         wire.get("sku"), wire.get("format"))
        except Exception:
            logging.exception("ace: tag handoff to Moonraker failed for slot %s (identity falls "
                              "back to offline/none)", slot_idx)

    def handle_shared_bus_filament_info_response(self, response):
        """Replay a late shared-bus get_filament_info reply if the slot is still pending."""
        if not self.transport_spec.shared_bus:
            return False

        result = response.get("result") or {}
        slot_idx = result.get("index")
        if not isinstance(slot_idx, int) or slot_idx not in self._pending_rfid_queries:
            return False

        self._handle_rfid_info_response(slot_idx, response)
        return True

    def wait_ready(self, on_wait_cycle=None, timeout_s=60.0):
        """Wait for ACE unit to be ready with a hard timeout."""
        waited = 0.0
        interval = 0.5
        total_wait = 0.0

        while self._info.get("status") != "ready":
            self.reactor.pause(self.reactor.monotonic() + interval)
            waited += interval
            total_wait += interval

            if waited >= 25.0:
                self.send_high_prio_request(
                    request=self.protocol.build_get_status_request(),
                    callback=self._status_update_callback
                )
                waited = 0.0

            if on_wait_cycle is not None:
                on_wait_cycle()

            if total_wait >= timeout_s:
                raise TimeoutError(
                    f"ACE[{self.instance_num}]: wait_ready timed out after {timeout_s:.0f}s"
                )

    def is_ready(self):
        """Check if ACE is ready."""
        return self._info.get("status") == "ready"

    def _update_feed_assist(self, slot_index):
        """Update feed assist state: enable if slot >= 0, disable if -1."""
        if slot_index == -1:
            self._disable_feed_assist(slot_index)
        else:
            self._enable_feed_assist(slot_index)

    def _get_current_feed_assist_index(self):
        """Get the current feed assist slot index (-1 if disabled)."""
        return self._feed_assist_index

    def _get_current_rollback_assist_index(self):
        """Get the current rollback assist slot index (-1 if disabled)."""
        return self._rollback_assist_index

    def _is_slot_empty(self, slot_index):
        """Return True if ACE reports the given slot as empty."""
        for slot in self._info.get("slots", []):
            if slot.get("index") == slot_index:
                slot_status = normalize_ace_slot_state(slot.get("status"))
                return slot_status == AceSlotStateMachineState.EMPTY.value
        return False

    def _get_slot_feed_error(self, slot_index):
        """Return the firmware-reported slot error detail, or None.

        Both generations report a per-slot error state ("gear_err") when a
        feed cannot make progress; ACE2 additionally distinguishes the fault
        via status_detail (feed_error/rollback_error/assist_error/
        preload_error/stuck_error/tangled_error/motor_error).
        On ACE2 the firmware aborts a blocked feed by itself after ~18 s —
        polling this during sensor waits turns a minutes-long blind timeout
        into a fast, precise failure.
        """
        for slot in self._info.get("slots", []):
            if slot.get("index") == slot_index:
                status = normalize_ace_slot_state(slot.get("status"))
                if status == AceSlotStateMachineState.GEAR_ERR.value:
                    return slot.get("status_detail") or status
                return None
        return None

    def _is_printing_or_paused(self):
        """Check if printer is in a printing or paused state.

        Returns:
            bool: True if printing or paused, False otherwise
        """
        print_stats = self.printer.lookup_object("print_stats", None)
        if not print_stats:
            return False

        try:
            stats = print_stats.get_status(self.reactor.monotonic())
            state = (stats.get("state") or "").lower()
            return state in ["printing", "paused"]
        except Exception:
            return False

    def _clear_assist_claims_for_slot(self, slot):
        """Drop EVERY assist claim on `slot`, because ONE frame stops them all.

        D-B, 2026-09-01. build_stop_feed_assist_request and build_stop_rollback_assist_request
        are byte-identical - both emit STOP_FEED_OR_ROLLBACK {index} (protocol_ace2.py, where
        the second now literally delegates to the first). Mode 2 and mode 3 are two states of
        ONE device activity and opcode 9 ends whichever is running. The driver nevertheless
        tracked them as two independent flags, and each stop cleared only its own: sending the
        rollback stop on a lane where MODE 2 was live killed mode 2 on the wire and left
        _feed_assist_index still pointing at that slot.

        That stale claim is load-bearing downstream. postgear_seek.cfg gates its entire seek on
        `feed_slot == cur` with no device-status term, and _SEEK_POSTGEAR_FAILED calls
        ACE_DISABLE_ROLLBACK_ASSIST unconditionally on its way out - which is exactly this
        case, on a lane the fine phase had just re-armed to mode 2. The next seek then reads
        the stale flag as True and force-moves up to max_distance (200mm) FORWARD in 2mm steps
        against a lane the ACE is clamping.

        CHOSEN FIX: clear the state, do NOT refuse the send. Refusing to send whenever rollback
        is not tracked as armed would re-introduce D5 (2026-08-31): _rollback_assist_index
        resets to -1 on a klippy restart while the ACE - a separate, non-Klipper MCU - keeps
        assisting, so ACE_DISABLE_ROLLBACK_ASSIST has to stay able to fire blind. One extra
        stop frame is harmless; a remembered assist that is not running is not.
        """
        if slot is None or not isinstance(slot, int) or slot < 0:
            return
        if self._feed_assist_index == slot:
            self._feed_assist_index = -1
            self._feed_assist_topology_position = None
            self._feed_assist_ack_pending = False
            try:
                self.state.set(f"ace_feed_assist_index_{self.instance_num}", -1)
            except Exception:
                logging.exception("ace: could not clear persisted feed assist index")
        if self._rollback_assist_index == slot:
            self._rollback_assist_index = -1

    def _device_slot_detail(self, slot_index):
        """The device's own report for one slot from the last heartbeat, or None.

        This is the evidence the assist logic must resolve against. The driver's claim and the
        device's refusal can both be wrong about what is happening - bench-proven 2026-09-04:
        a second enable on an assisting slot is REFUSED (FORBIDDEN) while the encoder shows
        ~18mm/s of real feed. Only the slot's own status answers "is it assisting".
        """
        try:
            for _s in (self._info or {}).get("slots", []):
                if _s.get("index") == slot_index:
                    return _s.get("status_detail") or _s.get("status")
        except Exception:
            pass
        return None

    def _enable_feed_assist(self, slot_index):
        """Enable feed assist for smooth filament loading."""
        # Mode 2 is FORBIDDEN while the slot is rollback_assisting; the firmware wants an
        # explicit STOP between the two assists.
        if self._rollback_assist_index >= 0:
            self._stop_rollback_assist(self._rollback_assist_index)
        # Captured BEFORE the assignment below overwrites it: on ACE2 an assist that is
        # already running is itself what holds the device 'busy', so the leading
        # wait_ready() must not wait on it. See the guard below.
        _already_assisting = (self._feed_assist_index == slot_index)
        if _already_assisting and self._device_slot_detail(slot_index) in (
                "assisting", "shifting"):
            # Bench-proven 2026-09-04: re-sending the enable to a slot the device is already
            # assisting gets FORBIDDEN, and the refusal handler then dropped the claim -
            # leaving the driver reporting -1 while the device fed at ~18mm/s, with every stop
            # path gated on the claim it had just dropped. That is how GOOSE_PURGE killed the
            # 2026-09-03 print: its unconditional enable destroyed the working assist the
            # unpark had verified seconds earlier. The enable is already satisfied; there is
            # nothing to send and everything to lose by sending it.
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: feed assist already active on slot {slot_index} "
                f"- not re-sending the enable")
            return
        self._feed_assist_index = slot_index
        self._feed_assist_topology_position = self.serial_mgr.get_usb_topology_position()

        def callback(response):
            # D-C: the claim is acknowledged either way - accepted below, dropped in the else.
            self._feed_assist_ack_pending = False
            if response and response.get("code") == 0:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed assist enabled on slot {slot_index}"
                )
                self.state.set(
                    f"ace_feed_assist_index_{self.instance_num}",
                    slot_index
                )
            else:
                msg = response.get("msg", "Unknown") if response else ""
                # D6, 2026-08-31. The index is set optimistically BEFORE the send (ACE2 goes
                # 'busy' the instant assist starts, and the busy/deadlock logic below needs it
                # already set). Until today a device REJECTION only reached logging.warning, so
                # the optimistic value stood forever: status["feed_assist_slot"] reported an
                # assist the device had refused, and _ACE_REQUIRE_ASSIST - whose whole job is to
                # prove the ACE will follow the extruder - passed against a lane assisting
                # nothing. Roll the claim back here so the refusal is visible to the guard.
                #
                # NOT converted to the blocking accept-then-set loop that
                # _start_rollback_assist_verified uses: that shape needs send_request to invoke
                # its callback, and _enable_feed_assist has ~70 call sites across 9 test modules
                # that mock send_request as a bare Mock() which never calls back, so each would
                # burn the full response deadline. The residual window is the ~20ms to the ACK;
                # every guard now waits a full heartbeat (>=1.1s) first, so no guard reads it.
                # FORBIDDEN is ambiguous: "you may not assist" OR "I am ALREADY assisting"
                # (the device refuses a mode change while assisting). Dropping the claim on
                # the second kind is how the operator loses the stop path - every stop is
                # gated on this index, and _disable_feed_assist returns early when it reads
                # -1. Resolve against the device before believing the refusal.
                _dev = self._device_slot_detail(slot_index)
                if _dev in ("assisting", "shifting"):
                    self._feed_assist_index = slot_index
                    try:
                        self.state.set(
                            f"ace_feed_assist_index_{self.instance_num}", slot_index)
                    except Exception:
                        logging.exception("ace: could not persist feed assist index")
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: enable refused ({msg}) but the device "
                        f"shows slot {slot_index} '{_dev}' - the refusal means ALREADY "
                        f"assisting; keeping the claim so it can still be stopped")
                    return
                self._feed_assist_index = -1
                self._feed_assist_topology_position = None
                self._feed_assist_ack_pending = False
                try:
                    self.state.set(
                        f"ace_feed_assist_index_{self.instance_num}", -1
                    )
                except Exception:
                    logging.exception("ace: could not clear persisted feed assist index")
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed assist enable REFUSED by the device on "
                    f"slot {slot_index} ({msg}) - assist claim dropped, do NOT move the extruder"
                )
                logging.warning(
                    f"ACE[{self.instance_num}]: Feed assist enable failed: {msg}"
                )

        # Re-arming assist on a slot that is ALREADY assisting deadlocks on ACE2: the
        # device is busy because of that assist and cannot return to 'ready' until it is
        # stopped, so this wait burned its full 60s and raised (2026-08-26, twice, both
        # during _BRUSH_PRIME). The trailing wait_ready() below was guarded for precisely
        # this reason; this one was missed. Every other path still waits.
        if not (_already_assisting and self.protocol.feed_assist_causes_busy()):
            self.wait_ready()
        request = self.protocol.build_start_feed_assist_request(slot_index)
        # D-C, 2026-09-01. Set immediately before the send and cleared by either branch of the
        # callback. See the note in __init__ and get_status(): for the ~20ms until the ACK,
        # status["feed_assist_slot"] reports -1 rather than an assist nothing has confirmed.
        self._feed_assist_ack_pending = True
        self.send_request(request, callback)
        # ACE1: stays 'ready' during feed assist, so wait confirms the command was processed.
        # ACE2: transitions to 'busy' the moment feed assist starts and never returns to
        # 'ready' until STOP_FEED_ASSIST is received.  Calling wait_ready() here on ACE2
        # would block forever.
        if not self.protocol.feed_assist_causes_busy():
            self.wait_ready()

    def _disable_feed_assist(self, slot_index):
        """Disable feed assist."""
        if slot_index < 0:
            # Feed assist is never active on a negative slot index; nothing to disable.
            # This guards against callers that pass _feed_assist_index directly when
            # feed assist was already off (-1), which would otherwise bypass the check
            # below and send a spurious STOP_FEED_OR_ROLLBACK command.
            return
        if self._feed_assist_index != slot_index:
            logging.warning(
                f"ACE[{self.instance_num}]: Feed assist not active on slot {slot_index}"
            )
            return

        # D-B, 2026-09-01. Was two manual assignments plus a persist inside the success
        # callback. Routed through the shared helper so that the ONE frame this is about to
        # send drops every claim it actually ends, and so the persisted
        # ace_feed_assist_index_<n> is written at the same instant as the in-memory index
        # rather than only when the device answers - the in-memory clear was already
        # unconditional, so a persist that waited for the ACK could disagree with it forever.
        self._clear_assist_claims_for_slot(slot_index)

        def callback(response):
            if response and response.get("code") == 0:
                logging.info(
                    f"ACE[{self.instance_num}]: Feed assist disabled for slot {slot_index}"
                )
            else:
                msg = response.get("msg", "Unknown") if response else ""
                logging.warning(
                    f"ACE[{self.instance_num}]: Feed assist disable failed: {msg}"
                )

        # ACE1: stays 'ready' during feed assist, so this pre-send wait guards against
        # sending STOP while the device is processing something else.
        # ACE2: is 'busy' *because* feed assist is active.  The only way to make it ready
        # again is to send STOP_FEED_ASSIST, so waiting for 'ready' first is a deadlock.
        if not self.protocol.feed_assist_causes_busy():
            self.wait_ready()
        request = self.protocol.build_stop_feed_assist_request(slot_index)
        self.send_request(request, callback)
        self.dwell(1.0)
        # ACE2: the device only leaves 'busy' once it has processed STOP_FEED_ASSIST,
        # and the cached status is refreshed via the 1 Hz heartbeat.  If a heartbeat
        # times out around print-end, wait_ready() can stall for up to 60s before
        # giving up
        if not self.protocol.feed_assist_causes_busy():
            self.wait_ready()

    def _ensure_assists_off_for_motion(self, slot, action, keep_rollback_slot=-1):
        """Disable any active feed OR rollback assist before a FEED/RETRACT command.

        ACE2 stays 'busy' the whole time feed assist is active and silently
        ignores feed/retract commands for ANY slot on the same device
        (recovery cycling does nothing while assist is active on another
        slot, and wait_ready() stalls too).
        Callers historically disabled assist only for the slot they were
        about to move, which no-ops in _disable_feed_assist when the assist
        is on a different slot. This guard runs in every motion primitive
        so no call path can send motion into a device blocked by assist.
        Mode 3 blocks the same way and additionally OPPOSES a forward feed,
        so a live rollback assist is cleared here too.

        keep_rollback_slot spares a rollback assist on that slot; only
        _start_rollback_assist_verified passes it, so the guard can never
        stop the assist it is about to start.
        """
        active = self._feed_assist_index
        if active >= 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed assist active on slot {active} - "
                f"disabling before {action} on slot {slot}"
            )
            self._disable_feed_assist(active)
        rollback = self._rollback_assist_index
        if rollback >= 0 and rollback != keep_rollback_slot:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Rollback assist active on slot {rollback} - "
                f"disabling before {action} on slot {slot}"
            )
            self._stop_rollback_assist(rollback)

    def _feed(self, slot, length, speed, callback=None):
        """Feed filament from slot."""
        self._ensure_assists_off_for_motion(slot, "feed")
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: _feed() -> slot={slot}, "
            f"length={length}mm, speed={speed}mm/s"
        )

        if callback is None:
            def callback(response):
                if response and response.get("msg") == "FORBIDDEN":
                    msg = f"ACE[{self.instance_num}]: Feed forbidden"
                    self.gcode.respond_info(f"{msg}: {response}")
                elif response and response.get("code", 0) != 0:
                    msg = f"ACE[{self.instance_num}]: Feed error: {response.get('msg')}"
                    self.gcode.respond_info(msg)

        request = self.protocol.build_feed_filament_request(slot, length, speed)
        self.gcode.respond_info(f"ACE[{self.instance_num}]: Sending request: {request}")
        self.send_request(request, callback)

    def _stop_feed(self, slot):
        """Stop feeding filament."""
        def callback(response):
            if response and response.get("code") != 0:
                msg = response.get("msg", "Unknown error")
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop feed error: {msg}"
                )
            elif response:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop feed successful: {response}"
                )

        request = self.protocol.build_stop_feed_filament_request(slot)
        self.send_high_prio_request(request, callback)

    def _retract_async(self, slot, length, speed):
        """Fire-and-forget retract: send the device command and return immediately, so
        gcode issued right after (a FORCE_MOVE) runs WHILE the ACE winds. The blocking
        _retract serialised the park's 'simultaneous' pull into one end after the other
        (observed 04:53-04:54 on 2026-08-23); both ends must genuinely move together.
        """
        self._ensure_assists_off_for_motion(slot, "retract")
        if self._is_slot_empty(slot):
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Async retract skipped - slot {slot} is empty"
            )
            return

        def callback(response):
            if response and (response.get("code", 0) != 0
                             or response.get("msg") == "FORBIDDEN"):
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Async retract rejected: "
                    f"{response.get('msg', 'no message')}"
                )

        request = self.protocol.build_unwind_filament_request(slot, length, speed)
        self.send_request(request, callback)

    # After any STOP_FEED_OR_ROLLBACK the device refuses further motion for a while. All
    # three 'stop' builders emit that identical opcode-9 frame - stopping assist, stopping a
    # feed and stopping an unwind are the SAME command on the wire - so any of them arms it.
    # The refusal has been OBSERVED to last tens of seconds. Its mechanism is NOT established:
    # a device-side timer, a second operation channel and a STOP setup race have all been
    # proposed and none of them is verified, so nothing here asserts one. The verified
    # starters below retry on FORBIDDEN, which makes the attempt budget the effective timeout.
    #
    # This is an ATTEMPT COUNT and each attempt costs a response deadline PLUS a pause.
    # 35 attempts was a ~140s budget, not the ~35s it reads as - mid-toolchange, 140s at
    # temperature with static filament in the melt zone, a clog/heat-soak hazard.
    #
    # D7, 2026-08-31. The old arithmetic above ("9 bounds the worst case at ~36s") only held
    # for the NO-RESPONSE case, where each attempt really does burn the full 3.0s deadline.
    # The case this budget actually exists for is the opposite one: inside the refuse window
    # the device answers FORBIDDEN promptly (~20ms), so an attempt cost ~1.02s and 9 attempts
    # spanned only ~9s - SHORTER than the 20-30s window it was sized to ride out. The starter
    # then raised on a refusal that was about to clear on its own.
    #
    # Fixed by making the pause depend on WHY the attempt failed (see STOP_SETTLE_*_PAUSE_S):
    #   FORBIDDEN / device error : ~0.02s + 3.0s pause  -> 12 attempts ~= 36s, spans the window
    #   no response at all       : 3.0s deadline + 0.5s -> 12 attempts ~= 42s, comms fault
    # Both still bound time-at-temperature to well under the old 140s.
    # Costs nothing in the normal case - acceptance returns on the first attempt.
    STOP_SETTLE_ATTEMPTS = 12
    # A FORBIDDEN means the device is inside its post-STOP refuse window; the only useful
    # response is to wait a meaningful slice of that window before asking again.
    STOP_SETTLE_REFUSED_PAUSE_S = 3.0
    # No response is a comms problem, and the 3.0s deadline has already elapsed.
    STOP_SETTLE_SILENT_PAUSE_S = 0.5

    def _retract_async_verified(self, slot, length, speed, max_attempts=None,
                                refused_pause=None, quiet=False):
        """Start an async retract and return only once the device has ACCEPTED it.

        _retract_async fire-and-forgets: a FORBIDDEN lands in a log callback and the caller
        never knows. For a tandem pull that is fatal - the extruder then pulls alone against
        a clamped lane (2026-08-27: 140mm of exactly that killed a toolchange mid-print).
        This uses the blocking _retract's own acceptance machinery, but returns as soon as
        the device says yes, so the pull itself still runs concurrently with the extruder.
        Raises instead of returning when the device keeps refusing - the caller must NOT
        move the extruder in that case.
        """
        if max_attempts is None:
            max_attempts = self.STOP_SETTLE_ATTEMPTS
        self._ensure_assists_off_for_motion(slot, "retract")
        if self._is_slot_empty(slot):
            raise ValueError(
                f"ACE[{self.instance_num}]: tandem retract refused - slot {slot} is empty")
        request = self.protocol.build_unwind_filament_request(slot, length, speed)
        why = "unknown"
        for attempt in range(1, max_attempts + 1):
            rc = {"response": None, "done": False}

            def callback(response, rc=rc):
                rc["response"] = response
                rc["done"] = True

            self.send_request(request, callback)
            deadline = time.time() + 3.0
            while not rc["done"] and time.time() < deadline:
                self.reactor.pause(self.reactor.monotonic() + 0.1)
            resp = rc["response"]
            if resp and resp.get("code", 0) == 0 and resp.get("msg") != "FORBIDDEN":
                return
            why = "no response" if not resp else resp.get("msg", "error")
            msg = (f"ACE[{self.instance_num}]: tandem retract not accepted "
                   f"(attempt {attempt}/{max_attempts}: {why}) - extruder held still")
            if quiet:
                # A caller doing many small retracts of its own (identify) produces one of
                # these per chunk. They are expected, they name an extruder that is not
                # involved, and 31 of them per run buries the one line that matters.
                logging.info("ace: %s", msg)
            else:
                self.gcode.respond_info(msg)
            # See STOP_SETTLE_ATTEMPTS: a prompt FORBIDDEN needs the long pause, silence
            # does not (its deadline already elapsed). But the long pause is sized for the
            # POST-STOP refuse window - a caller that never issues a STOP has no such window
            # and should not pay 3s per refusal for a hazard it cannot have.
            _refused = (self.STOP_SETTLE_REFUSED_PAUSE_S if refused_pause is None
                        else refused_pause)
            self.reactor.pause(self.reactor.monotonic() + (
                self.STOP_SETTLE_SILENT_PAUSE_S if resp is None else _refused))
        raise ValueError(
            f"ACE[{self.instance_num}]: device refused the tandem retract {max_attempts}x "
            f"({why}) - aborting BEFORE the extruder pulls against a clamped lane")

    def _start_rollback_assist_verified(self, slot, length=3000., speed=40.,
                                        max_attempts=None):
        """Start mode-3 ROLLBACK ASSIST and return only once the device has ACCEPTED it.

        Same contract as _retract_async_verified, for the same reason: a FORBIDDEN that lands in
        a log callback is invisible to the caller, and the caller would then move the extruder
        against a lane that is not participating. Raises rather than returning on refusal.

        Mode 3 differs from a mode-1 unwind in the way that matters here: the ACE decides when to
        move from BUFFER TENSION rather than running a commanded distance, so the extruder sets
        the pace and the two ends cannot drift out of sync. That hand-synchronisation is the
        entire failure mode of a driven tandem pull. The firmware clamps the assist to ~50 mm/s
        internally, so extruder retraction must stay below that or the extruder outruns the ACE.
        """
        if max_attempts is None:
            max_attempts = self.STOP_SETTLE_ATTEMPTS
        self._ensure_assists_off_for_motion(slot, "rollback assist",
                                            keep_rollback_slot=slot)
        if self._is_slot_empty(slot):
            raise ValueError(
                "ACE[%d]: rollback assist refused - slot %d is empty"
                % (self.instance_num, slot))
        request = self.protocol.build_start_rollback_assist_request(slot, length, speed)
        why = "unknown"
        for attempt in range(1, max_attempts + 1):
            rc = {"response": None, "done": False}

            def callback(response, rc=rc):
                rc["response"] = response
                rc["done"] = True

            self.send_request(request, callback)
            deadline = time.time() + 3.0
            while not rc["done"] and time.time() < deadline:
                self.reactor.pause(self.reactor.monotonic() + 0.1)
            resp = rc["response"]
            if resp and resp.get("code", 0) == 0 and resp.get("msg") != "FORBIDDEN":
                # Only now is mode 3 live; the macro layer gates its retracts on this.
                self._rollback_assist_index = slot
                return
            why = "no response" if not resp else resp.get("msg", "error")
            self.gcode.respond_info(
                "ACE[%d]: rollback assist not accepted (attempt %d/%d: %s) - extruder held still"
                % (self.instance_num, attempt, max_attempts, why))
            # See STOP_SETTLE_ATTEMPTS: a prompt FORBIDDEN needs the long pause, silence
            # does not (its deadline already elapsed).
            self.reactor.pause(self.reactor.monotonic() + (
                self.STOP_SETTLE_SILENT_PAUSE_S if resp is None
                else self.STOP_SETTLE_REFUSED_PAUSE_S))
        raise ValueError(
            "ACE[%d]: device refused rollback assist %dx (%s) - aborting BEFORE the extruder "
            "pulls against a lane that is not assisting" % (self.instance_num, max_attempts, why))

    def _stop_rollback_assist(self, slot):
        """End mode-3 assist. Uses the shared STOP; callers must respect the ~20-30s window in
        which the device rejects further motion after a STOP_FEED_OR_ROLLBACK."""
        if slot < 0:
            return
        # D5, 2026-08-31. This used to return WITHOUT SENDING whenever the tracked index did not
        # match. _rollback_assist_index initialises to -1 (see __init__) and the ACE is a
        # separate, non-Klipper MCU that keeps assisting across a klippy restart - so after a
        # FIRMWARE_RESTART the driver believes mode 3 is off while the device is still reeling
        # backward, and ACE_DISABLE_ROLLBACK_ASSIST - the exact command every rollback error
        # message tells the operator to run - was a silent no-op.
        #
        # CHOSEN FIX: always send when the tracked index is UNKNOWN (-1), rather than persisting
        # the index the way feed assist does in ace_feed_assist_index_<n>. Persist-and-restore is
        # right for mode 2 because a dropped forward assist is a loss worth repairing; mode 3 is
        # the opposite. We never want to RE-ARM a backward assist unattended after a restart - we
        # only ever want a guarantee that it is OFF. So the fail-safe here is an extra, harmless
        # stop frame, not remembered state that could resurrect reverse motion on a strand
        # threaded through a hot nozzle. A stop on a slot that was not assisting is accepted by
        # the device and costs only the settle dwell below.
        #
        # A tracked index pointing at a DIFFERENT slot is still a real mismatch: that assist is
        # someone else's and stopping it here would be the bug this guard exists to prevent.
        if self._rollback_assist_index >= 0 and self._rollback_assist_index != slot:
            logging.warning(
                "ACE[%d]: Rollback assist tracked on slot %d, not %d - not stopping"
                % (self.instance_num, self._rollback_assist_index, slot))
            return
        if self._rollback_assist_index < 0:
            logging.info(
                "ACE[%d]: Rollback assist state unknown (index -1, e.g. after a klippy "
                "restart) - sending the stop for slot %d anyway"
                % (self.instance_num, slot))
        # Cleared before the send: a STOP that throws still leaves us no claim on the slot.
        #
        # D-B, 2026-09-01. This used to clear ONLY _rollback_assist_index. The frame below is
        # the same opcode-9 STOP_FEED_OR_ROLLBACK that stops mode 2, so on a lane where mode 2
        # was live this stopped the forward assist on the wire while leaving
        # _feed_assist_index - and therefore status["feed_assist_slot"] - claiming it was still
        # running. _SEEK_POSTGEAR_FAILED reaches here with exactly that state. See
        # _clear_assist_claims_for_slot for the full trace and why clearing beats refusing.
        self._clear_assist_claims_for_slot(slot)
        try:
            request = self.protocol.build_stop_rollback_assist_request(slot)
            self.send_request(request, lambda response: None)
            # Same settle as _disable_feed_assist - the device needs it before the next command.
            self.dwell(1.0)
        except Exception:
            logging.exception("ace: stop rollback assist failed")

    # JAM CAP for the tandem pull, re-derived from the toolhead geometry 2026-08-30.
    # entry->postgear is 20mm and both tip-forming paths leave the tip on or just below the
    # post-gear switch (CROSSBOW parks the cut face on it via crossbow_postgear_to_blade;
    # _TIP_SHAPING's net -48mm leaves it 3.7mm below, postgear->nozzle being 51.7mm), so a
    # park-to-entry-clear pull is ~24mm. 48mm is 2x that.
    # The old 140 was sized on 2026-08-21 for the alternating-chunk extraction this function
    # REPLACED and was never re-derived; with a stall guard that could not fire (see below)
    # it authorised ~116mm of unwitnessed grinding.
    # Jam cap, NOT a target - the pull is sensor-terminated when toolhead_entry clears.
    #
    # 48 was 2x a 24mm figure derived from ace_park_to_postgear (1510) - ace_park_to_entry (1490).
    # Those are two ACE-FED distances measured ~900mm away through a compliant bowden; their
    # difference is not the toolhead geometry and never was.
    #
    # The toolhead itself says otherwise. A single-attempt pull on 2026-09-01 00:10 logged:
    #     entry cleared after 52mm of tandem pull (cap 48mm, post-gear cleared at 8mm)
    # post-gear released at 8mm and entry released between 44 and 48mm, so post-gear -> entry is
    # ~40mm. Every two-press pair on record sums to 68-76mm (48+20, 48+24, 56+20, 48+28).
    #
    # So the required pull sits AT the cap, and a few mm of run-to-run variation decides pass/fail.
    # That is the two-press unload: fresh filament, any lane, no regression anywhere. It predates
    # all of this week's work - klippy.log.2026-08-31:185585 records the identical failure at 14:02
    # on 08-31, hours before the mode-3 commit and a day before any of these edits.
    #
    # Sizing to the worst case costs a healthy pull nothing, because it terminates on the switch: a
    # pull that only needs 28mm still ends at 28mm. The real jam guard is the post-gear witness
    # (POSTGEAR_CLEAR_MM), which aborts at 16mm when the strand never moves at all.
    TANDEM_CAP_MM = 100.0

    def _tandem_extract(self, slot, speed, cap_mm=None, allow_unowned=False):
        """Reverse of the load crossing: both ends pull at the same speed only until
        the entry sensor CLEARS -- past that point the extruder no longer touches the
        filament, and the long pull home belongs to the ACE alone. Sensor-terminated
        because the required distance varies with where the tip parked; cap_mm is the
        jam guard, not the target.

        STALL CHECKS MUST MEASURE EXECUTED MOTION, NOT COMMANDED MOTION (fixed 2026-08-30).
        run_script_from_command("FORCE_MOVE ...") does NOT block: force_move.manual_move only
        calls toolhead.dwell(), which advances print_time and returns. `pulled` therefore counts
        millimetres QUEUED, and the old code reached its 16mm threshold in a few tens of
        milliseconds of wall time -- while the extruder had physically moved ZERO. It then read
        the hub encoder, correctly saw 0 pulses, and declared "strand NOT moving".

        That is what failed the 2026-08-30 toolchange: exactly four FORCE_MOVE segments inside a
        single 1s stats interval, then the raise, on filament that was very probably fine. Runs
        that "passed" earlier only passed because the ACE's own unwind happened to have a head
        start from _retract_async_verified's 0.1s polling, so a few pulses had accumulated. The
        guard was measuring ACE acknowledgement latency, not filament movement.

        The fix is to flush the motion queue before every measurement. Segments are still
        queued back-to-back so the pull stays smooth; only the CHECK waits for the machine to
        catch up. The check is also now continuous and incremental rather than one-shot.

        AND IT MUST MEASURE THE STRAND, NOT THE ACE (fixed 2026-08-30). The hub encoder sits at
        the hub and turns whenever the ACE turns - and the line below has the ACE unwinding the
        whole cap under its own power for the entire pull - so a strand severed, buckled or
        pinned at the toolhead NIP keeps ticking it while nothing moves at the toolhead. Letting
        encoder pulses satisfy the check made this guard weaker than the commanded-distance
        version it replaced: every window passed and the loop ran to cap_mm. toolhead_postgear is
        the only sensor downstream of the extruder nip, so its transition is the only evidence
        available here that the STRAND moved. The encoder is kept for the message only.
        """

        # OWNERSHIP, ENFORCED HERE BECAUSE HERE IS WHERE THE EXTRUDER MOVES.
        #
        # This is a TANDEM pull: the ACE unwinds `slot` AND the extruder force-moves backward.
        # The extruder half does not know which lane was named - it pulls whatever strand is in
        # the nip, which belongs to the LOADED lane. Running this on a lane that does not own the
        # nip therefore unwinds one lane while grinding another's strand.
        #
        # On 2026-09-03 an extract ran on slot 2 with ace_current_index=1: T1's strand moved 12mm,
        # stopped, and 76mm of extruder pull continued against it. Until now the ONLY thing
        # standing between a typed command and that outcome was a single `cur != t` condition in
        # ACE_LANE_EJECT - which left ACE_LANE_PARK and ACE_COLD_RETRACT completely unguarded,
        # since both reach this primitive by their own routes.
        #
        # Fail CLOSED, same rule and same reasoning as the identify jog guard below: an unreadable
        # index means we cannot prove ownership, and "probably the right lane" is not good enough
        # to drive the extruder backward on. allow_unowned is the operator's explicit assertion
        # (ACE_TANDEM_EXTRACT FORCE=1), for the genuine recovery where the index went stale but
        # the strand really is this lane's.
        if not allow_unowned:
            _mgr = getattr(self, "manager", None)
            try:
                _cur = int(_mgr.state.get("ace_current_index", -1))
            except Exception:
                raise self.printer.command_error(
                    "ACE[%d]: cannot read the loaded-tool index - refusing a tandem extract on "
                    "slot %d. The extruder half of this pull moves whatever is in the nip."
                    % (self.instance_num, slot))
            # ace_current_index is a GLOBAL tool number; `slot` is this instance's LOCAL index.
            # They coincide only on instance 0, so compare like for like or a second ACE turns
            # this guard into its own opposite - refusing a legitimate park and permitting a
            # cross-lane one.
            _tool = slot + (getattr(self, "tool_offset", 0) or 0)
            # DELIBERATELY NOT GATED ON ace_target_index. An in-flight toolchange is exactly when
            # ACE_LANE_PARK must work: perform_tool_change sets target_index as its FIRST
            # statement and current_index only flips at load END, so `target != current` is true
            # for the whole of every normal change. Refusing there aborted every toolchange on the
            # machine - after CROSSBOW_CUT_TIP had already severed the tip, leaving a cut strand
            # through the nip with the nozzle hot. During a change the strand in the nip belongs
            # to current_index, which is precisely the lane park extracts, so ownership is the
            # only question and `_tool == _cur` is the whole of it.
            if _cur < 0:
                raise self.printer.command_error(
                    "ACE[%d]: no lane is recorded as loaded, so whose strand is in the nip is "
                    "unknown - refusing a tandem extract on slot %d. Confirm it is this lane's, "
                    "then ACE_TANDEM_EXTRACT T=%d FORCE=1."
                    % (self.instance_num, slot, _tool))
            if _cur != _tool:
                raise self.printer.command_error(
                    "ACE[%d]: T%d owns the toolhead, not T%d - refusing a tandem extract. The "
                    "extruder would pull T%d's strand while the ACE unwinds T%d. To move T%d "
                    "alone use ACE_RETRACT (ACE-only, the extruder is not touched)."
                    % (self.instance_num, _cur, _tool, _cur, _tool, _tool))
        if cap_mm is None:
            cap_mm = self.TANDEM_CAP_MM
        if cap_mm > 2. * self.TANDEM_CAP_MM:
            self.gcode.respond_info(
                "ACE[%d]: tandem cap %.0fmm is far above the %.0fmm the toolhead geometry "
                "needs - it is a jam guard, not a target, and every millimetre above the "
                "geometry is grinding this cannot stop"
                % (self.instance_num, cap_mm, self.TANDEM_CAP_MM))
        # Accept-verified: raises before any extruder motion if the device refuses.
        # A PARKED LANE ALWAYS HAS FEED ASSIST ON, so the first retract after a park is
        # always issued into the ACE2's busy window - where it is acknowledged and IGNORED.
        #
        # The load arms it by design ("post-gear triggered after 24mm of crossing - engaging
        # assist"), and it stays armed through the park. The unload disables it and commands the
        # retract immediately afterwards. An ACE2 is busy for as long as assist is active and
        # silently ignores feed/retract for EVERY slot until STOP_FEED_ASSIST has taken effect.
        #
        # That is why a parked unload was perfectly reproducibly a TWO-PRESS operation: the
        # first press was spent inside that window with its retract discarded and the extruder
        # pulling alone against a clamped lane (measured: enc +0 for the full 48mm), and the
        # second press found assist already off and completed in 20-28mm.
        #
        # WAIT FOR A FRAME THAT POST-DATES THIS CALL, NOT FOR A DURATION.
        #
        # WHAT THIS IS: a tightened precondition and the machine's only freshness
        # instrumentation. WHAT IT IS NOT: the fix for the stalled tandem pull. An earlier
        # version of this comment claimed it was, and the logs refute that - the corrected
        # account is below, because a wrong causal story in the busiest primitive here will
        # mislead whoever reads it next.
        #
        # The old form was `pause(1.2)` then poll `_info["status"]`, resting on its own stated
        # assumption: "One heartbeat is 1.0s, so waiting past that guarantees any status we then
        # see is fresh." That is a sleep standing in for a check. _info is whole-object-replaced
        # by the heartbeat and carried no timestamp, so freshness was unverifiable. _info_at makes
        # it answerable, and requiring a frame stamped after _t0 means the 'ready' we act on
        # cannot predate this call. It is also typically FASTER: _t0 lands uniformly inside the
        # 1.0s heartbeat period, so the expected wait is ~0.55s against a flat 1.2s.
        #
        # WHY IT DOES NOT FIX THE DISCARDED RETRACT, which is the failure people will come here
        # looking for. ace2-protocol-findings.md 8.3: after STOP_FEED_OR_ROLLBACK the device keeps
        # answering normally, but feed/retract issued in the next ~20-30s SILENTLY DO NOTHING. The
        # admission gate is a context word at 0x0800B474, not the work-state field GET_STATUS
        # reports - and a STOP on an assisting slot writes state 0 and returns without stopping the
        # motor. So the device reports 'ready' promptly and honestly for the whole window in which
        # motion is discarded. The status was already fresh and already 'ready'; requiring
        # freshness changes nothing about whether the next retract will be honoured.
        #
        # Nor is the stalled pull well correlated with this settle. The line it logs precedes
        # EVERY tandem pull, successful ones included (klippy.log.2026-09-03:216971 settles
        # 'ready' and then clears entry in 44mm). And in the recorded failures no stop was sent in
        # this call at all - they are preceded by "Feed assist not active on slot N", the
        # early-return branch of _disable_feed_assist that sends nothing and dwells nothing. The
        # device really was ready. The stop that armed the poisoning window was the _stop_retract
        # from the PREVIOUS failed attempt, and neither 1.2s nor 3.2s covers 20-30s.
        #
        # There is also a stop this settle cannot cover by construction:
        # _retract_async_verified opens with _ensure_assists_off_for_motion, which may issue a
        # fresh STOP and then send the unwind on the next statement, long after this returns.
        #
        # Still bounded and never fatal. If no fresh frame arrives we proceed exactly as before,
        # so a stalled heartbeat degrades to the old behaviour rather than hanging. The age is
        # logged either way: "frame FRESH, 0.5s old" on a pull that still comes back enc +0 is
        # what retires the stale-read hypothesis for good, and nothing recorded that until now.
        if getattr(self.protocol, "feed_assist_causes_busy", lambda: False)():
            _t0 = self.reactor.monotonic()
            _deadline = _t0 + 3.2
            while self.reactor.monotonic() < _deadline:
                if (getattr(self, "_info_at", 0.0) > _t0
                        and self._info.get("status") == "ready"):
                    break
                self.reactor.pause(self.reactor.monotonic() + 0.1)
            # Guarded: _info_at is 0.0 until the first heartbeat lands, and monotonic() is
            # system uptime, so the unguarded form printed "348922.0s old" and reads as a broken
            # clock rather than as "no frame yet".
            _stamp = getattr(self, "_info_at", 0.0)
            _age = (self.reactor.monotonic() - _stamp) if _stamp > 0.0 else None
            _fresh = getattr(self, "_info_at", 0.0) > _t0
            self.gcode.respond_info(
                "ACE[%d]: settled for the assist stop before retracting (device now '%s', "
                "frame %s, %s, waited %.1fs)"
                % (self.instance_num, self._info.get("status"),
                   "FRESH" if _fresh else "STALE - proceeding anyway",
                   ("%.1fs old" % _age) if _age is not None else "none received yet",
                   self.reactor.monotonic() - _t0))

        self._retract_async_verified(slot, cap_mm, speed)
        toolhead = self.printer.lookup_object("toolhead")
        pulled = 0.0
        seg = 4.0
        # C27: the hub encoder witnesses extruder-driven pulls. If the strand has not moved
        # after a check window, every further segment is a grind - stop there instead of at the
        # cap (2026-08-28: three 140mm grinds on a notch sitting in the nip).
        enc = self.printer.lookup_object("ace_hub_encoder", None)

        def _enc_pulses():
            try:
                st = enc.get_status(self.reactor.monotonic())
                return int(st.get("pulses", 0)) if st.get("hooked", True) else None
            except Exception:
                return None

        def _seat():
            # Deliberately NOT _seat_sensor_triggered(): that returns True when the sensor is
            # absent, which would arm the deadline below on a machine that has no post-gear
            # switch and abort every pull at POSTGEAR_CLEAR_MM. Absent must read as "no
            # evidence", not as "filament".
            if not self.seat_verify_sensor:
                return None
            sensor = self.printer.lookup_object(
                "filament_switch_sensor %s" % self.seat_verify_sensor, None)
            if sensor is None:
                return None
            try:
                return bool(sensor.get_status(
                    self.reactor.monotonic()).get("filament_detected"))
            except Exception:
                return None

        # Baseline is taken with the queue already drained by _retract_async_verified.
        last_enc = _enc_pulses()
        start_seat = _seat()
        postgear_cleared_at = None
        last_checked_at = 0.0
        # How often the queue is drained and the switches re-read. Smaller than the old 16mm so
        # the entry-switch read below is never more than this far behind the real position.
        STALL_WINDOW_MM = 8.0
        # If post-gear read FILAMENT when the pull started, it must read CLEAR within this much
        # EXECUTED pull. Both tip-forming paths park within ~4mm of the switch, so this is >4x
        # the worst real park - deliberately loose, because the switch's own release hysteresis
        # has not been measured and a spurious abort mid-print is expensive. Falls on the second
        # STALL_WINDOW_MM boundary, so the abort is deterministic at 16mm.
        POSTGEAR_CLEAR_MM = 16.0
        if start_seat is not True:
            # Say it out loud: with no post-gear witness the cap is the ONLY thing bounding a
            # grind on this pull.
            self.gcode.respond_info(
                "ACE[%d]: post-gear reads %s at the start of the tandem pull - nothing "
                "downstream of the nip can witness the strand, so the %.0fmm cap is the only "
                "guard" % (self.instance_num,
                           "clear" if start_seat is False else "unavailable", cap_mm))

        while self.manager.get_switch_state(SENSOR_TOOLHEAD):
            err = self._confirmed_slot_error(slot)
            if err is not None:
                toolhead.wait_moves()
                self._stop_retract(slot)
                raise ValueError(
                    "ACE[%d]: device reports '%s' on slot %d during the tandem pull - its motor "
                    "is stopped. Pulling harder from the extruder alone would grind the filament."
                    % (self.instance_num, err, slot))
            if pulled >= cap_mm:
                # LOOK AGAIN AFTER THE DRAIN. `pulled` counts millimetres QUEUED, not executed -
                # the whole docstring above is about that - so reaching the cap only means the
                # loop outran the machine. wait_moves() is where the motion actually happens, and
                # the old code raised immediately after it without re-reading the switch. A pull
                # that physically completed during that drain was reported as "filament is not
                # moving back".
                #
                # 2026-09-01: this cost an entire evening. Extractions from a parked tip took TWO
                # attempts - the first did the work and raised anyway, the second found entry
                # already clear and "succeeded". No grinding at any point, which is what ruled out
                # slip: the filament was moving exactly as commanded, and only the verdict was
                # wrong. Same class of bug the docstring says it fixed for the STALL check; the
                # cap check was left reading commanded distance.
                #
                # Simon's observation is what closed it: a cut is always followed by a park to
                # post-gear, so a cut and a load leave the tip on the SAME edge of the same
                # switch. Identical start position, different outcome - so it was never geometry.
                toolhead.wait_moves()
                if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
                    break
                self._stop_retract(slot)
                raise ValueError(
                    "ACE[%d]: entry never cleared after %.0fmm of tandem pull (queue drained and "
                    "re-checked) - filament is not moving back"
                    % (self.instance_num, pulled))
            # FORCE_MOVE, not a planner move: the pull runs cold and the planner
            # rejects cold extrusion.
            self.gcode.run_script_from_command(
                "FORCE_MOVE STEPPER=extruder DISTANCE=-%.1f VELOCITY=%.0f ACCEL=%.0f"
                % (seg, speed, _extruder_accel(self.printer)))
            pulled += seg

            if (pulled - last_checked_at) >= STALL_WINDOW_MM:
                # Drain the queue FIRST. Without this the encoder is read against motion that
                # has not happened yet, which is the bug this whole rewrite exists to fix.
                #
                # The drain is UNCONDITIONAL - deliberately NOT gated on having a hub encoder.
                # It serves two purposes and only one of them is the stall check: it also bounds
                # how stale the `while get_switch_state(SENSOR_TOOLHEAD)` read at the top of this
                # loop can be. Gate the drain on the encoder and a machine without one runs the
                # loop completely unpaced, so entry-clear is noticed an arbitrary distance late
                # and the tip is dragged that much further back. Only the stall JUDGEMENT below
                # needs the encoder.
                toolhead.wait_moves()
                now_p = _enc_pulses()
                seat_now = _seat()
                # INSTRUMENTATION 2026-09-01. Four theories for "a parked unload always takes
                # two attempts" fitted the end state and were all wrong: slack take-up, a
                # queue race, a chewed strand, and the ACE unwind finishing early. Each was
                # inferred from where the pull ENDED. What nothing records is WHEN the strand
                # stops following - so measure that instead of guessing again.
                #
                # Per window: commanded mm, hub-encoder delta (witnesses the ACE turning) and
                # the post-gear seat (witnesses the strand actually translating past the nip).
                # A failed attempt then answers it directly:
                #   encoder advancing, seat static  -> the ACE turns, the strand does not follow
                #   encoder static                  -> the ACE stopped or never accepted
                #   both advancing to the cap       -> it really is travelling and the cap is short
                _d = ((now_p - last_enc)
                      if (now_p is not None and last_enc is not None) else None)
                self.gcode.respond_info(
                    "ACE[%d]: [pull] %.0fmm commanded | enc %s (%s total) | post-gear %s"
                    % (self.instance_num, pulled,
                       ("%+d pulses" % _d) if _d is not None else "n/a",
                       now_p if now_p is not None else "n/a",
                       "MADE" if seat_now else ("clear" if seat_now is False else "n/a")))
                if postgear_cleared_at is None and start_seat is True and seat_now is False:
                    postgear_cleared_at = pulled
                    # REVERTED 2026-09-01. Two changes lived here for about an hour and both
                    # are gone:
                    #
                    #   1. rebasing cap_mm on this moment, on the theory that a parked start
                    #      spends its first stroke taking up slack.
                    #   2. re-issuing the ACE unwind to match the longer cap.
                    #
                    # (2) actively broke the pull. The instrumentation caught it: the strand
                    # moved 8mm, the extension fired at exactly 8mm, and then NOTHING moved for
                    # the next 48mm - a second retract command issued while one is already
                    # running evidently disrupts it. Before these changes the unload took two
                    # presses; after them the first press could not move the strand at all.
                    #
                    # The design is tandem retraction until the ENTRY sensor clears. Do not add
                    # commands inside that loop without hardware evidence they help.

                # THE HUB ENCODER IS NOT ADMISSIBLE HERE - see the docstring. It witnesses the
                # ACE, which is unwinding the full cap under its own command regardless of what
                # the strand does. The post-gear switch is downstream of the extruder nip, so
                # its release is the one thing that proves the strand travelled. seat_now is
                # required to be True rather than "not False" so a sensor read that fails
                # mid-pull yields no evidence instead of a false abort.
                if (start_seat is True and seat_now is True
                        and pulled >= POSTGEAR_CLEAR_MM):
                    self._stop_retract(slot)
                    raise ValueError(
                        "ACE[%d]: post-gear STILL reads filament after %.0fmm of EXECUTED "
                        "tandem pull - it must clear within %.0fmm from any park, so nothing "
                        "downstream of the extruder nip has moved (hub encoder %+d pulses over "
                        "the last window, which witnesses the ACE, not the strand). Either the "
                        "strand is not moving at the nip, or the tip started far below the park. "
                        "Stopping before the gears chew it further (cut above entry, open the "
                        "idler, eject the rest ACE-alone)."
                        % (self.instance_num, pulled, POSTGEAR_CLEAR_MM,
                           ((now_p - last_enc)
                            if (now_p is not None and last_enc is not None) else 0)))
                last_enc = now_p
                last_checked_at = pulled

        # One margin segment so the tip rests clear of the switch, not on its edge.
        self.gcode.run_script_from_command(
            "FORCE_MOVE STEPPER=extruder DISTANCE=-%.1f VELOCITY=%.0f ACCEL=%.0f"
                % (seg, speed, _extruder_accel(self.printer)))
        pulled += seg
        # Let the extruder actually FINISH before the ACE is told to stop. Previously the ACE
        # was stopped while up to BUFFER_TIME_HIGH * speed (~20mm) of extruder retraction was
        # still queued, so every successful extraction ended with the extruder pulling alone
        # against a stationary lane - the exact wrong-actuator condition this function exists
        # to prevent, on the success path, every single time.
        toolhead.wait_moves()
        self._stop_retract(slot)
        self.gcode.respond_info(
            "ACE[%d]: entry cleared after %.0fmm of tandem pull (cap %.0fmm, post-gear %s) - "
            "toolhead free, ACE takes it from here"
            % (self.instance_num, pulled, cap_mm,
               ("cleared at %.0fmm" % postgear_cleared_at)
               if postgear_cleared_at is not None else
               ("never armed" if start_seat is not True else "never cleared")))

    def _make_sensor_trigger_monitor(self, sensor_type):
        """
        Create a sensor trigger time monitor callback.

        Args:
            sensor_type: SENSOR_TOOLHEAD or SENSOR_RDM
            expected_length: Expected retraction distance (mm)
            expected_speed: Expected retraction speed (mm/s)

        Returns:
            Callable that monitors sensor state changes and returns timing data
        """
        state_data = {
            "start_time": None,
            "trigger_time": None,
            "initial_state": None,
            "call_count": 0
        }

        def monitor():
            state_data["call_count"] += 1
            current_state = self.manager.get_switch_state(sensor_type)

            # First call - record initial state and start time
            if state_data["start_time"] is None:
                state_data["start_time"] = time.time()
                state_data["initial_state"] = current_state
                return

            # Sensor state changed - record trigger time
            if state_data["trigger_time"] is None and current_state != state_data["initial_state"]:
                state_data["trigger_time"] = time.time()

        # Return both the monitor function and the state data
        monitor.get_timing = lambda: (
            state_data["trigger_time"] - state_data["start_time"]
            if state_data["trigger_time"] and state_data["start_time"]
            else None
        )
        monitor.get_call_count = lambda: state_data["call_count"]
        monitor.state_data = state_data

        return monitor

    def _retract(self, slot, length, speed, on_retract_started=None, on_wait_for_ready=None, early_stop_callback=None, max_retries=None):
        """
        Retract filament from slot with automatic retry on FORBIDDEN errors.

        Args:
            slot: Local slot index (0-3)
            length: Distance to retract (mm)
            speed: Retract speed (mm/s)

        Returns:
            dict: Response from ACE

        Raises:
            ValueError: If retraction fails after all retries
        """
        # MAX_RETRIES (6) x the retry delay is ~12s, which is INSIDE the window in which the
        # device refuses motion after a STOP_FEED_OR_ROLLBACK. A caller that just issued a stop
        # must pass STOP_SETTLE_ATTEMPTS instead, or it hard-fails on a refusal that was going
        # to clear on its own.
        max_retries = MAX_RETRIES if max_retries is None else int(max_retries)
        # No response at all is a comms problem, and 2s is a reasonable settle for that.
        no_response_delay_s = 2.0
        # A FORBIDDEN is not a comms problem: it means the PREVIOUS move is still executing, so
        # the only sensible wait is that move's own duration. The flat 2.0s waited ten times
        # longer than the thing it was waiting for on the eject's 2mm/10mm-s chunks, and every
        # first attempt is rejected because wait_ready() clears the send against a status cache
        # the heartbeat only refreshes at 1Hz -- at 5 chunks/s that race cannot be won.
        # Measured 2026-08-21: 80mm of extraction took ~96s, ~72s of it this delay.
        try:
            retry_delay_s = min(2.0, max(0.15, (float(length) / float(speed)) * 1.5))
        except (TypeError, ValueError, ZeroDivisionError):
            retry_delay_s = 2.0

        # Must run BEFORE wait_ready(): with assist active, ACE2 is 'busy'
        # by design and wait_ready() would stall without this.
        self._ensure_assists_off_for_motion(slot, "retract")

        self.wait_ready()
        self._last_retract_early_stopped = False

        # If the slot already reports empty, there is nothing to retract.
        if self._is_slot_empty(slot):
            self._last_retract_early_stopped = True
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract skipped - slot {slot} is empty"
            )
            return {"code": 0, "msg": "Retract skipped: slot empty"}

        for attempt in range(1, max_retries + 1):
            ace_status_before = self._info.get('status', 'unknown')
            retract_start_time = time.time()
            early_stop_state = {"triggered": False, "elapsed": None}

            if attempt > 1:
                banner = (f"ACE[{self.instance_num}]: _retract() attempt {attempt}/{max_retries} "
                          f"slot={slot} len={length}mm speed={speed}mm/s "
                          f"status={ace_status_before}")
                if attempt >= 3:
                    self.gcode.respond_info(banner)
                else:
                    logging.debug(banner)

            request = self.protocol.build_unwind_filament_request(slot, length, speed)

            response_container = {"response": None, "done": False}

            def check_slot_empty():
                if early_stop_state["triggered"]:
                    return
                if self._is_slot_empty(slot):
                    early_stop_state["triggered"] = True
                    self._last_retract_early_stopped = True
                    early_stop_state["elapsed"] = time.time() - retract_start_time
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Retract stopped after "
                        f"{early_stop_state['elapsed']:.2f}s - slot {slot} reports empty"
                    )
                    self._stop_retract(slot)

            def callback(response):
                response_container["response"] = response
                response_container["done"] = True

            self.send_request(request, callback)

            timeout = time.time() + 5.0
            while not response_container["done"] and time.time() < timeout:
                self.reactor.pause(self.reactor.monotonic() + 0.1)

            response = response_container["response"]
            ace_status_after = self._info.get('status', 'unknown')

            if not response:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: _retract() attempt {attempt}/{max_retries} - "
                    f"No response from ACE"
                )
                if attempt < max_retries:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Waiting {no_response_delay_s}s before retry..."
                    )
                    self.reactor.pause(self.reactor.monotonic() + no_response_delay_s)
                    continue
                else:
                    raise ValueError(
                        f"ACE[{self.instance_num}]: Retract failed - no response after "
                        f"{max_retries} attempts. Check ACE connection"
                    )

            result_code = response.get('code', -1)
            result_msg = response.get('msg', 'unknown')

            if result_code == 0 and result_msg != 'FORBIDDEN':
                # self.gcode.respond_info(
                #     f"ACE[{self.instance_num}]: _retract() command accepted on attempt {attempt}"
                # )

                # Execute callback immediately after retract command accepted
                if on_retract_started is not None:
                    try:
                        on_retract_started()
                    except Exception as cb_error:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: Retract started callback error: {cb_error}"
                        )

                expected_time_s = length / speed
                dwell_time_s = expected_time_s

                # Waiting 1/4 of the expected time before waiting for ready to avoid any
                # timing issues with the ACE reporting ready inbetween gear shifting
                # Call callback during dwell in case sensor changes early
                dwell_end = time.time() + (dwell_time_s)
                while time.time() < dwell_end:
                    if on_wait_for_ready is not None:
                        on_wait_for_ready()
                    check_slot_empty()
                    if early_stop_state["triggered"]:
                        self.wait_ready()
                        return {"code": 0, "msg": "Retract stopped early: slot empty"}
                    if early_stop_callback is not None:
                        stop_reason = early_stop_callback()
                        if stop_reason:
                            self._stop_retract(slot)
                            self.wait_ready()
                            return {"code": 0, "msg": f"Retract stopped early: {stop_reason}"}
                    self.reactor.pause(self.reactor.monotonic() + 0.2)

                def wait_cycle():
                    if on_wait_for_ready is not None:
                        on_wait_for_ready()
                    check_slot_empty()
                    if early_stop_callback is not None:
                        stop_reason = early_stop_callback()
                        if stop_reason:
                            self._stop_retract(slot)

                self.wait_ready(on_wait_cycle=wait_cycle)

                if early_stop_state["triggered"]:
                    return {"code": 0, "msg": "Retract stopped early: slot empty"}

                return response

            # A first-attempt FORBIDDEN is the NORMAL case for chunked motion, not an incident.
            # Eight lines of respond_info per rejection put ~600 lines through Moonraker's
            # websocket to every connected client during one eject -- the same kind of load that
            # stalled the reactor and broke Cartographer homing on 2026-08-21. Keep it in the log
            # where it stays diagnosable, and only speak up once it stops looking routine.
            reject_msg = (
                f"ACE[{self.instance_num}]: Retract rejected - attempt {attempt}/{max_retries} "
                f"code={result_code} msg='{result_msg}' slot={slot} len={length}mm "
                f"speed={speed}mm/s status {ace_status_before}->{ace_status_after}"
            )
            if attempt >= 3:
                self.gcode.respond_info(reject_msg)
            else:
                logging.debug(reject_msg)

            if attempt < max_retries:
                self.reactor.pause(self.reactor.monotonic() + retry_delay_s)
            else:
                total_wait = max_retries * retry_delay_s
                raise ValueError(
                    f"ACE[{self.instance_num}]: Retract failed after {max_retries} attempts\n"
                    f"  Final Code: {result_code}\n"
                    f"  Final Message: '{result_msg}'\n"
                    f"  Slot: {slot}\n"
                    f"  Length: {length}mm\n"
                    f"  Speed: {speed}mm/s\n"
                    f"  Total wait time: {total_wait}s\n"
                    f"  Final ACE status: {ace_status_after}"
                )

        raise ValueError(
            f"ACE[{self.instance_num}]: Retract logic error - exhausted retries without exception"
        )

    def _stop_retract(self, slot):
        """Stop retracting filament."""
        def callback(response):
            if response and response.get("code") != 0:
                msg = response.get("msg", "Unknown error")
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop retract error: {msg}"
                )
            elif response:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Stop retract successful: {response}"
                )

        request = self.protocol.build_stop_unwind_filament_request(slot)
        self.send_request(request, callback)

    def _wait_hot_for_seat(self):
        """Block until the extruder can extrude, just before the seat phase moves it.

        Counterpart of the load guard in manager.py, which now starts the heat with M104 and
        lets the bowden feed run cold underneath it. By the time the filament reaches the
        toolhead sensor the nozzle is usually at temperature and this returns immediately;
        when it is not, waiting HERE costs only the residual, not the whole heat-up.
        """
        extruder = self.printer.lookup_object("extruder", None)
        if extruder is None:
            return
        heater = extruder.get_heater()
        cur = heater.get_temp(self.reactor.monotonic())[0]
        if cur >= heater.min_extrude_temp:
            return
        target = heater.target_temp
        if target < heater.min_extrude_temp:
            # Nothing is heating and nothing asked for heat: seating would grind cold
            # filament into a cold nozzle. Same fail-fast stance as the load guard.
            raise ValueError(
                f"ACE[{self.instance_num}]: extruder at {cur:.0f}\u00b0C with no active "
                f"heat target - cannot seat filament cold"
            )
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: waiting for nozzle ({cur:.0f}\u00b0C -> "
            f"{target:.0f}\u00b0C) before seating filament"
        )
        self.gcode.run_script_from_command(
            f"TEMPERATURE_WAIT SENSOR=extruder MINIMUM={max(heater.min_extrude_temp, target - 2):.0f}"
        )

    def _feed_to_toolhead_with_extruder_assist(self, local_slot, feed_length, feed_speed,
                                               extruder_feeding_length, extruder_feeding_speed):
        """
        Feed filament to toolhead using ACE feed + extruder assist.

        Starts ACE feed, polls toolhead sensor until triggered (or timeout),
        then slows to extruder_feeding_speed and drives the extruder to seat
        the filament, finally switches to feed_assist mode.

        Args:
            local_slot: Slot index to feed from
            feed_length: Total length to feed (mm)
            feed_speed: Initial ACE feed speed (mm/s)
            extruder_feeding_length: Extruder assist distance (mm)
            extruder_feeding_speed: Slow speed for final extruder push (mm/s)

        Returns:
            float: Extruder distance pushed during assist

        Raises:
            ValueError: If feed command fails or sensor times out
        """
        self._disable_feed_assist(local_slot)
        self.gcode.run_script_from_command("ACE_SWAP_PHASE NAME=bowden")
        self.execute_feed_with_retries(local_slot, feed_length, feed_speed)

        expected_time = feed_length / feed_speed
        timeout_s = expected_time * self.timeout_multiplier

        # Coordinated extruder nudges during ACE feed
        start_time = time.time()

        err_first_seen = None
        while not self.manager.get_switch_state(SENSOR_TOOLHEAD):
            now = time.time()
            # Fail fast on the firmware's own verdict: the device aborts a
            # blocked feed itself (slot -> gear_err) long before our sensor
            # timeout - don't wait blind, and don't grind with the extruder.
            if now - start_time > self.FEED_ERROR_GRACE_S:
                slot_error = self._get_slot_feed_error(local_slot)
                if slot_error is None:
                    err_first_seen = None
                elif err_first_seen is None:
                    err_first_seen = now
                elif now - err_first_seen >= self.FEED_ERROR_CONFIRM_S:
                    self._stop_feed(local_slot)
                    raise ValueError(
                        f"ACE[{self.instance_num}]: Firmware aborted the feed on "
                        f"slot {local_slot}: {slot_error}. Filament cannot "
                        f"advance - check spool, slot outlet and filament path."
                    )
            if now - start_time > timeout_s:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed timeout for {feed_length}mm after {timeout_s} seconds"
                )
                break
            # 20ms, not 100: at 60mm/s every poll tick is 1.2mm of overshoot past the
            # sensor, and the stop must land inside the inline buffer's travel.
            self.dwell(0.02)

        # Final sanity check
        if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
            # Last chance to honor the firmware verdict before the extruder
            # assist phase: grinding the extruder against a blocked path for
            # 60s can chew the filament and leave fragments in the toolhead.
            slot_error = self._get_slot_feed_error(local_slot)
            if slot_error is not None:
                # Same stale-cache guard as the poll loop above.
                self.dwell(self.FEED_ERROR_CONFIRM_S)
                slot_error = self._get_slot_feed_error(local_slot)
            if slot_error is not None:
                raise ValueError(
                    f"ACE[{self.instance_num}]: Firmware aborted the feed on "
                    f"slot {local_slot}: {slot_error}. Skipping extruder "
                    f"assist - filament cannot advance."
                )
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Toolhead sensor not triggered after feed. "
                f"Running extruder assist for up-to 60s..."
            )
            self._enable_feed_assist(local_slot)

            timeout = time.time() + 60.0
            while not self.manager.get_switch_state(SENSOR_TOOLHEAD) and time.time() < timeout:
                self.dwell(1)
            self._disable_feed_assist(local_slot)

            if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
                raise ValueError(
                    f"ACE[{self.instance_num}]: Feeding filament to toolhead failed. "
                    f"Toolhead filament sensor is not triggering. Filament may be jammed."
                )
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Toolhead sensor finally triggered after "
                    f"running feed-assist for 60s. Continuing..."
                )
        # Rebuilt 2026-08-22/23. The old sequence SLOWED the still-queued 2500mm bulk
        # feed and left it running through the seat -- an open-loop push with no
        # compliance, which buckled filament into the hub (the only open volume in the
        # path).
        self._stop_feed(local_slot)
        if self.seat_verify_sensor:
            # No park at entry: the trip point does not matter when POST-GEAR terminates
            # the crossing, so the moment the approach stops, both ends move on together
            # -- ACE feeding and extruder pulling at the SAME speed, continuously, never
            # taking turns (teeth turning while the ACE holds grinds the surface; the
            # ACE pushing a clamped nip is a wall). Any stop overshoot bleeds forward
            # through the moving nip. Cold by design: post-gear is short of the melt
            # zone; heat gates later, at the melt-zone door.
            self.wait_ready()
            if self._seat_sensor_triggered():
                # RUNOUT SWAP: post-gear already reads filament while the new head
                # has only just crossed entry - the consumed tool's tail stub still
                # fills gears->nozzle. The normal shape is unusable twice over: the
                # crossing's stop condition is already true (it would end at 0mm
                # with the head ungripped at entry), and the assist seat would
                # starve - assist only pushes when the buffer sees the extruder
                # pulling THIS lane, and the extruder grips the STUB (Simon's
                # catch, 2026-08-25). Butt-to-butt instead: hot tandem COMMANDED
                # push, ACE feeding at the extruder's speed the whole way so the
                # new head never loses contact with the stub's tail, carrying it
                # through the gears and melt zone while the stub extrudes out
                # ahead of it. Post-gear cannot terminate this (it lies); fixed
                # length, hot because the stub can only move by melting.
                self.gcode.run_script_from_command("ACE_SWAP_PHASE NAME=stub_crossing")
                # Simon's sequence (2026-08-25): the stub PRINTS - whatever lane
                # the user picked, colour included ("plan your prints better").
                # The tandem only closes most of entry->gears while the tail is
                # STILL GRIPPED (no empty-nip spinning, no tail grinding); the
                # head-to-tail gap persists through tandem (both ends advance
                # equally) and is closed afterwards by the assist HUG - the
                # lane's spring buffer presses the free head against the receding
                # tail, exactly the hand on a manual bowden push. The handoff
                # then completes under assist during the purge/resumed print.
                push = self.runout_bite_length
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: tail stub in the toolhead - bite "
                    f"({push:.0f}mm hot tandem, tail stays gripped), hug follows"
                )
                self._wait_hot_for_seat()
                self.execute_feed_with_retries(local_slot, push, extruder_feeding_speed)
                pushed = 0.0
                while pushed < push:
                    err = self._confirmed_slot_error(local_slot)
                    if err is not None:
                        self._stop_feed(local_slot)
                        self.printer.lookup_object('toolhead').wait_moves()
                        raise ValueError(
                            f"ACE[{self.instance_num}]: device reports '{err}' on "
                            f"slot {local_slot} during the stub crossing - it has "
                            f"stopped its own motor. Check the lane."
                        )
                    self.gcode.run_script_from_command(
                        "FORCE_MOVE STEPPER=extruder DISTANCE=%.1f VELOCITY=%.0f ACCEL=%.0f"
                        % (self.RELEASE_CHUNK_MM, extruder_feeding_speed, _extruder_accel(self.printer)))
                    pushed += self.RELEASE_CHUNK_MM
                    # Same drain as the normal crossing: `pushed` must mean executed, not
                    # queued, or the per-chunk device-fault check below runs against motion
                    # that has not happened and the bite over-runs its measured length.
                    self.printer.lookup_object('toolhead').wait_moves()
                self._stop_feed(local_slot)
                self.wait_ready()
                self._stub_load_completed = True
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: stub crossing done ({pushed:.0f}mm) "
                    f"- new head at the nozzle, engaging assist"
                )
            else:
                self.gcode.run_script_from_command("ACE_SWAP_PHASE NAME=crossing")
                self.execute_feed_with_retries(local_slot, 60.0, extruder_feeding_speed)
                crossed = 0.0
                while not self._seat_sensor_triggered():
                    err = self._confirmed_slot_error(local_slot)
                    if err is not None:
                        self._stop_feed(local_slot)
                        self.printer.lookup_object('toolhead').wait_moves()
                        raise ValueError(
                            f"ACE[{self.instance_num}]: device reports '{err}' on slot "
                            f"{local_slot} during the crossing - it has stopped its own "
                            f"motor, so continuing would only grind. Check the lane."
                        )
                    if crossed >= 60.0:
                        self._stop_feed(local_slot)
                        raise ValueError(
                            f"ACE[{self.instance_num}]: post-gear never triggered after "
                            f"{crossed:.0f}mm of crossing - filament is not passing the "
                            f"gears"
                        )
                    # FORCE_MOVE, not a planner move: the crossing runs COLD by design and
                    # the planner rejects cold extrusion (this failed the first genuinely
                    # cold load, 2026-08-23 -- every earlier crossing had a hot nozzle
                    # masking it). Same rule as the tandem extract.
                    self.gcode.run_script_from_command(
                        "FORCE_MOVE STEPPER=extruder DISTANCE=%.1f VELOCITY=%.0f ACCEL=%.0f"
                        % (self.RELEASE_CHUNK_MM, extruder_feeding_speed, _extruder_accel(self.printer)))
                    crossed += self.RELEASE_CHUNK_MM
                    # CROSSING LOOPS MUST READ THE SENSOR AGAINST REAL POSITION (2026-08-30).
                    # FORCE_MOVE does not block, so without this drain `crossed` counts
                    # millimetres QUEUED and _seat_sensor_triggered() is read against a position
                    # up to BUFFER_TIME_HIGH * speed behind the commanded total. Two failure
                    # modes: the loop keeps commanding after the tip has physically reached
                    # post-gear, driving it that much further toward the melt zone; or it burns
                    # the 60mm cap and raises "post-gear never triggered" on a healthy load.
                    # This is the same defect as the tandem stall check, mirrored.
                    self.printer.lookup_object('toolhead').wait_moves()
                self._stop_feed(local_slot)
                self.wait_ready()
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: post-gear triggered after {crossed:.0f}mm "
                    f"of crossing - engaging assist"
                )
        else:
            # No post-gear switch on this machine: back off to the entry release edge
            # for a fixed datum, step to the re-trip (proof of tip motion through the
            # soft buffer), then the configured distance to the nip; the extruder seat
            # below carries it the rest of the way blind.
            released_mm = 0.0
            while self.manager.get_switch_state(SENSOR_TOOLHEAD):
                err = self._confirmed_slot_error(local_slot)
                if err is not None:
                    raise ValueError(
                        f"ACE[{self.instance_num}]: device reports '{err}' on slot "
                        f"{local_slot} while backing off to the release edge - its "
                        f"motor is stopped, so the tip cannot move."
                    )
                if released_mm >= self.RELEASE_BACKOFF_CAP_MM:
                    raise ValueError(
                        f"ACE[{self.instance_num}]: entry sensor still triggered after "
                        f"{released_mm:.0f}mm of back-off - filament is not moving "
                        f"back, check the path for a jam"
                    )
                self._retract(local_slot, self.RELEASE_CHUNK_MM, self.RELEASE_BACKOFF_SPEED)
                released_mm += self.RELEASE_CHUNK_MM
            stepped = 0.0
            while not self.manager.get_switch_state(SENSOR_TOOLHEAD) and stepped < 24.0:
                self.execute_feed_with_retries(
                    local_slot, self.RELEASE_CHUNK_MM, self.RELEASE_BACKOFF_SPEED)
                self.wait_ready()
                stepped += self.RELEASE_CHUNK_MM
            if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
                raise ValueError(
                    f"ACE[{self.instance_num}]: entry sensor never re-tripped after "
                    f"{stepped:.0f}mm of handoff feed - filament is not advancing"
                )
            self.execute_feed_with_retries(local_slot, self.entry_to_gear_mm,
                                           self.RELEASE_BACKOFF_SPEED)
            self.wait_ready()

        # The device silently swallows an enable sent while it is still settling from the
        # feed op (every load on 2026-08-22 logged a post-enable drop; the watchdog's late
        # restore left the seat pulling against a statically gripped ACE). Do not move the
        # extruder until the device CONFIRMS assist -- on ACE2 assist makes the instance
        # 'busy' and the slot reports 'assisting'.
        self.gcode.run_script_from_command("ACE_SWAP_PHASE NAME=assist")
        for attempt in range(3):
            self._enable_feed_assist(local_slot)
            if self._wait_assist_active(local_slot, 2.5):
                break
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: assist not confirmed on slot {local_slot} "
                f"(attempt {attempt + 1}/3) - re-sending"
            )
        else:
            raise ValueError(
                f"ACE[{self.instance_num}]: feed assist never engaged on slot "
                f"{local_slot} - refusing to seat against a static ACE grip"
            )

        # NO heat gate here. The crossing ends at post-gear, which is cold-side of the
        # heatbreak -- nothing that follows in THIS method enters the melt zone. The gate
        # moved to the nozzle phase in _feed_filament_into_toolhead, which is the first
        # thing that actually pushes into the hot end, and which an idle load skips.
        if self.seat_verify_sensor:
            # Post-gear already proved the extruder has the filament; the caller's
            # sensor-to-nozzle feed runs from that datum under confirmed assist.
            return 0.0
        self._extruder_move(extruder_feeding_length, extruder_feeding_speed, wait_for_move_end=True)
        return self.extruder_feeding_length

    def _extruder_push_verified(self, slot, total_mm, speed, what="melt-zone push"):
        """Push filament with the extruder in chunks, each verified at the hub encoder.

        THE INVARIANT (Simon, 2026-09-03, after a buckle and a grind on the same day): neither
        actuator may move filament further than the inline buffer can absorb (~3mm) without
        positive evidence the other end is following. "Armed", device "busy" and the driver's
        own claim are NOT evidence - bench-proven 2026-09-04, when all three read quiet while
        554mm free-fed through the hub. Real filament through the hub encoder is.

        Two-sided, because either direction alone folds filament:
          - STARVATION (hub delta far below the chunk): the ACE is not paying out, so the
            extruder is consuming the buffer and will then grind the strand at its nip. Caught
            after one chunk (~10mm) instead of after 90.
          - OVERRUN (hub delta far above the chunk): the ACE is free-running past the hub
            faster than the extruder takes it, and the surplus folds in the hub - the
            2026-09-03 buckle. Blind-stop the assist (the one stop that works with no valid
            claim) and abort.

        No encoder configured -> the legacy single move, loudly, so machines without the RDM
        wheel keep working but nobody mistakes that for verification.
        """
        enc = self.printer.lookup_object("ace_hub_encoder", None)
        if enc is None:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: no hub encoder - {what} of {total_mm:.0f}mm runs "
                f"UNVERIFIED (cannot prove the lane is following)")
            self._extruder_move(total_mm, speed, wait_for_move_end=True)
            return

        def _dist():
            try:
                return float(enc.get_status(self.reactor.monotonic()).get("distance_mm", 0.0))
            except Exception:
                return 0.0

        def _assist():
            """(cycles, seconds) of ACE assist activity - the SECOND witness.

            The hub encoder is NOT sufficient on its own and never was: Simon's measured note,
            2026-08-26, is that it "stayed frozen through a perfectly good swap", while
            feed_assist_count "ticks steadily when healthy" and stayed 0 through a 95-minute
            air print. Building a hard abort on the frozen-capable instrument alone failed two
            healthy toolchanges on 2026-09-04 - the operator then extruded the same lane by
            hand, with the same assist armed, and it fed fine.
            """
            try:
                st = self._info or {}
                return (int(st.get("feed_assist_count", 0) or 0),
                        float(st.get("cont_assist_time", 0.0) or 0.0))
            except Exception:
                return (0, 0.0)

        # v2, 2026-09-03. v1 used a 40% per-chunk floor borrowed from a tag-read heuristic;
        # a 77%-delivery bleed passed all nine chunks while the operator HEARD the slip. A slip
        # is a deficit that accumulates, not a binary stall, so v2 judges four ways and logs
        # every chunk (v1's pass path logged nothing - the bleed was invisible until audible).
        # Thresholds are derived from measurement, not convenience:
        #   SLOP 4mm        one-time: buffer travel ~3mm + edge quantisation ~1mm
        #   floor 70%       healthy engaged chunks measure ~10/10; first chunk legitimately
        #                   dips while the buffer tensions (measured 7.5) so it is exempt
        #   rolling 80%/3   catches a uniform bleed noise can hide from the floor: today's
        #                   77% trace trips this at chunk 4 (replayed offline against the
        #                   recorded numbers); a healthy 7.5,9.1,9.2,9.8.. trace passes
        #   deficit 8mm     absolute backstop: total commanded-minus-delivered beyond slop
        CHUNK = 10.0
        SLOP = 4.0
        MAX_DEFICIT = 8.0
        pushed = 0.0
        delivered = 0.0
        hist = []
        _under = False
        def _fail(kind, msg):
            raise ValueError(
                f"ACE[{self.instance_num}]: {what} {kind} on slot {slot} - {msg} "
                f"(delivered {delivered:.1f}/{pushed:.0f}mm, chunks {hist}). Inspect the "
                f"strand at the extruder gears before retrying.")
        while pushed < total_mm - 1e-6:
            c = min(CHUNK, total_mm - pushed)
            d0 = _dist()
            a0 = _assist()
            self._extruder_move(c, speed, wait_for_move_end=True)
            # let the last edges land; the counter is event-driven but the read is polled
            self.reactor.pause(self.reactor.monotonic() + 0.4)
            delta = _dist() - d0
            a1 = _assist()
            assist_alive = (a1[0] > a0[0]) or (a1[1] > a0[1] + 0.01)
            pushed += c
            if delta < -0.5:
                # Counter discontinuity: ACE_HUB_ENCODER RESET=1 (the hub-crossing anchor
                # schedules one on a reactor callback, which runs inside our 0.4s pause) or a
                # swallowed first read. Not a measurement - resync the ledger to neutral and
                # restart the rolling window rather than raising STALLED/OVERRUN on garbage
                # and poisoning the next three chunks (momus F4).
                logging.warning("ace: %s slot %s chunk %.0f - encoder discontinuity "
                                "(delta %.1f), resyncing, chunk not judged", what, slot, c, delta)
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: {what} - encoder discontinuity on slot {slot}, "
                    f"one {c:.0f}mm chunk ran unjudged (counter reset mid-push)")
                delivered = pushed - SLOP
                hist = []
                continue
            delivered += max(0.0, delta)
            hist.append(round(delta, 1))
            deficit = pushed - delivered - SLOP
            logging.info("ace: %s slot %s chunk %.0f -> hub %.1f (cum %.1f/%.0f deficit %.1f)",
                         what, slot, c, delta, delivered, pushed, max(0.0, deficit))
            if delta > c * 2.0 + SLOP:
                try:
                    # _stop_rollback_assist early-returns when a rollback is tracked on a
                    # DIFFERENT lane, and stopping that lane first costs a 1.0s blocking dwell
                    # before the stop that matters (momus r2 f3: ~18mm more fold at bench
                    # free-run rate). The tracked claim is stale bookkeeping, not hardware -
                    # drop it in RAM so the stop for OUR lane takes the idx<0 send-anyway path
                    # and goes on the wire FIRST.
                    if 0 <= self._rollback_assist_index != slot:
                        logging.warning("ace: dropping stale rollback claim on slot %s to "
                                        "stop slot %s without delay",
                                        self._rollback_assist_index, slot)
                        self._rollback_assist_index = -1
                    self._stop_rollback_assist(slot)   # blind opcode-9 stop, needs no claim
                except Exception:
                    logging.exception("ace: blind stop after overrun failed")
                _fail("OVERRUN", f"hub saw {delta:.1f}mm for a {c:.0f}mm chunk - the ACE is "
                                 f"free-running past the hub; a stop was ISSUED (verify the "
                                 f"device went quiet before trusting it), check the hub for "
                                 f"a fold")
            # UNDER-DELIVERY IS REPORTED, NOT ADJUDICATED. THIS INSTRUMENT CANNOT DECIDE IT.
            #
            # The hub encoder sits ~934mm upstream with the ACE's spring buffer in between, and
            # that buffer exists PRECISELY to decouple the two: the ACE feeds in assist bursts
            # and between them the extruder pulls from slack while the encoder sits still. That
            # is this machine's own measured, written finding - printer.cfg sets
            # detection_length: 120.0 for the clog detector because "120mm is longer than the
            # buffer can plausibly absorb", after demanding an edge every 12mm false-tripped a
            # CLOG twice inside 90s of a healthy print. The same file states that a toolchange
            # spends FAR more than detection_length with the hub static.
            #
            # This guard's whole window - 10mm chunks, 90mm total, a 12mm budget - sits inside
            # the regime that calibration exempts by name. ace_purge.cfg reaches the same
            # conclusion about the same sensor: "blind in three known regimes (it slips under an
            # ACE push...) so aborting on it alone has already thrown away a swap that worked."
            #
            # And the record proves the signal carries no discrimination here. In four
            # invocations, a lane the operator then hand-extruded PERFECTLY measured [0.9, 0.0],
            # and the run that genuinely ground measured [0.9, 0.0, 0.0, ...]. Those openings are
            # identical. No threshold can separate two identical measurements; tightening one
            # only changes which of them gets punished. Both of this guard's previous settings
            # were wrong in opposite directions for exactly that reason - it hard-aborted two
            # healthy toolchanges on 2026-09-03, and then passed a 4.7-of-90mm grind on
            # 2026-09-04.
            #
            # So: say what was measured, loudly, with the numbers, every time - and leave the
            # abort to the instruments that ARE sized for it. The ACE's own feed watchdog is
            # armed (check=110 error=100) and halts the device on a stalled feed; the clog
            # detector at 120mm is calibrated for this buffer. Both are better arbiters than a
            # 12mm budget on a sensor that legitimately reads zero across a whole toolchange.
            #
            # OVERRUN above stays FATAL and is deliberately untouched: it fires on the hub
            # reading MORE than commanded, and the blindness documented here causes an
            # UNDER-read, never an over-read. A fold at the hub is a different signature and
            # this instrument does see it.
            #
            # THE DECISIVE EVIDENCE is not that the two openings look alike. It is the
            # 2026-09-03 abort at klippy.log.2026-09-03:317902 - chunks
            # [5.6, 0, 0, 0, 0, 0, 4.7], aborted on the LAST chunk with
            # "hub saw only 4.7mm AND the ACE assist never cycled - nothing is feeding",
            # and it paused a live print. The hub had just measured 4.7mm of real filament
            # motion in that very chunk: the second-largest reading of the run. _assist() reads
            # the 1Hz status cache, and a chunk takes ~1.65s, so two samples of a 1Hz counter
            # alias. The guard had two witnesses that disagreed and chose the aliasing one over
            # the one that had just recorded motion. "Both witnesses dead" is not a sound arm.
            #
            # AND BE HONEST ABOUT WHAT CATCHES THIS INSTEAD: nothing does, today. Do not read
            # the paragraph above as "the other guards will get it".
            #   - The clog detector is SUPPRESSED for the whole swap: _ACE_SWAP_VARS.swapping is
            #     set at ace.cfg:701 and cleared at ace_purge.cfg:148, and printer.cfg's
            #     runout_gcode says "Encoder quiet during a toolchange - expected, not pausing."
            #     When it re-arms, _ACE_RUNOUT_VERDICT stands down on `moved`, which is built
            #     from feed_assist_count and cont_assist_time - the same witness that voted down
            #     all nine chunks of the 2026-09-04 grind.
            #   - The ACE feed watchdog cannot fire inside one push: check_length is 110mm and
            #     the entire melt-zone push is 90mm. It also compares commanded feed against the
            #     ACE's OWN spool motion, so a lane paying out into a slack bowden while the
            #     extruder grinds at the nip looks healthy to it. Wrong end of the path.
            #
            # So this window is genuinely unguarded, and saying so is the point. The honest fix
            # is a witness that measures filament rather than rotation - GET_FEED_INFO's
            # moved_mm, logged at completion below - and a threshold built from a measured
            # healthy baseline, which this machine has never recorded.
            # Judged ONCE, after the loop. Per-chunk advisories fire on every recorded trace -
            # the 12mm budget is under two chunks, so any quiet hub trips it at chunk 2 - which
            # would print ~8 near-identical lines per toolchange and ~240 across a four-colour
            # print. An alarm that fires on every healthy run is not a signal, it is a hum, and
            # the one that matters would be indistinguishable from the 239 that did not.
            _under = (_under or deficit > MAX_DEFICIT
                      or (pushed - c > SLOP and delta < c * 0.7)
                      or (len(hist) >= 4 and sum(hist[-3:]) / 3.0 < c * 0.8))
        # Read ONCE, after the push, outside the loop. GET_FEED_INFO is a last-operation
        # register and not an odometer, so per-chunk sampling could only have repeated a stale
        # constant - and putting a blocking read inside the loop delayed the OVERRUN stop by up
        # to a second per chunk, which is the exact latency a previous review removed.
        # moved_mm is signed: negative means a retract, not under-delivery.
        _fi = self._read_feed_info(slot)
        _dev = ("device last-op: moved %smm counts %s" % (_fi.get("moved_mm"),
                                                          _fi.get("motor_counts"))
                if _fi else "device last-op: no GET_FEED_INFO answer")
        logging.info("ace: %s slot %s complete - %.1f/%.0fmm at the hub, chunks %s | %s",
                     what, slot, delivered, pushed, hist, _dev)
        if _under:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: {what} UNDER-DELIVERY advisory - the hub saw "
                f"{delivered:.1f}mm of {pushed:.0f}mm commanded (chunks {hist}), assist "
                f"{'cycling' if assist_alive else 'IDLE'}. {_dev}. NOT stopping: this encoder "
                f"is blind under an ACE push and across a toolchange, and nothing else guards "
                f"this window either - the clog detector is suppressed during a swap and the "
                f"feed watchdog's 110mm window is larger than this 90mm push. If the print will "
                f"not extrude, this line is the reason to look.")

    def unpark_to_nozzle(self, slot, target_temp=0):
        """Advance a PARKED tip from post-gear into the melt zone.

        A park leaves the tip cold-side of the heatbreak, ~40mm short of the nozzle, with
        post-gear still triggered. That reads as "filament present" to every sensor check,
        which is why the toolchange used to relabel it 'nozzle' and return -- starting the
        print 40mm short with the state claiming otherwise. This is the missing reverse of
        the park: heat first (nothing may enter the melt zone cold), let the ACE follow
        under assist, then push exactly the park distance back in.
        """
        if target_temp > 0:
            self.gcode.run_script_from_command("M104 S%d" % int(target_temp))
        # Assist so the ACE pays out slack instead of the extruder dragging a static lane.
        if self._feed_assist_index != slot:
            self._enable_feed_assist(slot)
        # G1: the enable above is commanded, not observed. Every other push in this file
        # waits for the device to actually assist before the extruder moves; this one only
        # waited for heat (argus audit, 2026-08-28). Same gate as the seat.
        if not self._wait_assist_active(slot, 8.0):
            raise ValueError(
                f"ACE[{self.instance_num}]: feed assist never engaged on slot {slot} - "
                f"refusing to unpark against a static ACE grip"
            )
        self._wait_hot_for_seat()
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: unparking slot {slot} - "
            f"{self.toolhead_full_purge_length:.0f}mm post-gear to nozzle"
        )
        self._extruder_push_verified(slot, self.toolhead_full_purge_length,
                                     self.toolhead_slow_loading_speed,
                                     what="unpark to nozzle")
        return self.toolhead_full_purge_length

    def _confirmed_slot_error(self, slot, since=None):
        """The device's own error for this slot, or None. Confirmed, not a single frame.

        The ACE reports seven distinct faults (feed/rollback/assist/preload/stuck/tangled/
        motor) and STOPS ITS OWN MOTOR when it raises one. Loops that only watch a sensor
        and a distance cap keep commanding into that stop, then report a generic timeout --
        losing the one piece of information worth having. `tangled_error` in particular is
        an instruction to go look at the spool, not to retry.

        Confirmation matters as much as detection: the status cache is refreshed at 1Hz and
        can still hold the PREVIOUS operation's fault, which killed two healthy loads on
        2026-08-22. Same rule as the feed loop -- it must survive a refresh.
        """
        detail = self._get_slot_feed_error(slot)
        if detail is None:
            return None
        self.dwell(self.FEED_ERROR_CONFIRM_S)
        return self._get_slot_feed_error(slot)

    def _wait_assist_active(self, slot, timeout_s):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # 'busy' is DEVICE-WIDE and has other causes (a feed, a preload, another
            # slot's assist). On its own it proved nothing about this slot - it is the exact
            # term that passed the 2026-09-03 unpark with nothing armed at all. Require the
            # driver's claim to name this slot as well; the per-slot check below stays the
            # stronger arm.
            if (self._info.get("status") == "busy"
                    and self.protocol.feed_assist_causes_busy()
                    and self._feed_assist_index == slot):
                return True
            for s in self._info.get("slots", []):
                if s.get("index") == slot:
                    detail = s.get("status_detail") or s.get("status")
                    if detail in ("assisting", "shifting"):
                        return True
            self.dwell(0.3)
        return False

    def _seat_sensor_triggered(self):
        sensor = self.printer.lookup_object(
            "filament_switch_sensor %s" % self.seat_verify_sensor, None
        )
        if sensor is None:
            return True
        return bool(sensor.get_status(self.reactor.monotonic()).get("filament_detected"))

    def execute_feed_with_retries(self, local_slot, feed_length, feed_speed):
        max_retries = MAX_RETRIES
        for attempt in range(max_retries):
            response = self.feed_filament_with_wait_for_response(local_slot, feed_length, feed_speed)

            if response.get("code") == 0:
                break
            elif response.get("msg") == "FORBIDDEN" and attempt < max_retries - 1:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed FORBIDDEN, waiting 1s before retry "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )
                self.dwell(delay=1.0)
            else:
                raise ValueError(
                    f"ACE[{self.instance_num}]: Feed failed: {response.get('msg')}"
                )

    STUB_NUDGE_BUDGET_MM = 6.0

    def _clear_stub_from_entry(self):
        """Runout swap: the consumed lane's stub has re-covered the entry switch.

        The tail stops within a mm of the switch when the runout is declared, and the
        pause retract pulls it back over it. A sensor-terminated approach would then
        end at 0mm with the new head still at the hub, and the bite would push the
        stub off the gears. Nudge the stub down, hot, until the switch clears; the
        budget is well inside entry->gears so the tail stays gripped.
        """
        self._wait_hot_for_seat()
        moved = 0.0
        while (self.manager.get_switch_state(SENSOR_TOOLHEAD)
               and moved < self.STUB_NUDGE_BUDGET_MM):
            self._extruder_move(1.0, self.extruder_feeding_speed, wait_for_move_end=True)
            moved += 1.0
        if self.manager.get_switch_state(SENSOR_TOOLHEAD):
            raise ValueError(
                f"ACE[{self.instance_num}]: entry switch still covered after a "
                f"{self.STUB_NUDGE_BUDGET_MM:.0f}mm nudge - that is not a stub re-covering "
                f"it. Check the toolhead before loading."
            )
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: stub nudged {moved:.0f}mm to clear the entry "
            f"switch before the approach"
        )

    # Tail alignment budget. Retract coarse (the tail may be well past the switch), creep
    # forward fine (this sets the final reference point, so its step size IS the gap error).
    TAIL_ALIGN_BACK_MM = 0.4
    TAIL_ALIGN_FWD_MM = 0.2
    TAIL_ALIGN_BACK_BUDGET_MM = 25.0
    TAIL_ALIGN_FWD_BUDGET_MM = 10.0

    def _align_tail_at_entry(self):
        """Park the consumed tail's END exactly on the toolhead_entry trip point.

        The runout fires when the tail clears entry, but it keeps moving for the debounce,
        the deceleration and the pause retract -- so its end sits an UNKNOWN distance below
        the switch. The incoming head stops ON that switch. Referencing both ends to the same
        physical point turns that unknown into the switch's own hysteresis, which is what
        lets the following tandem carry the head into the gears with neither side pushing
        against the other.

        Hot, because the tail is in the melt zone and can only move by melting. Never raises
        on failure to find the switch: an unaligned tail still loads, just with the old
        unknown gap, and refusing the swap outright would be worse.
        """
        self._wait_hot_for_seat()
        # Phase 1: back the tail up until entry sees it again.
        moved = 0.0
        while (not self.manager.get_switch_state(SENSOR_TOOLHEAD)
               and moved < self.TAIL_ALIGN_BACK_BUDGET_MM):
            self._extruder_move(-self.TAIL_ALIGN_BACK_MM, self.extruder_feeding_speed,
                                wait_for_move_end=True)
            moved += self.TAIL_ALIGN_BACK_MM
        if not self.manager.get_switch_state(SENSOR_TOOLHEAD):
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: tail alignment skipped - entry never saw the tail "
                f"again after {moved:.1f}mm of retract. Loading with the old unknown gap."
            )
            return
        # Phase 2: creep forward until it just clears. This is the reference point.
        fwd = 0.0
        while (self.manager.get_switch_state(SENSOR_TOOLHEAD)
               and fwd < self.TAIL_ALIGN_FWD_BUDGET_MM):
            self._extruder_move(self.TAIL_ALIGN_FWD_MM, self.extruder_feeding_speed,
                                wait_for_move_end=True)
            fwd += self.TAIL_ALIGN_FWD_MM
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: tail aligned on the entry switch "
            f"(back {moved:.1f}mm, forward {fwd:.1f}mm) - the incoming head stops on the same "
            f"switch, so the joint gap is now just the switch hysteresis"
        )

    def _feed_filament_into_toolhead(self, tool, check_pre_condition=True):
        """Feed filament from slot to toolhead sensor, then extruder to nozzle."""
        self.wait_ready()
        local_slot = tool - self.tool_offset

        if local_slot < 0 or local_slot >= self.SLOT_COUNT:
            raise ValueError(f"Tool {tool} not managed by this ACE instance.")

        if check_pre_condition:
            has_rdm = self.manager.has_rdm_sensor()

            if has_rdm and self.manager.get_switch_state(SENSOR_RDM):
                raise ValueError("Cannot feed, filament stuck in RMS")

            if self.manager.get_switch_state(SENSOR_TOOLHEAD):
                raise ValueError("Cannot feed, filament in nozzle")

        self._loading_slot = local_slot
        self._stub_load_completed = False
        # Runout-swap signature: entry CLEAR (the tail passed it) with post-gear still
        # TRIGGERED (the stub is in the gears). Reference the tail to the switch before the
        # new head comes down to the same switch.
        if (not check_pre_condition and not self.manager.get_switch_state(SENSOR_TOOLHEAD)
                and self._seat_sensor_triggered()):
            self._align_tail_at_entry()
        if (not check_pre_condition and self.manager.get_switch_state(SENSOR_TOOLHEAD)
                and self._seat_sensor_triggered()):
            self._clear_stub_from_entry()
        try:
            self._feed_to_toolhead_with_extruder_assist(
                local_slot,
                self.toolchange_load_length,
                self.feed_speed,
                self.extruder_feeding_length,
                self.extruder_feeding_speed
            )
        except Exception as e:
            # Perform your custom action here, e.g., log, cleanup, etc.
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Exception during feed to toolhead: {e}, "
                f"retracting filament 150mm back in case it got squished and stuck "
                f"in the filament-hub"
            )
            # The crossing can fail with its feed op still ACTIVE on the device; the
            # recovery retract then gets FORBIDDEN forever (6x on 2026-08-23) while the
            # orphaned feed keeps pushing into a stationary toolhead. Kill it first.
            self._stop_feed(local_slot)
            self.wait_ready()
            self._retract(local_slot, 150, self.retract_speed)

            raise  # Re-raise the original exception
        finally:
            self._loading_slot = -1

        self.state.set(
            "ace_filament_pos",
            FILAMENT_STATE_TOOLHEAD
        )

        # A load with no print behind it has no business in the melt zone: the tip would
        # sit there cooking, and the nozzle would have to be heated for a 45mm push that
        # nothing is waiting on. Stop at post-gear -- the same cold park the unload
        # produces -- and let the first T<n> of a print unpark it (manager's PARKED
        # branch). Mid-print this is skipped: there the melt zone is exactly where the
        # filament has to be.
        self.last_load_parked = (not self.manager._is_printing_or_paused()
                                 or getattr(self.manager, '_force_cold_load', False))
        if self.last_load_parked:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: idle load - parking at post-gear, cold "
                f"(no print running, so nothing needs the melt zone)"
            )
            self.gcode.run_script_from_command("M104 S0")
            self.gcode.run_script_from_command(
                "SAVE_VARIABLE VARIABLE=filament_parked VALUE=1")
            self.gcode.run_script_from_command(
                "SAVE_VARIABLE VARIABLE=filament_loaded_hot VALUE=0")
            return 0.0

        # Stub load: the bite left the joint near the nip with assist now engaged.
        # A short extruder-only push seats the hug - the spring closes any
        # remaining gap as the tail recedes (Simon's 2-4mm "make sure the head
        # is pushing against the tail"). No 90mm seat: the melt zone never
        # emptied, and the stub prints into the part.
        if getattr(self, '_stub_load_completed', False):
            self._stub_load_completed = False
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: hug push - {self.runout_hug_length:.0f}mm "
                f"under assist to press the head against the tail"
            )
            self._extruder_move(self.runout_hug_length, self.extruder_feeding_speed,
                                wait_for_move_end=True)
            self.state.set("ace_filament_pos", FILAMENT_STATE_NOZZLE)
            # 0.0: everything extruded so far was stub/joint material, and the
            # ENDLESS purge path voids the credit anyway.
            return 0.0

        # The melt-zone door: the first push into the hot end, and the only place a load
        # may block for temperature.
        self.gcode.run_script_from_command("ACE_SWAP_PHASE NAME=heat_wait")
        self._wait_hot_for_seat()
        self.gcode.run_script_from_command("ACE_SWAP_PHASE NAME=nozzle")
        self.gcode.run_script_from_command("G92 E0")
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Feeding from sensor to nozzle..."
        )

        self._extruder_push_verified(
            local_slot,
            self.toolhead_full_purge_length,
            self.toolhead_slow_loading_speed,
            what="sensor-to-nozzle fill")

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Feeding from sensor to nozzle "
            f"({self.toolhead_full_purge_length}mm) finished"
        )
        self.state.set(
            "ace_filament_pos", FILAMENT_STATE_NOZZLE
        )

        return self.toolhead_full_purge_length

    def _change_retract_speed(self, slot, retract_speed):
        """
        Request to update active retract speed while unwind command is running.

        Returns:
            bool: True if firmware acknowledged, False otherwise
        """
        logging.info(
            f"ACE[{self.instance_num}]: Requesting retract speed change to "
            f"{retract_speed}mm/s (slot {slot})"
        )

        request = self.protocol.build_update_unwinding_speed_request(slot, retract_speed)
        response_container = {"response": None}

        def callback(response):
            response_container["response"] = response

        try:
            self.send_request(request, callback)
        except Exception as exc:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract speed change request error: {exc}"
            )
            return False

        timeout = time.time() + 3.0
        while response_container["response"] is None and time.time() < timeout:
            self.dwell(0.05)

        response = response_container["response"]
        if not response:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract speed change returned no response"
            )
            return False

        code = response.get("code", -1)
        msg = response.get("msg", "Unknown")
        if code != 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract speed change failed: "
                f"code={code}, msg='{msg}'"
            )
            return False

        return True

    def _change_feed_speed(self, slot, feed_speed):
        """
        Request to update active feed speed while feeding is running.

        Returns:
            bool: True if firmware acknowledged, False otherwise
        """
        logging.info(
            f"ACE[{self.instance_num}]: Requesting feed speed change to "
            f"{feed_speed}mm/s (slot {slot})"
        )

        request = self.protocol.build_update_feeding_speed_request(slot, feed_speed)
        response_container = {"response": None}

        def callback(response):
            response_container["response"] = response

        try:
            self.send_request(request, callback)
        except Exception as exc:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed speed change request error: {exc}"
            )
            return False

        timeout = time.time() + 1.0
        while response_container["response"] is None and time.time() < timeout:
            self.dwell(0.05)

        response = response_container["response"]
        if not response:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed speed change returned no response"
            )
            return False

        code = response.get("code", -1)
        msg = response.get("msg", "Unknown")
        if code != 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed speed change failed: "
                f"code={code}, msg='{msg}'"
            )
            return False

        return True

    def _read_feed_info(self, slot, timeout_s=1.0):
        """The ACE's own measurement of the last operation on a slot, or None.

        THE THIRD WITNESS, AND THE ONLY HONEST ONE FOR A TOOLHEAD-SIDE PUSH. The hub encoder is
        ~934mm upstream with the spring buffer in between and is documented blind under an ACE
        push; the assist counter only proves the motor's control loop is alive, not that
        filament arrived. GET_FEED_INFO returns fields the DEVICE measured: motor_counts,
        magnitude_mm, and moved_mm - and moved_mm is a filament reading, not an echo. A bench
        test moved the motor 299mm / 3699 counts on an EMPTY lane and moved_mm correctly read
        zero. That is exactly the distinction every guard on this machine has been unable to
        make.

        Fully decoded in protocol_ace2.py since the protocol work and never once called. Wired
        here as an OBSERVATION ONLY: nothing branches on it yet, because whether the registers
        update while feed assist (mode 2) is running - as opposed to after a commanded feed - has
        not been established on hardware. Logging it through a few real pushes is what produces
        the baseline this guard has never had. Do not gate anything on it until that data exists.

        Never raises and never blocks for long: a witness that can break the push it observes is
        worse than no witness.
        """
        try:
            build = getattr(self.protocol, "build_get_feed_info_request", None)
            if build is None:
                return None
            box = {"r": None, "done": False}

            def _cb(response):
                box["r"] = response
                box["done"] = True

            self.send_request(build(), _cb)
            deadline = time.time() + timeout_s
            while not box["done"] and time.time() < deadline:
                self.dwell(delay=0.05)
            if not box["done"] or not box["r"]:
                return None
            slots = ((box["r"].get("result") or {}).get("slots") or [])
            return slots[slot] if 0 <= slot < len(slots) else None
        except Exception:
            logging.exception("ace: GET_FEED_INFO read failed (witness only, ignoring)")
            return None

    def feed_filament_with_wait_for_response(self, slot, length, speed):
        """
        Synchronously feed filament and wait for response.

        Returns:
            dict: Response from ACE
        """
        self._ensure_assists_off_for_motion(slot, "feed (sync)")
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() -> slot={slot}, "
            f"length={length}mm, speed={speed}mm/s"
        )

        response_container = {"response": None, "done": False}

        def callback(response):
            response_container["response"] = response
            response_container["done"] = True
            # if response:
            #     code = response.get("code", -1)
            #     msg = response.get("msg", "Unknown")
            #     self.gcode.respond_info(
            #         f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() response -> "
            #         f"code={code}, msg='{msg}'"
            #     )

        request = self.protocol.build_feed_filament_request(slot, length, speed)
        # self.gcode.respond_info(
        #     f"ACE[{self.instance_num}]: Sending sync request: {request}"
        # )

        self.wait_ready()
        self.send_request(request, callback)

        timeout = time.time() + 10.0
        poll_count = 0
        while not response_container["done"] and time.time() < timeout:
            self.dwell(delay=0.2)
            poll_count += 1

        if not response_container["done"]:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() TIMEOUT after "
                f"{poll_count * 0.2:.1f}s - no response received"
            )
            return {"code": -1, "msg": "Feed command timeout - no response"}

        final_response = response_container["response"] or {"code": -1, "msg": "No response"}
        logging.info(
            f"ACE[{self.instance_num}]: feed_filament_with_wait_for_response() completed -> "
            f"slot={slot}, length={length}mm, result_code={final_response.get('code')}"
        )

        return final_response

    def _wait_for_condition(self, condition_fn, timeout_s, poll_interval=0.02):
        """
        Poll condition_fn until it returns True or timeout expires.

        Returns:
            float | None: Time (s) until condition met, or None on timeout
        """
        start = time.time()
        deadline = start + timeout_s

        while time.time() < deadline:
            if condition_fn():
                return time.time() - start
            self.dwell(poll_interval)

        return None

    def rmd_triggered_unload_slot(self, manager, slot, length, overshoot_length,
                                  max_retries=None):
        """Unload slot with RDM sensor monitoring during retraction.

        Monitors the RDM sensor via _retract(early_stop_callback=...) and
        stops retraction as soon as the filament clears, plus overshoot.

        Args:
            manager: AceManager instance (for sensor access)
            slot: Local slot index (0-3)
            length: Maximum retract distance (mm) — safety limit
            overshoot_length: Extra mm to retract after sensor clears
        Returns:
            True if sensor cleared and retract stopped, False on timeout
        """
        if not manager.has_rdm_sensor():
            return False

        f_index = self._get_current_feed_assist_index()
        self._disable_feed_assist(slot)

        # Shared state for the callback
        rdm_state = {
            "cleared": False,
            "clear_time": None,
            "start_time": time.time(),
            "last_log": 0,
        }

        def rdm_early_stop_check():
            """Called every ~200ms from _retract()'s dwell loop.
            Returns a truthy string to trigger early stop, or None to continue."""
            elapsed = time.time() - rdm_state["start_time"]

            rdm_has_filament = manager.get_instant_switch_state(SENSOR_RDM)

            # Log sensor state every 2 seconds
            if elapsed - rdm_state["last_log"] >= 2.0:
                state_str = "TRIGGERED" if rdm_has_filament else "CLEAR"
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: [{elapsed:.1f}s] RDM={state_str}"
                )
                rdm_state["last_log"] = elapsed

            if not rdm_has_filament and not rdm_state["cleared"]:
                rdm_state["cleared"] = True
                rdm_state["clear_time"] = elapsed

                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: RDM CLEAR after {elapsed:.1f}s — "
                    f"applying {overshoot_length}mm overshoot"
                )

                # Overshoot: let the motor run a bit longer before stopping
                overshoot_time = overshoot_length / self.retract_speed
                if overshoot_time > 0:
                    self.reactor.pause(
                        self.reactor.monotonic() + overshoot_time
                    )
                return f"RDM clear at {elapsed:.1f}s"

            return None

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: RDM-triggered unload — "
            f"slot {slot}, max {length}mm @ {self.retract_speed}mm/s, "
            f"overshoot {overshoot_length}mm"
        )

        try:
            result = self._retract(
                slot, length, self.retract_speed,
                early_stop_callback=rdm_early_stop_check,
                max_retries=max_retries,
            )
        except Exception as e:
            self._stop_retract(slot)
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Retract error: {e}"
            )
            self._restore_assist_after_unload(manager, f_index)
            # If the sensor already cleared, the physical unload succeeded
            # even if wait_ready timed out afterward
            return rdm_state["cleared"]

        self._restore_assist_after_unload(manager, f_index)

        # Slot was already empty before retraction started
        if result and "slot empty" in result.get("msg", ""):
            return True

        if rdm_state["cleared"]:
            total = time.time() - rdm_state["start_time"]
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: RMD triggered unload slot {slot} "
                f"completed in {total:.1f}s "
                f"(sensor cleared at {rdm_state['clear_time']:.1f}s)"
            )
            return True
        else:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: RDM triggered unload — "
                f"sensor never cleared during {length}mm retract!"
            )
            return False

    def _restore_assist_after_unload(self, manager, f_index):
        """Restore the feed assist captured before an RDM-monitored retract.

        Standalone unloads (runout recovery, operator unload of a parked
        slot) must give back the assist they took — on ACE1 the single gear
        assembly serves all four slots, so the retract necessarily stole it
        from the printing slot.  During a toolchange the orchestration owns
        assist state: the new tool's load enables its own assist, and
        re-arming the OLD tool's here runs a second assist in parallel
        with the still-loaded previous tool's, which can drive two units
        pushing filament into one toolhead.  Strict `is True` so mock
        managers in tests (whose attributes are truthy Mocks) keep the
        standalone behavior.
        """
        if getattr(manager, "toolchange_in_progress", False) is True:
            if f_index is not None and f_index >= 0:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Not restoring feed assist on "
                    f"slot {f_index} after unload - toolchange in progress "
                    f"owns assist state"
                )
            return
        self._update_feed_assist(f_index)

    def _manager_toolchange_in_progress(self):
        """True only while a real toolchange is running (strict-bool).

        The registry-backed ``manager`` property may be absent or a test
        mock; anything but a literal ``True`` counts as "no toolchange".
        """
        try:
            mgr = self.manager
        except Exception:
            return False
        return getattr(mgr, "toolchange_in_progress", False) is True

    def _smart_unload_slot(self, slot, length=100, on_retract_started=None,
                           max_retries=None):
        """
        Fixed-length retraction with optional sensor validation.

        **SIMPLIFIED MODE:**
        - Always retracts exactly 'length' mm (ignores overshoot_length)
        - No sensor polling during retraction
        - RDM sensor used only for post-retraction validation (if available)

        Args:
            slot: Slot index to retract from
            length: Retraction length in mm (exact distance)
            on_retract_started: Optional callback after retract starts

        Returns:
            bool: True if retraction completed successfully

        Raises:
            ValueError: If path still blocked after retraction (RDM available only)
        """
        has_rdm = self.manager.has_rdm_sensor()

        timeout_seconds = (length / self.retract_speed) * self.timeout_multiplier

        mode_str = "with RDM validation" if has_rdm else "toolhead-only mode"
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Fixed-length unload slot {slot} ({mode_str}):\n"
            f"  Length: {length}mm\n"
            f"  Speed: {self.retract_speed}mm/s\n"
            f"  Timeout: {timeout_seconds:.1f}s"
        )

        try:
            self._disable_feed_assist(slot)

            # Create sensor monitor for toolhead sensor (primary sensor for retraction)
            sensor_monitor = self._make_sensor_trigger_monitor(
                SENSOR_TOOLHEAD
            )

            start_time = time.time()

            # Start retraction with sensor monitoring
            self._retract(
                slot,
                length,
                self.retract_speed,
                on_retract_started,
                on_wait_for_ready=sensor_monitor,
                max_retries=max_retries,
            )

            elapsed_s = time.time() - start_time
            sensor_trigger_time = sensor_monitor.get_timing()
            call_count = sensor_monitor.get_call_count()
            expected_time = length / self.retract_speed

            # Log retraction results with timing data
            if sensor_trigger_time is not None:
                sensor_efficiency = (sensor_trigger_time / expected_time * 100) if expected_time > 0 else 0
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Retraction completed in {elapsed_s:.2f}s "
                    f"(sensor: {sensor_trigger_time:.2f}s, {sensor_efficiency:.1f}% of expected, "
                    f"{call_count} polls)"
                )

                if sensor_efficiency > 90:
                    extra_length = self.parkposition_to_toolhead_length
                    extra_speed = self.retract_speed / 2
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Suspicious late sensor trigger - "
                        f"Retracting extra parkposition_to_toolhead_length to ensure clear path: "
                        f"(Length: {extra_length:.2f}mm, "
                        f"{extra_speed:.1f}mm/s)"
                    )

                    self._retract(
                        slot,
                        extra_length,
                        extra_speed
                    )

            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Retraction completed in {elapsed_s:.2f}s "
                    f"(sensor did not change state, {call_count} polls)"
                )

            # Give klipper time to process any pending state changes and avoid reporting not updated sensor state
            self.dwell(1)
            # Consistency check: Validate with sensors (if RDM available)
            toolhead_clear = not self.manager.get_switch_state(SENSOR_TOOLHEAD)

            if has_rdm:
                rdm_clear = not self.manager.get_switch_state(SENSOR_RDM)
                path_clear = toolhead_clear and rdm_clear

                if path_clear:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: ✓ Path clear (both sensors)"
                    )
                    return True
                else:
                    if not self.rmd_triggered_unload_slot(self.manager, slot, length, self.parkposition_to_rdm_length):
                        slot_status = self.inventory[slot].get("status", "unknown")
                        raise ValueError(
                            "ACE[%d]: ✗ Retraction failed - path still blocked after %.1fmm\n"
                            "  Slot: %d\n"
                            "  Spool status: %s\n"
                            "  Time: %.2fs\n"
                            "  RDM sensor: %s\n"
                            "  Toolhead sensor: %s\n"
                            "  → Manual intervention required"
                            % (
                                self.instance_num,
                                float(length),
                                slot,
                                slot_status,
                                float(elapsed_s),
                                "BLOCKED" if not rdm_clear else "clear",
                                "BLOCKED" if not toolhead_clear else "clear",
                            )
                        )
                    else:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: ✓ RDM-triggered unload succeeded"
                        )
                        return True
            else:
                # No RDM - validate with toolhead sensor only
                if toolhead_clear:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: ✓ Toolhead sensor clear"
                    )
                    return True
                else:
                    self.gcode.respond_info(
                        "ACE[%d]: ⚠ WARNING - Toolhead sensor still triggered after %.1fmm retraction\n"
                        "  This may indicate:\n"
                        "  - Insufficient retraction length\n"
                        "  - Filament stuck in path\n"
                        "  - Sensor malfunction\n"
                        "  Proceeding anyway (no RDM sensor for validation)"
                        % (self.instance_num, float(length))
                    )
                    return False
        except ValueError as e:
            # Validation error - already logged
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Smart unload validation failed: {e}"
            )
            try:
                self._stop_retract(slot)
            except Exception:
                pass
            raise

        except Exception as e:
            # Unexpected error
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Unexpected error during unload: {e}"
            )
            try:
                self._stop_retract(slot)
            except Exception:
                pass
            raise

    RAW_TAG_SENTINEL = 0x0202     # firmware V1.1.3X: "not Anycubic-format, image is cached"
    RAW_TAG_LEN = 144             # pages 4..39

    def _raw_tag_packed(self, reader, op, arg1, arg2=0):
        """The RC522 passthrough's packed request index.

        bit31 marks it as a sub-command so it lands on the stub rather than being treated as a
        slot number - the index >= 4 rejection at 0x0800E7DA is what the stub hooks.
        """
        return (0x80000000 | (reader << 24) | (op << 16)
                | ((arg1 & 0xFF) << 8) | (arg2 & 0xFF))

    def _fetch_raw_tag_image(self, slot_idx):
        """Copy the captured image out of the page buffer. Nothing else. Nothing that writes.

        THE CAPTURE WAS NEVER THE PROBLEM. A paced byte-by-byte audit of the buffer found the
        pristine image still sitting there - and every damaged region mapped to a write made by
        an earlier version of THIS function: bytes 0-1 were its staged READ opcode, 2-21 its
        SELECT scratch, 64-127 its transceive replies. The fetch was vandalising the evidence it
        came to collect, attempt after attempt, and each attempt poisoned the buffer the next
        one read.

        So this version performs no RF operation at all. op 9 is a pure memory read; SELECT,
        staging and transceive are gone, and with them every way this path can corrupt the
        buffer, interfere with a load, or depend on where the tag happens to be.

        THE TAIL IS NOT READ, BECAUSE IT CANNOT BE. The firmware captures pages 4..39 = 144
        bytes while the tag sweeps past the coil mid-preload; by the time the host learns a
        decode was rejected, the spool has carried the tag far out of the field - measured: a
        SELECT at fetch time returns nothing at all. A 177-byte OpenSpool message therefore has
        its last ~33 bytes - the closing brace and the spool identity - physically beyond
        reach on the automatic path. The parser repairs the truncated JSON and yields every
        field the head holds (brand, material, colour, temps); identity comes honestly or not
        at all. Closing that gap needs the FIRMWARE to read more pages at capture time, or tags
        whose payload fits in 144 bytes. It cannot be fixed from here, and pretending otherwise
        cost this machine a full evening.
        """
        # R5 TORN BUFFER. The op-9 walk below takes ~2.88s (144 reads x 0.02s). Hold the slot in
        # _pending_rfid_queries for the WHOLE walk so no host-initiated read (live/cached/refresh)
        # can start mid-walk and splice the buffer; it is released in _apply_raw_tag_image on
        # success and on every failure path here. (image_is_intact in _apply is the backstop
        # against a firmware-side scan, which pending cannot block.)
        self._pending_rfid_queries.add(slot_idx)
        reader = slot_idx // 2
        buf = bytearray(self.RAW_TAG_LEN)

        def read(off):
            def got(response):
                try:
                    buf[off] = int((response or {}).get("code", 0)) & 0xFF
                except (TypeError, ValueError):
                    buf[off] = 0
                try:
                    if off + 1 < self.RAW_TAG_LEN:
                        # A breath between reads. Deferred on the reactor, never slept - this
                        # runs inside a response callback, where blocking stalls all of Klipper
                        # and an exception shuts the printer down (both happened tonight).
                        self.reactor.register_callback(
                            lambda et: read(off + 1), self.reactor.monotonic() + 0.02)
                    else:
                        img = bytes(buf)
                        self.gcode.respond_info(
                            "ACE[%d]: Slot %d captured image | head %s | %s"
                            % (self.instance_num, slot_idx,
                               "".join("%02x" % b for b in img[:16]),
                               "".join(chr(c) if 32 <= c < 127 else "."
                                       for c in img[:64])))
                        self._apply_raw_tag_image(slot_idx, img)
                except Exception:
                    logging.exception("ace: tag fetch chain failed for slot %s", slot_idx)
                    self._pending_rfid_queries.discard(slot_idx)   # R5: never leak the hold
            try:
                self.send_request(self.protocol._build_command_request(
                    "FILAMENT_IDENTIFY",
                    {"index": self._raw_tag_packed(reader, 9, off)}), got)
            except Exception:
                logging.exception("ace: could not send a tag fetch read for slot %s", slot_idx)
                self._pending_rfid_queries.discard(slot_idx)       # R5: never leak the hold

        read(0)

    def _apply_raw_tag_image(self, slot_idx, image):
        """Turn a raw-fetched tag image into inventory render fields and hand identity off.

        R8: this NO LONGER binds a spool locally (no spool_from_record) - the Moonraker resolver is
        the single identity authority. It still stores material/colour/brand for OFFLINE display,
        gates the heater per R1/R3 (temp only from a recognised real format with a plausible
        value), refuses a torn buffer per R5, and refuses a paired-antenna collision per R2.
        Never raises into Klipper.
        """
        # The op-9 walk is finished; release the R5 hold so a fresh read may run later.
        self._pending_rfid_queries.discard(slot_idx)
        try:
            from . import ace_tag_formats as fmts
        except ImportError:
            try:
                import ace_tag_formats as fmts
            except ImportError:
                logging.warning("ace: ace_tag_formats not installed; raw tag image dropped")
                return
        try:
            rec = fmts.parse(image)
        except Exception:
            logging.exception("ace: could not parse raw tag image for slot %s", slot_idx)
            return

        fmt = rec.get("format")

        # BUFFER-CONTAMINATION GUARD. The raw fetch path is reached ONLY for a NON-Anycubic
        # tag (an Anycubic tag decodes natively in firmware and never triggers a fetch). An
        # Anycubic-format image here therefore cannot be THIS lane's own tag - it is a
        # neighbour's Anycubic tag left in the shared page buffer 0x20000704 and read back
        # by op-9. Observed: slot 3, on the OTHER physical reader, returned slot 0's SM22 -
        # impossible over RF, so it is the buffer. Reject; never bind a neighbour's identity.
        if fmt == "anycubic":
            self.gcode.respond_info(
                "ACE[%d]: Slot %d raw fetch returned an Anycubic image (%r) - only a "
                "NON-Anycubic tag reaches this path, so this is a neighbour's tag in the "
                "shared buffer, not this lane's. Ignoring."
                % (self.instance_num, slot_idx, rec.get("sku")))
            logging.warning("ace: slot %s raw fetch buffer-contaminated (anycubic sku %r) "
                            "- dropped", slot_idx, rec.get("sku"))
            return

        # R5 TORN BUFFER. The op-9 walk takes ~2.88s and a firmware-side scan can splice it. Ask
        # the pure module whether the image is structurally intact; on a torn buffer degrade to
        # scenario 3 (render what parsed, never bind) rather than trust spliced identity bytes.
        intact = True
        checker = getattr(fmts, "image_is_intact", None) or getattr(fmts, "ndef_is_intact", None)
        if callable(checker):
            try:
                intact = bool(checker(image))
            except Exception:
                logging.exception("ace: image_is_intact check failed for slot %s", slot_idx)
                intact = True   # a broken checker must not block a legitimate render

        # R3: recover the tag UID from the image (4- or 7-byte layout) for scenario-2 resolution.
        try:
            # N1 (QA): the raw image is pages 4..39, NOT page 0, so the UID is not in it.
            # first_page=4 makes uid_from_image return "" instead of misreading page-4
            # bytes as a page-0 UID (~1/128 would emit a bogus one). The 7-byte NTAG UID
            # only becomes available with the deferred firmware stash.
            uid = _normalise_uid_local(fmts.uid_from_image(image, first_page=4) or "")
        except Exception:
            logging.exception("ace: uid_from_image failed for slot %s", slot_idx)
            uid = ""

        # R1/R3 HEATER GATE. Only a recognised real format with a plausible value may seed temp.
        temp_to_store = raw_temp_to_store(fmt, rec.get("temp_min"), rec.get("temp_max"))

        self.gcode.respond_info(
            "ACE[%d]: Slot %d tag is %s format (%s) - sku=%r material=%r colour=%r uid=%r%s%s"
            % (self.instance_num, slot_idx, fmt, rec.get("why"),
               rec.get("sku"), rec.get("material"), rec.get("color"), uid,
               "" if intact else " [TORN IMAGE - render only]",
               (" temp=%d" % temp_to_store) if temp_to_store is not None else " (no heat)"))

        if 0 <= slot_idx < self.SLOT_COUNT:
            inv = self.inventory[slot_idx]
            # A partial record still renders the lane with the backend unreachable, which is the
            # point of reading the tag. Never overwrite a good value with None. Identity binding is
            # NOT decided here (R8) - only render fields, and temp only when the gate above allowed.
            if rec.get("material"):
                inv["material"] = rec["material"]
            if rec.get("brand"):
                inv["brand"] = rec["brand"]
            if rec.get("color"):
                inv["color"] = rec["color"]
            if rec.get("sku"):
                inv["sku"] = str(rec["sku"])
            if temp_to_store is not None:
                inv["temp"] = int(temp_to_store)
            inv["tag_format"] = fmt
            try:
                self.manager._sync_inventory_to_persistent()
            except Exception:
                logging.exception("ace: could not persist raw tag inventory")

        if not intact:
            self.gcode.respond_info(
                "ACE[%d]: Slot %d raw image was torn (a background scan spliced the ~2.88s read) "
                "- rendered from the tag but NOT binding identity. Re-present the spool to retry, "
                "or assign with MMU_GATE_MAP GATE=%d SPOOLID=<n>."
                % (self.instance_num, slot_idx, slot_idx))
            return

        # R2 paired-antenna guard, only meaningful when a UID was actually recovered.
        if uid and not self._anticollision_clear(slot_idx, uid):
            return

        # R7/R8 HANDOFF. The resolver decides the spool id against the CONFIRMED backend (sku
        # scenario 1, uid scenario 2, or scenario-3 render) - not this path.
        rec_uid = dict(rec)
        rec_uid["uid"] = uid
        self._handoff_to_moonraker(slot_idx, rec_uid, uid=uid, native=False)

    # ------------------------------------------------------------------ #
    # Lock-in shared-reader identification. See SHARED_READER_ARBITRATION.md #
    # ------------------------------------------------------------------ #
    def _identify_read_sync(self, slot, deadline_s=1.5):
        """Issue a live cmd-68 FILAMENT_IDENTIFY and block cooperatively for the reply.

        Returns (code, response). code 0 = a tag is in the coil now (response['result'] carries
        sku/version/index); 3 = SELECT failed (nothing in the field); 4/6 = anticollision /
        read-refused. COMMAND CONTEXT ONLY - the reactor.pause loop must never run inside a
        response callback.
        """
        rc = {"resp": None, "done": False}
        def _cb(response, rc=rc):
            rc["resp"] = response
            rc["done"] = True
        try:
            req = self.protocol._build_command_request("FILAMENT_IDENTIFY", {"index": slot})
            self.send_request(req, _cb)
        except Exception:
            logging.exception("ace: identify read send failed for slot %s", slot)
            return -1, None
        end = self.reactor.monotonic() + deadline_s
        while not rc["done"] and self.reactor.monotonic() < end:
            self.reactor.pause(self.reactor.monotonic() + 0.05)
        resp = rc["resp"]
        try:
            code = int((resp or {}).get("code", -1))
        except (TypeError, ValueError):
            code = -1
        return code, resp

    @staticmethod
    def _read_tag_key(resp):
        """A stable identity for the tag a cmd-68 read returned, or None. Distinguishes a tag
        that appeared BECAUSE of a jog (correlated) from a static parked-sibling tag."""
        try:
            result = (resp or {}).get("result") or {}
            sku = result.get("sku")
            if sku:
                return "sku:%s" % str(sku).strip()
            ver = result.get("version")
            if ver is not None:
                return "ver:%s" % ver
        except Exception:
            pass
        return None

    def _hub_is_blocked(self):
        """True if the shared hub sensor reports filament present - do not jog forward into it."""
        try:
            s = self.printer.lookup_object("filament_switch_sensor hub_detect", None)
            if s is None:
                return False
            return bool(getattr(getattr(s, "runout_helper", None), "filament_present", False))
        except Exception:
            return False

    def _note_staged_delta(self, slot, delta_mm):
        """Tell the preload guard about motion that bypasses its ACE_FEED/ACE_RETRACT wrappers.

        identify jogs the lane through the driver directly, so the guard's _track never sees it and
        its staged counter keeps the pre-jog value. That is not cosmetic: the counter is what sizes
        the NEXT backward move, so a stale-high value asks for hundreds of mm of retract against a
        lane that has only its grip margin left - i.e. it pulls the tail out of the gears. Update it
        per accepted chunk so an abort part-way still leaves the counter true.
        """
        try:
            guard = self.printer.lookup_object("ace_preload_guard", None)
            if guard is None:
                return
            guard._staged[slot] = max(0., guard._staged.get(slot, 0.) + delta_mm)
            guard._persist_staged()
        except Exception:
            logging.exception("ace: staged-counter update failed for slot %s", slot)

    def _repark_after_identify(self, slot, moved_mm):
        """Return the lane to its sensor-defined park datum once identify is done with it.

        Identify deliberately drives the lane off its datum, so stopping there would leave the
        operator to finish the job by hand, leave ace_lane_pos claiming "parked" when it is not,
        and leave the next move sizing itself against a lane that is not where the flag says. This
        runs from the finally, so an abort or an exception re-parks too - the routine never exits
        having left the lane somewhere undefined.

        Calling ACE_LANE_NORMALIZE directly from here does NOT work, and the failure is subtle:
        the lane's status comes from a 1Hz heartbeat, so in the same tenth of a second the last
        retract chunk ends the lane still reads "unwinding" and normalize refuses on its readiness
        gate. Nothing is wrong with the path - we simply asked too early. So hand it to the guard's
        normalize QUEUE instead, which drains on its own poll once the lane really is ready and the
        shared path is clear, and which keeps the entry across a restart.

        This matters more than tidiness: identify only ever jogs a lane FURTHER from park, so a
        re-park that silently fails means every run walks the lane backward and the next run has
        less room, until it reaches the grip reserve and identify can no longer move at all.
        """
        if moved_mm <= 0.:
            return          # never moved; already at its datum
        try:
            guard = self.printer.lookup_object("ace_preload_guard", None)
            if guard is not None:
                guard.queue_normalize(slot, front=True,
                                      reason="identify jogged the lane off park")
                return
        except Exception:
            logging.exception("ace: could not queue a normalize for slot %s", slot)
        self.gcode.respond_info(
            "ACE[%d]: slot %d is ~%dmm off park and no path guard is loaded to re-park it - "
            "run ACE_LANE_NORMALIZE T=%d" % (self.instance_num, slot, int(moved_mm), slot))

    def identify_by_jog(self, slot, budget_mm=None, feed_speed=12.0, gcmd=None):
        """Lock-in identification for a shared-reader lane (SHARED_READER_ARBITRATION.md).

        ONE continuous forward feed (not rapid stop-start jogs, which the ACE FORBIDs) while
        polling a synchronous cmd-68 (V1.1.40+ injects sm_id on that path, so an OpenSpool read is
        clean, not torn). A read (code 0) whose identity differs from the pre-feed baseline appeared
        BECAUSE of our rotation -> this lane's; it goes to the normal decode path (R2 / Fix-2 /
        anticollision / plausibility + moonraker handoff). A read matching the baseline is the
        static parked sibling -> filtered. The forward distance is capped up front to
        (hub - staged - margin) so the tip can NEVER reach the hub. COMMAND CONTEXT ONLY.
        """
        reader = slot // 2
        owner = self._reader_id_owner.get(reader, -1)
        if owner not in (-1, slot):
            return "reader %d busy identifying slot %d - retry after it finishes" % (reader, owner)

        # NEVER JOG A LOADED LANE. This routine drives the ACE backward while the extruder is
        # idle - and an idle extruder is a CLAMPED one. That is the 2026-08-27 tandem failure in
        # mirror image (there the extruder pulled against a clamped ACE; here the ACE pulls
        # against a clamped extruder), and bwd_room for a loaded lane is most of the bowden.
        # _retract_async_verified guards the other direction of this hazard and has no idea about
        # this one: its _is_slot_empty() reads the ACE LANE sensor, not the toolhead.
        #
        # Fail CLOSED. An unreadable index or a missing sensor means we cannot rule out a loaded
        # lane, and "probably not loaded" is not good enough to unwind a metre of filament on.
        _mgr = getattr(self, "manager", None)
        try:
            _cur = int(_mgr.state.get("ace_current_index", -1))
        except Exception:
            return ("cannot read the loaded-tool index - refusing to jog slot %d. Identify will "
                    "not move a lane it cannot prove is unloaded." % slot)
        if _cur == slot:
            return ("slot %d IS the loaded tool - refusing to jog it. The extruder is gripping "
                    "this strand; unwinding the ACE against it is how a toolchange was killed on "
                    "2026-08-27. Unload the tool first, then identify." % slot)
        for _name in ("toolhead_entry", "toolhead_postgear"):
            _sens = self.printer.lookup_object("filament_switch_sensor " + _name, None)
            if _sens is None:
                return ("%s is not configured - refusing to jog slot %d, because a loaded lane "
                        "cannot be ruled out without it" % (_name, slot))
            try:
                _present = bool(_sens.runout_helper.filament_present)
            except Exception:
                return ("cannot read %s - refusing to jog slot %d" % (_name, slot))
            if _present:
                return ("%s still reads filament - refusing to jog slot %d until the toolhead is "
                        "clear (something is loaded, even if the index disagrees)"
                        % (_name, slot))

        def _say(msg):
            (gcmd.respond_info if gcmd is not None else self.gcode.respond_info)(msg)

        # Pick the jog DIRECTION with the most rotation room, then cap it. Reading needs ~a full
        # spool rotation for the tag to sweep the coil; forward room ends at the hub (buckle risk),
        # so a lane near the hub is jogged BACKWARD toward park instead - more room, and a retract
        # can never buckle. Derived from the persisted geometry.
        fwd_room = None
        bwd_room = None
        try:
            sv = self.printer.lookup_object("save_variables", None)
            vals = getattr(sv, "allVariables", {}) if sv is not None else {}
            staged_list = vals.get("ace_staged_mm") or []
            if slot < len(staged_list):
                staged = float(staged_list[slot])
                hub = (float(vals.get("ace_park_to_entry", 0))
                       - float(vals.get("ace_cal_hub_to_entry", 0)))
                if hub > 0:
                    fwd_room = max(0.0, hub - staged - 90.0)   # to the hub, less a buckle margin
                bwd_room = max(0.0, staged - 60.0)             # to park, keep it gripped
        except Exception:
            logging.exception("ace: identify room calc failed for slot %s", slot)
        if bwd_room is not None and (fwd_room is None or bwd_room > fwd_room):
            direction, room, dirname = -1, bwd_room, "backward"
        else:
            direction, room, dirname = 1, (fwd_room if fwd_room is not None else 300.0), "forward"
        budget = room if budget_mm is None else min(budget_mm, room)
        if budget < 16.0:
            return ("slot %d has no room to jog (fwd %s / bwd %s mm) - reposition it first"
                    % (slot, ("%.0f" % fwd_room) if fwd_room is not None else "?",
                       ("%.0f" % bwd_room) if bwd_room is not None else "?"))
        if direction > 0 and self._hub_is_blocked():
            return ("hub already blocked - retract the lane off the hub before identifying slot %d"
                    % slot)

        self._reader_id_owner[reader] = slot
        _prev_sync = self.rfid_inventory_sync_enabled
        self.rfid_inventory_sync_enabled = False   # background sync tears identify reads - pause it
        fed = 0.0          # declared out here so the finally's re-park knows how far we drove
        try:
            self._ensure_assists_off_for_motion(slot, "identify")
            base_code, base_resp = self._identify_read_sync(slot)
            baseline_key = self._read_tag_key(base_resp) if base_code == 0 else None
            if baseline_key:
                _say("ACE[%d]: reader %d baseline holds a STATIC tag (%s) - filtering it as the "
                     "parked sibling" % (self.instance_num, reader, baseline_key))
            CHUNK = 40.0
            _say("ACE[%d]: identifying slot %d - discrete %.0f mm %s jogs, reading while still "
                 "(the ACE cannot read mid-motion), up to %.0f mm"
                 % (self.instance_num, slot, CHUNK, dirname, budget))
            saw_any = False
            while fed < budget:
                if direction > 0 and self._hub_is_blocked():
                    _say("ACE[%d]: hub reached at %d mm - stopping; slot %d unbound"
                         % (self.instance_num, int(fed), slot))
                    break
                chunk = min(CHUNK, budget - fed)
                moved = True
                if direction > 0:
                    self._feed(slot, chunk, feed_speed)
                else:
                    try:
                        # No STOP is ever issued here (each jog ends on its own length), so
                        # the post-STOP backoff does not apply - measured, it was 31 refusals
                        # x 3s = 94s of a 642s run. quiet: these refusals are expected and
                        # would otherwise drown the result.
                        self._retract_async_verified(slot, chunk, feed_speed,
                                                     refused_pause=0.3, quiet=True)
                    except Exception:
                        moved = False   # refused after every retry - do not bill it to the budget
                        logging.exception("ace: identify backward chunk refused, slot %s", slot)
                if moved:
                    fed += chunk
                    self._note_staged_delta(slot, chunk * direction)
                # Let this feed FINISH and the ACE settle before reading: it cannot service a
                # cmd-68 while feeding (the read times out), so dwell past the feed's own duration,
                # THEN read while the lane is still. Each _feed stops on its own length (no STOP,
                # so no post-STOP refusal window).
                self.reactor.pause(self.reactor.monotonic() + (chunk / max(1.0, feed_speed)) + 1.5)
                code, resp = self._identify_read_sync(slot)
                if code != 0:
                    continue
                saw_any = True
                key = self._read_tag_key(resp)
                if key is not None and key == baseline_key:
                    continue  # static parked-sibling tag
                before = self.inventory[slot].get("sku") if 0 <= slot < self.SLOT_COUNT else None
                self._handle_rfid_info_response(slot, resp)
                inv = self.inventory[slot] if 0 <= slot < self.SLOT_COUNT else {}
                if inv.get("rfid") and inv.get("sku"):
                    # SUCCESS = this jog produced a valid identity. The old test required the sku
                    # to CHANGE, so a lane already bound to the right spool read back the same
                    # value and a perfect injected read was reported as "unidentified".
                    verb = "identified" if inv.get("sku") != before else "confirmed"
                    return "%s slot %d after %d mm: sku=%s" % (verb, slot, int(fed), inv.get("sku"))
            if not saw_any:
                return ("slot %d: no tag reached the coil in %d mm of jogging (reads timing out?) "
                        "- left unbound" % (slot, int(fed)))
            return ("slot %d unidentified after %d mm - bind manually: "
                    "MMU_GATE_MAP GATE=%d SPOOLID=<n>" % (slot, int(fed), slot))
        finally:
            self.rfid_inventory_sync_enabled = _prev_sync
            self._reader_id_owner[reader] = -1
            self._repark_after_identify(slot, fed)

    def _live_read_then_cache(self, slot_idx):
        """Try a LIVE tag read first, and only fall back to the cached record.

        GET_FILAMENT_INFO (cmd 13) is served by the firmware's cache handler at 0x0800E910 and
        never touches the tag. FILAMENT_IDENTIFY (cmd 68, 0x0800E7A8) is the command that
        actually selects the card and reads its pages - and it is the one V1.1.3X's raw-tag hook
        sits on, so it is the only route by which a non-Anycubic tag can be decoded at all.

        The catch is that a tag is only in the antenna's field while the spool turns, so a cmd 68
        issued at an arbitrary moment answers code 3 (SELECT failed). THIS is the right moment:
        the caller has just seen the RFID-detected edge, which means the preload search stopped
        BECAUSE the tag reached the coil. Anywhere else the read fails; here it should succeed.

        The cache is worth falling back to rather than abandoning: it survives a failed read, and
        for an Anycubic tag it holds a perfectly good decode. It also outlives the spool - the
        firmware does not clear it on eject - which is why a stale record must never be preferred
        to a live read.

        Defensive throughout. This runs inside a status callback, and an exception here took
        Klipper down once already; nothing in this path may raise.
        """
        try:
            if slot_idx in self._pending_rfid_queries:
                return
            self._pending_rfid_queries.add(slot_idx)

            def after_live(response):
                try:
                    code = int((response or {}).get("code", -1))
                except (TypeError, ValueError):
                    code = -1
                self._pending_rfid_queries.discard(slot_idx)
                if code == 0:
                    self._handle_rfid_info_response(slot_idx, response)
                    return
                # 3 = SELECT failed (tag not in the field), 4 = anticollision,
                # 6 = read refused (a MIFARE/Bambu tag). None of these are faults here.
                self.gcode.respond_info(
                    "ACE[%d]: Slot %d live tag read returned code %s - using the cached record"
                    % (self.instance_num, slot_idx, code))
                self._query_rfid_full_data(slot_idx)

            req = self.protocol._build_command_request(
                "FILAMENT_IDENTIFY", {"index": slot_idx})
            self.send_request(req, after_live)
        except Exception:
            logging.exception("ace: live tag read failed for slot %s", slot_idx)
            self._pending_rfid_queries.discard(slot_idx)
            try:
                self._query_rfid_full_data(slot_idx)
            except Exception:
                logging.exception("ace: cached fallback also failed for slot %s", slot_idx)

    def _query_rfid_full_data(self, slot_idx):
        """
        Query full RFID tag data via get_filament_info.

        This gets the complete RFID data including:
        - extruder_temp: {min, max}
        - hotbed_temp: {min, max}
        - colors (RGBA array)
        - icon_type
        - diameter, total, current
        """
        # Prevent duplicate queries while one is in-flight
        if slot_idx in self._pending_rfid_queries:
            return
        self._pending_rfid_queries.add(slot_idx)

        def rfid_callback(response):
            self._handle_rfid_info_response(slot_idx, response)

        request = self.protocol.build_get_filament_info_request(slot_idx)
        self.send_request(request, rfid_callback)

    def start_drying(self, temperature, duration, callback=None):
        """Start the ACE dryer using the active protocol adapter."""
        self._dryer_active = True
        self._dryer_temperature = temperature
        self._dryer_duration = duration

        request = self.protocol.build_start_drying_request(temperature, duration)
        self.send_request(request, callback or (lambda response: None))

    def stop_drying(self, callback=None):
        """Stop the ACE dryer using the active protocol adapter."""
        self._dryer_active = False
        self._dryer_temperature = 0
        self._dryer_duration = 0

        request = self.protocol.build_stop_drying_request()
        self.send_request(request, callback or (lambda response: None))

    def _emit_inventory_update(self):
        """Emit JSON inventory update for KlipperScreen."""
        try:
            slots_out = []
            for i in range(self.SLOT_COUNT):
                inv = self.inventory[i]
                slot_data = {
                    "status": inv.get("status"),
                    "color": inv.get("color"),
                    "material": inv.get("material"),
                    "temp": inv.get("temp"),
                    "rfid": inv.get("rfid", False),
                }
                # Include optional RFID metadata fields if present
                for key in [
                    "sku",
                    "brand",
                    "icon_type",
                    "rgba",
                    "extruder_temp",
                    "hotbed_temp",
                    "diameter",
                    "total",
                        "current"]:
                    if key in inv:
                        slot_data[key] = inv[key]
                slots_out.append(slot_data)
            self.gcode.respond_info("// " + json.dumps({"instance": self.instance_num, "slots": slots_out}))
        except Exception:
            pass

    def _status_update_callback(self, response):
        """Handle status updates from ACE hardware."""
        inventory_changed = False
        feed_assist_was_active = self._feed_assist_index
        filament_loaded = False

        # LOG RAW status RESPONSE (only slots data for readability)
        # if response and "result" in response:
        #     slots_data = response.get("result", {}).get("slots", [])
        #     if slots_data:
        #         for slot in slots_data:
        #             idx = slot.get("index")
        #             rfid_val = slot.get("rfid")
        #             status_val = slot.get("status")
        #             self.gcode.respond_info(
        #                 f"ACE[{self.instance_num}]: Slot {idx} status RAW -> rfid={rfid_val}, status={status_val}"
        #             )

        if response and "result" in response:
            self._info = response["result"]
            try:
                self._info_at = self.reactor.monotonic()
            except Exception:
                pass
            # _info now reflects real device state (the initialized default
            # reports every slot 'empty') — gates skipping actions on
            # device-reported emptiness may trust it from here on.
            self._device_status_seen = True

            # Handle pending RFID refresh after reconnect
            if self._pending_rfid_refresh:
                self._pending_rfid_refresh = False
                if self.rfid_inventory_sync_enabled:
                    # Query slots sequentially to avoid post-reconnect request bursts
                    # that can push responses beyond timeout and look unsolicited.
                    self._pending_rfid_refresh_slots = list(range(self.SLOT_COUNT))

            if (
                self.rfid_inventory_sync_enabled
                and self._pending_rfid_refresh_slots
                and not self._pending_rfid_queries
            ):
                slot_idx = self._pending_rfid_refresh_slots.pop(0)
                logging.info(
                    f"ACE[{self.instance_num}]: Reconnect - querying RFID data for slot {slot_idx}"
                )
                self._query_rfid_full_data(slot_idx)

            slots = self._info.get("slots", [])
            for slot in slots:
                idx = slot.get("index")
                if idx is not None and 0 <= idx < self.SLOT_COUNT:
                    # Get saved metadata (material/color/temp)
                    saved_color = self.inventory[idx].get("color", [0, 0, 0])
                    saved_material = self.inventory[idx].get("material", "")
                    saved_temp = self.inventory[idx].get("temp", 0)
                    saved_rfid = self.inventory[idx].get("rfid", False)

                    updated_color = saved_color
                    updated_material = saved_material
                    updated_temp = saved_temp
                    updated_rfid = None  # Will be set based on transition or status

                    # Get current states
                    old_status = self.inventory[idx].get("status")
                    new_status = normalize_ace_slot_state(
                        slot.get("status"),
                        default=AceSlotStateMachineState.EMPTY.value,
                    )

                    # Detect state changes
                    if old_status != new_status:
                        inventory_changed = True

                        # ANY slot reaching ready means a (pre)load cycle just
                        # finished - the ACE hardware disables feed assist
                        # while gearing another slot. The 1 Hz heartbeat
                        # usually samples the intermediate preload/identifying
                        # states, so the empty→ready case below almost never
                        # matches directly (inserting a spool into T0 mid-print
                        # silently kills feed assist on the printing T1).
                        # Trigger the restore on any transition into ready
                        # instead.
                        if new_status == AceSlotStateMachineState.READY.value:
                            filament_loaded = True

                        # Log the state transition
                        if (
                            old_status == AceSlotStateMachineState.EMPTY.value
                            and new_status == AceSlotStateMachineState.READY.value
                        ):
                            # Check if the new spool is non-RFID (RFID_STATE_NO_INFO = 0)
                            rfid_state = slot.get("rfid")
                            if rfid_state == RFID_STATE_NO_INFO and saved_rfid:
                                # RFID → non-RFID transition: clear RFID-specific fields only
                                # (User may have replaced empty RFID spool with matching non-RFID spool)
                                # Keep material/color/temp for print continuity
                                updated_rfid = False
                                # Clear all optional RFID-specific fields from inventory
                                for key in [
                                    "sku",
                                    "brand",
                                    "icon_type",
                                    "extruder_temp",
                                    "hotbed_temp",
                                    "diameter",
                                    "total",
                                        "current"]:
                                    self.inventory[idx].pop(key, None)
                                # Clear query state
                                self._pending_rfid_queries.discard(idx)
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} RFID→non-RFID swap detected -> "
                                    f"empty → ready (clearing RFID fields, preserving material={saved_material})"
                                )
                            else:
                                # RFID spool (identifying/identified) OR same non-RFID refilled: preserve metadata
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} auto-restored: "
                                    f"empty → ready (material={saved_material})"
                                )

                        elif (
                            old_status == AceSlotStateMachineState.READY.value
                            and new_status == AceSlotStateMachineState.EMPTY.value
                        ):
                            self.gcode.respond_info(
                                f"ACE[{self.instance_num}]: Slot {idx} marked empty "
                                f"(was: material={saved_material})"
                            )

                    # If slot is empty, clear RFID marker and metadata
                    if new_status == AceSlotStateMachineState.EMPTY.value:
                        updated_rfid = False
                        # Clear all optional RFID fields
                        for key in [
                            "sku",
                            "brand",
                            "icon_type",
                            "extruder_temp",
                            "hotbed_temp",
                            "diameter",
                            "total",
                                "current"]:
                            self.inventory[idx].pop(key, None)
                        self._pending_rfid_queries.discard(idx)

                        # If NOT printing/paused, also clear material/color/temp to defaults
                        # (For endless spool/runout during printing, we preserve this info)
                        if not self._is_printing_or_paused():
                            updated_material = ""
                            updated_color = [0, 0, 0]
                            updated_temp = 0
                            inventory_changed = True  # Force persistence update

                        # Authoritative check: if this slot is recorded as the
                        # active/loaded tool (ace_current_index) but the ACE
                        # hardware itself reports it empty, the persisted
                        # state is stale - clear it. Catches cases the
                        # sensor-only startup validation can miss entirely
                        # (e.g. wrong physical unit bound to this instance).
                        if self.manager:
                            self.manager.reconcile_stale_current_index(
                                self.tool_offset + idx,
                                reason=(
                                    f"ACE[{self.instance_num}] slot {idx} "
                                    f"status update"
                                ),
                            )

                    # Handle RFID tag detection - only query get_filament_info, don't use status metadata
                    elif new_status == AceSlotStateMachineState.READY.value:
                        rfid_state = slot.get("rfid")

                        # When RFID sync enabled: only use data from get_filament_info callback
                        # Don't read material/color/sku from get_status - it may be stale
                        if self.rfid_inventory_sync_enabled and rfid_state == RFID_STATE_IDENTIFIED:
                            # Query on RFID state transition (false → detected)
                            # Skip if query is already in-flight
                            query_pending = idx in self._pending_rfid_queries

                            if not saved_rfid and not query_pending \
                                    and self._reader_id_owner.get(idx // 2, -1) == -1:
                                updated_rfid = True
                                # Query get_filament_info - callback will populate all metadata
                                self._live_read_then_cache(idx)
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} RFID detected -> "
                                    f"reading the tag (it is in the coil right now)..."
                                )
                                inventory_changed = True
                            else:
                                # RFID already detected - keep existing data
                                if updated_rfid is None:
                                    updated_rfid = saved_rfid
                        else:
                            # RFID sync disabled or no RFID: keep inventory as source of truth
                            if updated_rfid is None:
                                updated_rfid = saved_rfid

                        # If still missing metadata, fall back to defaults for ready slots
                        missing_material = not updated_material.strip()
                        missing_temp = updated_temp <= 0
                        missing_color = (
                            not updated_color
                            or len(updated_color) < 3
                            or all(c == 0 for c in updated_color[:3])
                        )

                        # When RFID sync is disabled AND the slot reports RFID data (rfid_state != RFID_STATE_NO_INFO),
                        # do not auto-fill defaults; leave values as-is to satisfy "ignore RFID data".
                        allow_default_fill = not (
                            not self.rfid_inventory_sync_enabled and rfid_state not in (
                                None, RFID_STATE_NO_INFO))

                        # Never overwrite with defaults while an RFID query is in-flight;
                        # the callback will populate the real data when it arrives.
                        rfid_query_in_flight = idx in self._pending_rfid_queries

                        if allow_default_fill and not rfid_query_in_flight and (missing_material or missing_temp):
                            # Check if we're actually changing anything before setting inventory_changed
                            needs_update = False
                            if missing_material and updated_material != self.DEFAULT_MATERIAL:
                                updated_material = self.DEFAULT_MATERIAL
                                needs_update = True
                            elif missing_material:
                                updated_material = self.DEFAULT_MATERIAL

                            if missing_temp and updated_temp != self.DEFAULT_TEMP:
                                updated_temp = self.DEFAULT_TEMP
                                needs_update = True
                            elif missing_temp:
                                updated_temp = self.DEFAULT_TEMP

                            if missing_color and updated_color != self.DEFAULT_COLOR:
                                updated_color = list(self.DEFAULT_COLOR)
                                needs_update = True
                            elif missing_color:
                                updated_color = list(self.DEFAULT_COLOR)

                            if needs_update:
                                inventory_changed = True
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Slot {idx} ready with no metadata -> "
                                    f"defaulting to {updated_material} {updated_temp}C, color {updated_color}"
                                )
                    else:
                        # Don't overwrite updated_rfid if it was already set
                        if updated_rfid is None:
                            updated_rfid = saved_rfid

                    # Final safety check: ensure updated_rfid has a value
                    if updated_rfid is None:
                        updated_rfid = saved_rfid

                    # Only write to inventory if values actually changed
                    inv = self.inventory[idx]
                    if (inv.get("status") != new_status or
                        inv.get("color") != updated_color or
                        inv.get("material") != updated_material or
                        inv.get("temp") != updated_temp or
                            inv.get("rfid") != updated_rfid):
                        inv["status"] = new_status
                        inv["color"] = updated_color
                        inv["material"] = updated_material
                        inv["temp"] = updated_temp
                        inv["rfid"] = updated_rfid

        # Persist changes if any status changed (deferred; flushed at print end)
        if inventory_changed:
            if self.manager:
                self.manager._sync_inventory_to_persistent(self.instance_num, flush=False)

            # Emit inventory update for KlipperScreen
            # self._emit_inventory_update()

            # Restore feed assist if it was active before filament loading
            # (ACE hardware disables feed assist when loading filament on any
            # slot). Queue it via the retrying pending-restore path instead of
            # enabling directly: the slot reports ready BEFORE the preload
            # cycle really finishes, and ACE1 units can watchdog-reset their
            # USB during preload (Errno 5 write errors) - the queue waits for
            # a genuinely ready device and survives a reconnect in between.
            if filament_loaded and feed_assist_was_active >= 0:
                # ACE2 is excluded here on purpose: its work status is 'busy'
                # the whole time assist is active, so if assist survived the
                # slot load, a queued restore would retry wait_ready (5s
                # timeout each) against a busy-by-design device for the full
                # attempt budget. _reconcile_feed_assist_state() is the
                # authoritative ACE2 detector - it queues only when the
                # device provably dropped assist (status 'ready').  ACE1
                # stays 'ready' during assist and needs this event path.
                try:
                    ace2_like = self.protocol.feed_assist_causes_busy()
                except Exception:
                    ace2_like = False
                if not ace2_like and self._pending_feed_assist_restore < 0:
                    if self._manager_toolchange_in_progress():
                        # The toolchange owns assist state (it enables the
                        # new tool's assist after load) - a slot-ready
                        # restore here re-arms the OLD tool's assist in
                        # parallel.  Also silences the restore churn from a
                        # spool end flapping at the slot sensor mid-swap.
                        logging.info(
                            f"ACE[{self.instance_num}]: Slot-ready assist "
                            f"restore for slot {feed_assist_was_active} "
                            f"skipped - toolchange in progress"
                        )
                    else:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: Queueing feed assist restore on "
                            f"slot {feed_assist_was_active} after automatic disable "
                            f"during slot loading"
                        )
                        self._pending_feed_assist_restore = feed_assist_was_active
                        self._feed_assist_restore_attempts = 0

    def _on_heartbeat_response(self, response):
        """Handle heartbeat response."""
        if response is None:
            self._record_status_failure("no response")
            return

        if response.get("code") == 0 and "result" in response:
            self._reset_status_failure_tracking()
            self._status_update_callback(response)

            # Detect device-side feed assist loss (ACE2) and queue a restore
            self._reconcile_feed_assist_state()

            # Restore pending feed assist after first successful heartbeat
            self._maybe_restore_pending_feed_assist()
        else:
            msg = response.get("msg", "Unknown error")
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Heartbeat response error: {msg}"
            )
            self._record_status_failure(str(msg))

    def _reset_status_failure_tracking(self):
        """Clear heartbeat/status failure tracking after successful communication."""
        self._status_failure_streak = 0
        self._status_recovery_in_progress = False

    def _record_status_failure(self, reason):
        """Track one failed heartbeat/status response and trigger reconnect if needed."""
        self._status_failure_streak += 1
        if self._status_failure_streak < self.status_failure_threshold:
            return
        if self._status_recovery_in_progress:
            return

        self._status_recovery_in_progress = True
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Heartbeat/status failed "
            f"{self._status_failure_streak} times ({reason}) - reconnecting"
        )

        reconnect = getattr(self.serial_mgr, "reconnect", None)
        if callable(reconnect):
            try:
                reconnect()
                return
            except Exception as exc:
                logging.warning(
                    "ACE[%s]: reconnect() after status failures failed: %s",
                    self.instance_num,
                    exc,
                )

        disconnect = getattr(self.serial_mgr, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect()
            except Exception as exc:
                logging.warning(
                    "ACE[%s]: disconnect() after status failures failed: %s",
                    self.instance_num,
                    exc,
                )

    def _reconcile_feed_assist_state(self):
        """Detect device-side feed assist loss and queue a restore (ACE2).

        On ACE2 the device stays 'busy' the whole time feed assist is active
        - a 'ready' work status while the driver believes assist is on means
        the firmware dropped it (e.g. while preloading a freshly inserted
        spool on another slot). Two consecutive
        contradicting heartbeats guard against a stale sample right after
        enabling. ACE1 has no equivalent signal (work status stays 'ready'
        during assist) and relies on the slot-ready restore path in
        _status_update_callback instead.
        """
        if self._feed_assist_index < 0:
            self._assist_lost_streak = 0
            return
        if self._pending_feed_assist_restore >= 0:
            return  # restore already queued
        try:
            if not self.protocol.feed_assist_causes_busy():
                return  # ACE1: no device-side signal to reconcile against
        except Exception:
            return

        # A device-side assist drop on an EMPTY slot is not a loss to repair
        # - the spool ran out (endless-spool tail case).  Restoring assist
        # onto an empty slot just pokes the firmware's starved retry cycling
        # (~4 s pump ramps, forever, with the work status toggling
        # busy/ready - which is exactly what feeds this reconcile check).
        # The slot-ready restore path in _status_update_callback re-arms
        # assist when the slot is refilled.
        if (getattr(self, "_device_status_seen", False)
                and self._is_slot_empty(self._feed_assist_index)):
            self._assist_lost_streak = 0
            return

        if self._info.get("status") != "ready":
            self._assist_lost_streak = 0
            return

        self._assist_lost_streak += 1
        if self._assist_lost_streak < 2:
            return

        self._assist_lost_streak = 0
        slot = self._feed_assist_index
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Feed assist on slot {slot} was dropped "
            f"by the device (work status 'ready' while assist expected) - restoring"
        )
        self._pending_feed_assist_restore = slot
        self._feed_assist_restore_attempts = 0

    def _maybe_restore_pending_feed_assist(self):
        """
        Restore feed assist if pending after reconnect.

        Called after each successful heartbeat. A busy device (e.g. RFID
        identification after power-up) re-queues the restore and it is
        retried on subsequent heartbeats, up to
        FEED_ASSIST_RESTORE_MAX_ATTEMPTS.
        Only restores if reconnecting to same position in daisy chain (topology).
        """
        if self._pending_feed_assist_restore < 0:
            return

        # Defer while a toolchange runs - it owns assist state, and a
        # restore firing mid-toolchange re-arms an assist the toolchange
        # deliberately disabled.  The pending restore stays queued and
        # re-evaluates on the next heartbeat; the ownership check below
        # then drops it if the toolchange made it stale.
        if self._manager_toolchange_in_progress():
            return

        slot = self._pending_feed_assist_restore
        self._pending_feed_assist_restore = -1  # Clear before attempting

        # Never restore assist onto a slot the device reports empty - the
        # spool ran out and the firmware dropping assist there is correct
        # behavior, not a loss.  The slot-ready restore path re-queues when
        # the slot is refilled.  Gated on _device_status_seen: the
        # initialized _info defaults every slot to 'empty' and must not
        # suppress restores before the first real heartbeat.
        if (getattr(self, "_device_status_seen", False)
                and self._is_slot_empty(slot)):
            logging.info(
                f"ACE[{self.instance_num}]: Skipping feed assist restore on "
                f"slot {slot} - device reports it empty (spool ran out)"
            )
            return

        # Single-assist invariant (same rule as _on_ace_connect): assist
        # may only be restored for the slot holding the globally current
        # tool - anything else is stale state (e.g. left behind by a failed
        # toolchange) and restoring it runs a second assist in parallel
        # with the loaded tool's (two ACEs pushing filament into one
        # toolhead).  Fail-open when the state store is unavailable or
        # unparsable.
        current_tool = None
        try:
            current_tool = int(self.state.get("ace_current_index", -1))
        except Exception:
            current_tool = None
        if current_tool is not None:
            expected_ace, expected_slot = None, -1
            if current_tool >= 0:
                try:
                    expected_ace, expected_slot = (
                        get_ace_instance_and_slot_for_tool(current_tool)
                    )
                except Exception:
                    expected_ace, expected_slot = None, -1
            if (expected_ace is not self or expected_slot != slot)                     and slot != self._loading_slot:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: NOT restoring feed assist on "
                    f"slot {slot} - it does not belong to the current tool "
                    f"(T{current_tool}). Clearing stale assist state."
                )
                if self._feed_assist_index == slot:
                    self._feed_assist_index = -1
                    self._feed_assist_topology_position = None
                try:
                    self.state.set(
                        f"ace_feed_assist_index_{self.instance_num}", -1
                    )
                except Exception:
                    pass
                return

        self._feed_assist_restore_attempts += 1
        if self._feed_assist_restore_attempts > self.FEED_ASSIST_RESTORE_MAX_ATTEMPTS:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Giving up feed assist restore on slot "
                f"{slot} after {self.FEED_ASSIST_RESTORE_MAX_ATTEMPTS} attempts - "
                f"re-enable manually with ACE_ENABLE_FEED_ASSIST"
            )
            return

        # Check if we're at the same position in the daisy chain
        current_position = self.serial_mgr.get_usb_topology_position()

        if self._feed_assist_topology_position is not None and current_position != self._feed_assist_topology_position:
            # Connected to different position in chain, don't restore
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Skipping feed assist restore - "
                f"different chain position (was: {self._feed_assist_topology_position}, "
                f"now: {current_position})"
            )
            self._feed_assist_index = -1  # Clear stale state
            self._feed_assist_topology_position = None
            return

        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Restoring feed assist on slot {slot} "
            f"(chain position: {current_position})"
        )
        try:
            # Use short timeout - if ACE is busy (e.g., RFID read/preload), skip restoration
            # Feed assist can be restored later or manually if needed
            self.wait_ready(timeout_s=5.0)
            request = self.protocol.build_start_feed_assist_request(slot)
            self.send_request(
                request,
                lambda response: self._on_feed_assist_restore_response(response, slot)
            )
        except TimeoutError:
            # ACE busy (likely RFID read/preload) - re-queue so the next
            # heartbeat actually retries (previously this message promised a
            # retry that never happened: the pending flag was already cleared)
            self._pending_feed_assist_restore = slot
            logging.info(
                f"ACE[{self.instance_num}]: Feed assist restoration on slot {slot} "
                f"deferred (ACE busy) - retry "
                f"{self._feed_assist_restore_attempts}/{self.FEED_ASSIST_RESTORE_MAX_ATTEMPTS} "
                f"on next status update"
            )
        except Exception as e:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Failed to restore feed assist: {e}"
            )

    def _on_ace_connect(self):
        """
        Handle ACE connection/reconnection.

        Refreshes RFID data for all slots with detected tags to catch any
        spool changes that occurred while disconnected.

        Defers feed assist restoration and RFID refresh until after first
        successful status update to ensure connection is stable.
        """
        self._reset_status_failure_tracking()

        # Set flag to refresh RFID data on next status update
        # This ensures we have current data if spools were changed during disconnect
        self._pending_rfid_refresh = True
        self._pending_rfid_refresh_slots = []
        logging.info(
            f"ACE[{self.instance_num}]: Connected - will refresh RFID data after first status update"
        )

        if not self.feed_assist_active_after_ace_connect:
            logging.info(
                f"ACE[{self.instance_num}]: Connected - feed assist restoration disabled"
            )
            return

        # Check if feed assist was active before disconnect. After a klippy
        # restart the in-memory index is -1 even though assist was on before -
        # fall back to the persisted ace_feed_assist_index_<n> variable (which
        # was previously written but never read back, losing feed assist on
        # every klippy/FIRMWARE_RESTART mid-print).
        slot = self._feed_assist_index
        if slot < 0:
            try:
                slot = int(self.state.get(
                    f"ace_feed_assist_index_{self.instance_num}", -1
                ))
            except (TypeError, ValueError):
                slot = -1
            if 0 <= slot < self.SLOT_COUNT:
                logging.info(
                    f"ACE[{self.instance_num}]: Recovered persisted feed assist "
                    f"slot {slot} (klippy restart)"
                )
            else:
                slot = -1

        if slot >= 0:
            # Single-assist invariant: feed assist may only be restored for
            # the slot holding the globally current tool (ace_current_index).
            # At most ONE assist may exist across ALL instances - anything
            # else is stale state (e.g. left behind by a failed toolchange).
            # Restoring it would run a second assist in parallel with the
            # loaded tool's, and on ACE2 additionally block every
            # feed/retract command on that device.
            #
            # Fail-open: the invariant is only applied when ace_current_index
            # is positively readable as an int (-1 included: "no tool loaded"
            # makes any assist stale). If the state store is unavailable or
            # the value unparsable, keep the plain restore behavior rather
            # than destroying possibly-legitimate assist state.
            current_tool = None
            try:
                current_tool = int(self.state.get("ace_current_index", -1))
            except Exception:
                current_tool = None
            if current_tool is not None:
                expected_ace, expected_slot = None, -1
                if current_tool >= 0:
                    try:
                        expected_ace, expected_slot = (
                            get_ace_instance_and_slot_for_tool(current_tool)
                        )
                    except Exception:
                        expected_ace, expected_slot = None, -1
                if (expected_ace is not self or expected_slot != slot)                         and slot != self._loading_slot:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: NOT restoring feed assist "
                        f"on slot {slot} - it does not belong to the current "
                        f"tool (T{current_tool}). Clearing stale assist state."
                    )
                    self._feed_assist_index = -1
                    self._feed_assist_topology_position = None
                    self.state.set(
                        f"ace_feed_assist_index_{self.instance_num}", -1
                    )
                    slot = -1

        if slot >= 0:
            # Defer restoration until after first heartbeat confirms communication
            self._pending_feed_assist_restore = slot
            self._feed_assist_restore_attempts = 0
            logging.info(
                f"ACE[{self.instance_num}]: Connected - "
                f"will restore feed assist on slot {slot} after heartbeat"
            )
        else:
            logging.info(
                f"ACE[{self.instance_num}]: Connected - "
                f"no previous feed assist to restore"
            )

    def _on_feed_assist_restore_response(self, response, slot):
        """Handle response from feed assist restoration after reconnect."""
        if response and response.get("code") == 0:
            # Restore bypasses _enable_feed_assist, so sync runtime state here.
            # Matters for the klippy-restart recovery path where the in-memory
            # index is still -1 (slot came from the persisted variable).
            self._feed_assist_index = slot
            self._feed_assist_topology_position = (
                self.serial_mgr.get_usb_topology_position()
            )
            self._feed_assist_restore_attempts = 0
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed assist restored on slot {slot}"
            )
        else:
            msg = response.get("msg", "Unknown") if response else "No response"
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Feed assist restoration failed: {msg}"
            )

    def get_status(self, eventtime=None):
        """Return status dict for Klipper/Moonraker queries."""
        # Debug logging reserved for status_debug_logging; keep silent by default

        status = copy.deepcopy(self._info)
        status.pop("raw_fields", None)
        status["instance"] = self.instance_num
        status["protocol"] = self.protocol_name
        status["rfid_sync_enabled"] = bool(self.rfid_inventory_sync_enabled)
        # D-C, 2026-09-01. The published value must never claim an assist the device is not
        # performing: this is what the macro guards read (_ACE_REQUIRE_ASSIST via
        # inst.feed_assist_slot) and what postgear_seek.cfg gates 200mm of forward FORCE_MOVE
        # on. _feed_assist_index is set optimistically before the START goes out, so until the
        # device answers, report -1. _get_current_feed_assist_index() is deliberately NOT
        # changed - manager.py uses it to decide whether assist is already running on a slot,
        # and answering -1 there would re-issue an enable on a slot that is already assisting,
        # which is the ACE2 wait_ready deadlock of 2026-08-26.
        # D-E, 2026-09-04. A DEAD LINK IS ALSO "AN ASSIST THE DEVICE IS NOT PERFORMING".
        #
        # Same principle as D-C above, applied to the other way this claim goes false.
        # _feed_assist_index and _rollback_assist_index are plain attributes on this object;
        # nothing in the connection lifecycle touches them. serial_manager.disconnect() stops
        # the heartbeat, closes the port, clears the queues and the supervision counters, and
        # sets _connected = False - and leaves both assist indices exactly as they were.
        # reset_feed_assist_state() would clear one of them, but it has a single caller in the
        # entire driver: a manual operator G-code in commands.py.
        #
        # So after an RS485 drop the indices keep their last value, this accessor keeps
        # publishing an armed lane, and every _ACE_REQUIRE_ASSIST call site - 21 of them, and
        # since 2026-09-04 they gate the shaping retracts and both print-start purges - reads
        # "assist is live" from a device that is not answering and is mechanically CLAMPING.
        # The guards would wave through exactly the push they exist to refuse, on the one fault
        # where refusing matters most.
        #
        # Reporting -1 when the link is down makes those guards fail CLOSED: they refuse, the
        # operator gets "assist is NOT active", and nothing pushes into a clamped lane. That is
        # the correct behaviour for a disconnected ACE, and the recovery path is a reconnect.
        #
        # Fixed HERE and not in disconnect() deliberately. This accessor is the single place
        # the guards read, so one edit covers every one of them; and clearing the indices inside
        # the connection lifecycle would discard state that the reconnect and the assist-resume
        # logic still reason about. _get_current_*_assist_index() are likewise left alone -
        # manager.py uses them to avoid re-issuing an enable on an already-assisting slot, which
        # is the ACE2 wait_ready deadlock of 2026-08-26.
        _link_up = True
        try:
            _link_up = bool(self.serial_mgr.is_connected())
        except Exception:
            # An accessor that raises would take down every get_status consumer on the printer.
            # If we cannot tell, keep the previous behaviour rather than inventing a refusal.
            _link_up = True

        status["feed_assist_slot"] = (
            -1 if (self._feed_assist_ack_pending or not _link_up)
            else self._get_current_feed_assist_index()
        )
        status["rollback_assist_slot"] = (
            -1 if not _link_up
            else self._get_current_rollback_assist_index()
        )

        # Attach device info from last get_info response, if available
        device_info = getattr(self.serial_mgr, "device_info", {})
        if isinstance(device_info, dict):
            normalized_info = {}
            for key in ("model", "firmware", "boot_firmware", "structure_version"):
                if key in device_info and device_info[key] is not None:
                    normalized_info[key] = device_info[key]

            # ACE2 get_info currently reports version and boot_version keys.
            if "firmware" not in normalized_info and device_info.get("version"):
                normalized_info["firmware"] = device_info["version"]
            if "boot_firmware" not in normalized_info and device_info.get("boot_version"):
                normalized_info["boot_firmware"] = device_info["boot_version"]

            for key, value in normalized_info.items():
                status.setdefault(key, value)

        # Protocol-aware fallback label when the device does not report model metadata.
        if not status.get("model") and self.protocol_name == "ace2_proto":
            port_description = getattr(self.serial_mgr, "_port_description", None)
            if port_description:
                status["model"] = f"ACE2 ({port_description})"
            else:
                status["model"] = "ACE2 (Shared Bus)"
        # Attach connection info (port / usb path) for diagnostics
        port = getattr(self.serial_mgr, "serial_name", None) or getattr(self.serial_mgr, "_port", None)
        usb_path = getattr(self.serial_mgr, "_usb_location", None)
        if port:
            status.setdefault("usb_port", port)
        if usb_path:
            status.setdefault("usb_path", usb_path)

        # Expose UI-friendly slot inventory (material/color/temp/status/RFID metadata)
        # Keep the device-reported error detail from the raw status slots:
        # ACE2 reports a per-slot error family (129-135: feed/rollback/assist/
        # preload/stuck/tangled/motor error) that the inventory only sees
        # collapsed to "gear_err" — status_detail/status_code preserve which
        # fault it actually was for diagnostics and error handling.
        live_slots = {
            s.get("index"): s
            for s in (status.get("slots") or [])
            if isinstance(s, dict)
        }
        slots_out = []
        for i in range(self.SLOT_COUNT):
            inv = self.inventory[i]
            slot_data = {
                "index": i,
                "tool": self.tool_offset + i,
                "status": inv.get("status"),
                "color": inv.get("color"),
                "material": inv.get("material"),
                "temp": inv.get("temp"),
                "rfid": inv.get("rfid", False),
            }
            live = live_slots.get(i) or {}
            for key in ("status_detail", "status_code"):
                if key in live:
                    slot_data[key] = live[key]
            for key in [
                "sku",
                "brand",
                "icon_type",
                "rgba",
                "extruder_temp",
                "hotbed_temp",
                "diameter",
                "total",
                "current",
            ]:
                if key in inv:
                    slot_data[key] = inv[key]
            slots_out.append(slot_data)

        status["slots"] = slots_out

        # Expose communication state machine for KlipperScreen connection indicator
        status["connection_state"] = getattr(self.serial_mgr, "connection_state", "unknown")

        return status

    def dwell(self, delay=1.0, verbose=False):
        """Sleep in reactor time."""
        start_time = time.time()
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)
        actual_delay = time.time() - start_time

        if verbose and delay > 0.5:  # Only log significant delays
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Dwell complete: "
                f"requested={delay:.2f}s, actual={actual_delay:.2f}s"
            )

    def _extruder_move(self, length, speed, wait_for_move_end=False):
        """Move extruder (relative) via motion planner, synchronously."""
        if length == 0:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: _extruder_move() -> Skipping zero-length move"
            )
            return

        toolhead = self.printer.lookup_object('toolhead')
        cur_pos = list(toolhead.get_position())  # [X, Y, Z, E]

        new_pos = cur_pos[:]
        new_pos[3] += length

        toolhead.move(new_pos, speed)
        if wait_for_move_end:
            toolhead.wait_moves()

    def reset_persistent_inventory(self):
        """Reset persistent inventory to empty slots."""
        self.inventory = create_inventory(self.SLOT_COUNT)
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: Persistent inventory reset to empty"
        )

    def reset_feed_assist_state(self):
        """Reset feed assist state."""
        if self._feed_assist_index != -1:
            self._feed_assist_index = -1
            self.state.set(
                f"ace_feed_assist_index_{self.instance_num}",
                -1
            )

            self.gcode.respond_info(f"ACE[{self.instance_num}]: Feed assist state reset")

    def _feed_filament_to_verification_sensor(self, slot, target_sensor, feed_length):
        """
        Feed filament from slot until target sensor triggers.

        Args:
            slot: Slot index to feed from
            target_sensor: SENSOR_RDM or SENSOR_TOOLHEAD
            feed_length: Max feed distance to sensor (mm)

        Raises:
            ValueError: If feeding fails or sensors are in wrong state
        """
        target_sensor_name = "RDM" if target_sensor == SENSOR_RDM else "toolhead"

        logging.info(
            f"ACE[{self.instance_num}]: Feeding to {target_sensor_name} sensor "
            f"(length={feed_length}mm)"
        )

        self.wait_ready()

        # Pre-check: target sensor must be clear
        if self.manager.get_switch_state(target_sensor):
            raise ValueError(
                f"ACE[{self.instance_num}]: Cannot feed, filament already at {target_sensor_name} sensor"
            )

        # Start feeding
        self._feed(slot, feed_length, self.feed_speed)

        # Wait for target sensor to trigger
        expected_time = feed_length / self.feed_speed
        timeout_s = expected_time * self.timeout_multiplier
        start_time = time.time()

        while not self.manager.get_switch_state(target_sensor):
            elapsed = time.time() - start_time
            # Fail fast on the firmware's own verdict (slot -> gear_err)
            if elapsed > self.FEED_ERROR_GRACE_S:
                slot_error = self._get_slot_feed_error(slot)
                if slot_error is not None:
                    self._stop_feed(slot)
                    raise ValueError(
                        f"ACE[{self.instance_num}]: Firmware aborted the feed on "
                        f"slot {slot}: {slot_error}. Filament cannot advance - "
                        f"check spool, slot outlet and filament path."
                    )
            if elapsed > timeout_s:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Feed timeout after {timeout_s:.1f}s "
                    f"(target: {target_sensor_name})"
                )
                break
            self.dwell(0.01)

        self._stop_feed(slot)

        # Incremental feeding if sensor not reached
        accumulated_feed_length = feed_length

        if not self.manager.get_switch_state(target_sensor):
            forbidden_streak = 0

            while (not self.manager.get_switch_state(target_sensor) and
                   accumulated_feed_length < self.total_max_feeding_length):
                # Fail fast on the firmware's own verdict before pushing more
                slot_error = self._get_slot_feed_error(slot)
                if slot_error is not None:
                    self._stop_feed(slot)
                    raise ValueError(
                        f"ACE[{self.instance_num}]: Firmware aborted the feed on "
                        f"slot {slot}: {slot_error} (after {accumulated_feed_length}mm). "
                        f"Filament cannot advance - check spool, slot outlet and "
                        f"filament path."
                    )

                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Incremental feed to {target_sensor_name} "
                    f"({self.incremental_feeding_length}mm at "
                    f"{self.incremental_feeding_speed}mm/s)"
                )

                # Send synchronously and check the verdict: the ACE rejects a
                # feed command while the previous feed is still executing
                # (FORBIDDEN). The old blind timer pacing (nominal move time
                # + 0.1s margin) overran regularly, and every rejected feed
                # was still counted as fed filament - the loop then hit
                # total_max_feeding_length hundreds of phantom mm before the
                # filament actually got there.
                response = self.feed_filament_with_wait_for_response(
                    slot, self.incremental_feeding_length,
                    self.incremental_feeding_speed
                )
                if response and response.get("msg") == "FORBIDDEN":
                    forbidden_streak += 1
                    if forbidden_streak >= self.INCREMENTAL_FEED_FORBIDDEN_MAX:
                        self._stop_feed(slot)
                        raise ValueError(
                            f"ACE[{self.instance_num}]: Feed rejected (FORBIDDEN) "
                            f"{forbidden_streak} times in a row on slot {slot} - "
                            f"device refuses to feed, aborting"
                        )
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Feed deferred (FORBIDDEN - "
                        f"previous feed still running), retrying "
                        f"({forbidden_streak}/{self.INCREMENTAL_FEED_FORBIDDEN_MAX})"
                    )
                    self.dwell(0.5)
                    continue

                # Non-FORBIDDEN errors keep the old count-and-continue
                # semantics: not counting them could loop forever, and the
                # sensor/max-length checks above remain the safety net.
                forbidden_streak = 0
                accumulated_feed_length += self.incremental_feeding_length

                self.dwell((self.incremental_feeding_length / self.incremental_feeding_speed) + 0.1)

            if not self.manager.get_switch_state(target_sensor):
                raise ValueError(
                    f"ACE[{self.instance_num}]: Fed {accumulated_feed_length}mm, "
                    f"but {target_sensor_name} sensor not triggered"
                )

            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament reached {target_sensor_name} sensor after feeding "
                f"{accumulated_feed_length}mm. Consider updating 'feed_length' if needed."
            )

        self.wait_ready()

        # Set final filament position state
        if target_sensor == SENSOR_RDM:
            self.state.set(
                "ace_filament_pos",
                FILAMENT_STATE_SPLITTER
            )
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament position set to splitter (at RDM)"
            )
        else:
            self.state.set(
                "ace_filament_pos",
                FILAMENT_STATE_TOOLHEAD
            )
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Filament position set to toolhead"
            )
