import io
# Run from L:/ace2-fw-analysis:  python3 scripts/pbdesc_report.py
exec(open('scripts/pbdesc_decode.py').read())

RAM = {0x20000fc8: (0x08019328, 0x080190a4, 0x08010005),
       0x20000fa4: (0x080192d0, 0x080190a4, 0x08010039),
       0x20000fbc: (0x080193a8, 0x080190a4, 0x08010171),
       0x20000fb0: (0x08019058, 0x080190a4, 0x080101ed),
       0x20000e70: (0x0801907c, 0x08019170, 0x0800fc4d),
       0x20000e88: (0x0801919c, 0x080190a4, 0x0800fc99),
       0x20000e7c: (0x0801907c, 0x080191c8, 0x0800fd5d)}

CMD = {
 0:  (0x08018cbc, 'DISCOVER_DEVICE',       0x0800fade, 0x08014,   'obj'),
 1:  (0x08018cc8, 'ASSIGN_DEVICE_ID',      0x0800fafa, 0,         'obj'),
 2:  (0x08018d10, 'IAP_UPGRADE',           0x0800fbda, 0x08013fe4,'reg'),
 3:  (0x08018d28, 'IAP_FIRMWARE',          0x0800fbf6, 0x08013ff2,'reg'),
 4:  (0x08018d34, 'IAP_UPGRADE_FINISH',    0x0800fc12, 0x08014000,'reg'),
 5:  (0x08018d1c, 'IAP_VERSION',           0x0800fc2e, 0x0801400e,'reg'),
 6:  (0x08018cb0, 'GET_STATUS',            0x0800fb16, 0,         'obj'),
 7:  (0x08018ca4, 'GET_INFO',              0x0800fb32, 0,         'obj'),
 8:  (0x08018c8c, 'FEED_OR_ROLLBACK',      0x0800fa36, 0x08015836,'reg'),
 9:  (0x08018c98, 'STOP_FEED_OR_ROLLBACK', 0x0800fa52, 0x08015844,'reg'),
 10: (0x08018c68, 'UPDATE_SPEED',          0x0800fa6e, 0x08015852,'reg'),
 11: (0x08018cd4, 'DRYING',                0x0800fb4e, 0x0800c358,'reg'),
 12: (0x08018cec, 'SET_DRY_TEMP',          0x0800fb86, 0x0800c374,'reg'),
 13: (0x08018d7c, 'GET_RFID_CACHE',        0x0800fec8, 0x0800ea7c,'reg'),
 14: (0x08018d64, 'SET_RFID_ENABLE',       0x0800fee4, 0x0800ea8a,'reg'),
 15: (0x20000e88, 'LINEAR_KEY_CALIBRATE',  0x0800fd40, 0x08015ad4,'reg'),
 16: (0x08018d4c, 'GET_MATERIAL_INFO',     0x0800fda6, 0x08015b9a,'reg'),
 17: (0x08018d58, 'SET_SLOT_STATUS',       0x0800fdde, 0x08015bb6,'reg'),
 18: (0x08018d40, 'SET_MATERIAL_NAME',     0x0800fdc2, 0x08015ba8,'reg'),
 19: (0x08018c80, 'SET_FEED_CHECK',        0x0800faa6, 0x0801586e,'reg'),
 20: (0x08018d88, 'SET_PRINTER_STATUS',    0x0800ff00, 0x0800ea98,'reg'),
 64: (0x08018ce0, 'GET_TEMP',              0x0800fb6a, 0x0800c366,'reg'),
 65: (0x08018d04, 'SET_DRY_POWER',         0x0800fbbe, 0x0800c390,'reg'),
 66: (0x20000fbc, 'SET_VALVE',             0x080101ce, 0x08015a1c,'reg'),
 68: (0x08018d70, 'GET_FILAMENT_INFO',     0x0800feac, 0x0800ea6e,'reg'),
 70: (0x20000fb0, 'FLASH_LED',             0x0801025c, 0x08015a3e,'reg'),
 71: (0x20000fa4, 'SET_FAN',               0x08010154, 0x080159fa,'reg'),
 72: (0x20000fc8, 'MOTOR_MOVE(?)',         0x0801001a, 0x080159d8,'reg'),
 73: (0x20000e70, 'GET_SENSOR_STATE',      0x0800fc7c, 0x08015ab2,'reg'),
 75: (0x08018cf8, 'DRY_CMD',               0x0800fba2, 0x0800c382,'reg'),
 76: (0x08018c74, 'GET_FEED_INFO',         0x0800fa8a, 0x08015860,'reg'),
 77: (0x08018c5c, 'MOTOR_TEST',            0x0800fac2, 0x0801587c,'reg'),
 78: (0x20000e7c, 'GET_MOTOR_STATUS(?)',   0x0800fd8a, 0x08015af6,'reg'),
}

NAME = {
 0x0801907c: 'Empty', 0x080190a4: 'GenericResponse', 0x08018e4c: 'DiscoverDeviceResponse',
 0x08018dc8: 'AssignDeviceIdRequest', 0x080194e4: 'UpgradeRequest', 0x0801901c: 'FirmwareRequest',
 0x08019148: 'InfoResponse', 0x08019424: 'StatusResponse', 0x08018e80: 'DryStatus',
 0x080193d4: 'SlotStatus', 0x08018f78: 'FeedOrRollbackRequest',
 0x08019454: 'StopFeedOrRollbackRequest', 0x080194b0: 'UpdateSpeedRequest',
 0x08018f10: 'FeedInfoResponse', 0x08018f44: 'FeedInfo', 0x080192fc: 'SetFeedCheckRequest',
 0x08018fe0: 'FilamentInfoResponse', 0x08018e1c: 'ColorInfo', 0x08018ee4: 'ExtruderTemp',
 0x08019110: 'HotbedTemp', 0x08019250: 'RfidRequest', 0x0801937c: 'SetRfidEnableRequest',
 0x08018eb0: 'DryingRequest', 0x080190e4: 'GetTempResponse', 0x080192a0: 'SetDryTempRequest',
 0x08019278: 'SetDryPowerRequest', 0x080193a8: 'SetValveRequest', 0x08019058: 'FlashLedRequest',
 0x080192d0: 'SetFanRequest', 0x08019328: 'Cmd72Request', 0x08019170: 'GetSensorStateResponse',
 0x0801919c: 'LinearCalibrationRequest', 0x080191c8: 'Cmd78Response', 0x08018df4: 'Cmd78Entry',
 0x080191f4: 'GetMaterialInfoRequest', 0x08019224: 'GetMaterialInfoResponse',
 0x08019484: 'MaterialInfo', 0x08019510: 'SetSlotStatusRequest',
 0x08019540: 'SetMaterialNameRequest', 0x08019350: 'SetPrinterStatusRequest'}

FN = {
 0x080190a4: {1: 'code'},
 0x08018e4c: {1: 'uid1', 2: 'uid2', 3: 'uid3'},
 0x08018dc8: {1: 'uid1', 2: 'uid2', 3: 'uid3', 4: 'device_id'},
 0x080194e4: {1: 'size', 2: 'crc', 3: 'version'},
 0x0801901c: {1: 'address', 2: 'firmware'},
 0x08019148: {1: 'version', 2: 'boot_version', 3: 'first_request'},
 0x08019424: {1: 'status', 2: 'dry_status', 3: 'temp', 4: 'humidity', 5: 'rfid1_enable',
              6: 'rfid2_enable', 7: 'feed_assist_count', 8: 'cont_assist_time', 9: 'slot_status'},
 0x08018e80: {1: 'status', 2: 'target_temp', 3: 'duration', 4: 'remain_time'},
 0x080193d4: {1: 'status', 2: 'filament'},
 0x08018f78: {1: 'index', 2: 'speed', 3: 'length', 4: 'mode'},
 0x08019454: {1: 'index'},
 0x080194b0: {1: 'index', 2: 'speed'},
 0x08018f10: {1: 'feed_info'},
 0x08018f44: {1: 'steps', 2: 'length', 3: 'decoder'},
 0x080192fc: {1: 'check_length', 2: 'error_length'},
 0x08018fe0: {1: 'index', 2: 'version', 3: 'sku', 4: 'type', 5: 'colors', 6: 'extruder_temp',
              7: 'hotbed_temp', 8: 'diameter', 9: 'length', 10: 'icon_type', 11: 'remainder', 12: 'code'},
 0x08018e1c: {1: 'rgba'},
 0x08018ee4: {1: 'min_temp', 2: 'max_temp', 3: 'min_speed', 4: 'max_speed'},
 0x08019110: {1: 'min_temp', 2: 'max_temp'},
 0x08019250: {1: 'index'},
 0x0801937c: {1: 'index', 2: 'enable'},
 0x08018eb0: {1: 'temp', 2: 'duration', 3: 'auto_roll'},
 0x080190e4: {1: 'unknown1_always_zero', 2: 'unknown2_always_zero', 3: 'temp_a', 4: 'temp_b',
              5: 'temp_c', 6: 'temp_d', 7: 'temp_e'},
 0x080192a0: {1: 'temp'},
 0x08019278: {1: 'power'},
 0x080193a8: {1: 'valve1', 2: 'valve2'},
 0x08019058: {1: 'components', 2: 'loop', 3: 'quick1', 4: 'slow1', 5: 'quick2', 6: 'slow2'},
 0x080192d0: {1: 'speed', 2: 'fan1', 3: 'fan2'},
 0x08019328: {1: 'components', 2: 'state'},
 0x08019170: {1: 'state'},
 0x0801919c: {1: 'id', 2: 'type'},
 0x080191c8: {1: 'entries'},
 0x08018df4: {1: 'value_a', 2: 'value_b'},
 0x080191f4: {1: 'index'},
 0x08019224: {1: 'index', 2: 'info'},
 0x08019484: {1: 'name', 2: 'code'},
 0x08019510: {1: 'index', 2: 'status'},
 0x08019540: {1: 'index', 2: 'name'},
 0x08019350: {1: 'status'},
}

PROTO_T = {0: 'bool', 1: 'int32', 2: 'uint32', 3: 'sint32', 4: 'float', 5: 'double',
           6: 'bytes', 7: 'string'}


def enrich(m):
    subs = list(m['subs'])
    i = 0
    for f in m['fields']:
        if f['ltype'] in (8, 9):
            f['sub'] = subs[i]
            i += 1
    return m


def ctype(f):
    lt = f['ltype']
    if lt in (8, 9):
        return NAME.get(f.get('sub'), 'msg_%08x' % f.get('sub', 0))
    if lt == 4:
        return 'float' if f['data_size'] == 4 else 'fixed64'
    return PROTO_T.get(lt, 'LTYPE%d' % lt)


def triple(t):
    if t in RAM:
        return RAM[t]
    return u32(t), u32(t + 4), u32(t + 8)


# ---------------- proto ----------------
out = io.StringIO()
W = out.write
W('// ============================================================================\n')
W('// ACE2 Pro firmware V1.1.31 (20260306) - protobuf wire layout recovered from\n')
W('// the DEVICE firmware image (ex/ACE2_V1.1.31_20260306.bin, link base 0x08008000).\n')
W('//\n')
W('// Descriptor encoding: nanopb 0.4.x.\n')
W('//   pb_msgdesc_t = 7 x uint32 { field_info, submsg_info, default_value,\n')
W('//                               field_callback, field_count, required_count,\n')
W('//                               largest_tag }   (pb_size_t is 32-bit in this build)\n')
W('//   field_info   = packed uint32 words, 1/2/4/8-word variants (low 2 bits = format)\n')
W('//   emitted layout per message: [field_info + 0][pb_msgdesc_t][submsg_info + NULL]\n')
W('//\n')
W('// Field NUMBERS / wire types / C sizes / array bounds below are PROVEN from the\n')
W('// descriptors. Field NAMES are inherited from hakimio ace2-pro.proto wherever the\n')
W('// shape matches; names marked (?) are unproven.\n')
W('// ============================================================================\n\n')
W('syntax = "proto3";\n')
W('package ace_com;\n\n')
W('// ---- Commands actually registered by this firmware (33 of them).\n')
W('// 67 DRY_TEST, 69 RFID_TEST and 74 SET_KEY_LOG_ENABLE are NOT registered here.\n')
W('enum CommandType {\n')
for c in sorted(CMD):
    W('  // %-3d %s\n' % (c, CMD[c][1]))
W('}\n\n')

for a in sorted(NAME, key=lambda x: (NAME[x])):
    m = enrich(msgdesc(a))
    W('// msgdesc 0x%08x  field_info 0x%08x  fields=%d  largest_tag=%d  C struct >= %d bytes\n'
      % (a, m['field_info'], m['field_count'], m['largest_tag'],
         max([f['data_offset'] + f['data_size'] for f in m['fields']] + [0])))
    W('message %s {\n' % NAME[a])
    for f in m['fields']:
        nm = FN.get(a, {}).get(f['tag'], 'field%d' % f['tag'])
        rep = 'repeated ' if f['htype'] == 0x20 else ''
        t = ctype(f)
        extra = ''
        if f['ltype'] == 7:
            extra = '  // char[%d] -> max %d chars' % (f['data_size'], f['data_size'] - 1)
        elif f['ltype'] == 6:
            extra = '  // pb_bytes_array_t[%d] -> max %d bytes' % (f['data_size'], f['data_size'] - 4)
        elif f['htype'] == 0x20:
            extra = '  // max_count=%d, element %d bytes' % (f['array_size'], f['data_size'])
        elif f['ltype'] in (8, 9):
            extra = '  // submessage, %d bytes' % f['data_size']
        W('  %s%s %s = %d;%s\n' % (rep, t, nm, f['tag'], extra))
    W('}\n\n')

open(r'L:/ace2-fw-analysis/derived-protocol.proto', 'w').write(out.getvalue())

# ---------------- slice ----------------
s = io.StringIO()
V = s.write
V('ACE2 Pro V1.1.31 - nanopb descriptor recovery\n')
V('image: ex/ACE2_V1.1.31_20260306.bin  base 0x08008000  end 0x%08x\n' % END)
V('generic dispatcher: 0x08009C5C   registrar: 0x0800B7A4\n')
V('static triple table: 0x08018C5C .. 0x08018D93 (26 x 12 bytes)\n')
V('RAM triple slots  : 0x20000E70/E7C/E88 and 0x20000FA4/FB0/FBC/FC8 (7 x 12 bytes)\n')
V('\n== COMMAND TABLE ==\n')
V('%-4s %-22s %-10s %-10s %-10s %-10s %s\n' % ('cmd', 'name', 'triple', 'req_desc', 'resp_desc', 'handler', 'req -> resp'))
for c in sorted(CMD):
    t, nm, thunk, reg, kind = CMD[c]
    rq, rs, h = triple(t)
    V('%-4d %-22s 0x%08x 0x%08x 0x%08x 0x%08x %s -> %s\n'
      % (c, nm, t, rq, rs, h & ~1, NAME.get(rq, '?'), NAME.get(rs, '?')))
V('\n  thunk addresses (each passes its triple to 0x08009C5C):\n')
for c in sorted(CMD):
    t, nm, thunk, reg, kind = CMD[c]
    V('    cmd %-3d thunk 0x%08x  registered at 0x%08x (%s)\n' % (c, thunk, reg, kind))

V('\n== DECODED MESSAGE DESCRIPTORS (%d) ==\n' % len(NAME))
for a in sorted(NAME):
    m = enrich(msgdesc(a))
    V('\n%s  @0x%08x\n' % (NAME[a], a))
    V('  msgdesc words: field_info=0x%08x submsg_info=0x%08x default=0x%08x cb=0x%08x '
      'field_count=%d required=%d largest_tag=%d\n'
      % (m['field_info'], m['submsg_info'], m['default'], m['callback'],
         m['field_count'], m['required'], m['largest_tag']))
    if m['subs']:
        V('  submsg_info[] = %s\n' % ', '.join('0x%08x (%s)' % (x, NAME.get(x, '?')) for x in m['subs']))
    for f in m['fields']:
        nm = FN.get(a, {}).get(f['tag'], '?')
        V('    [0x%08x] %s  proto=%s%s  name=%s\n'
          % (f['addr'], fdesc(f), 'repeated ' if f['htype'] == 0x20 else '', ctype(f), nm))
        V('              raw words: %s\n' % ' '.join('%08x' % r for r in f['raw']))
open(r'L:/ace2-fw-analysis/slices/slice_pb_descriptors.txt', 'w').write(s.getvalue())
print('written both files')
print(s.getvalue()[:4000])


# ---- appended diff-vs-hakimio commentary ----
TAIL = r'''

// ============================================================================
// DIFF vs hakimio gists/ace2-pro.proto  (that file was reconstructed from the
// Kobra S1 *printer* gklib FileDescriptorProto; this one from the ACE2 *device*)
// ============================================================================
//
// AGREES exactly (field numbers + wire types + C sizes all match):
//   GenericRequest(=Empty), GenericResponse, DiscoverDeviceResponse,
//   AssignDeviceIdRequest, DryStatus, SlotStatus, StatusResponse, InfoResponse,
//   FeedOrRollbackRequest, StopFeedOrRollbackRequest, UpdateSpeedRequest,
//   FeedInfo, FeedInfoResponse, SetFeedCheckRequest, ColorInfo, ExtruderTemp,
//   HotbedTemp, FilamentInfoResponse, RfidRequest, SetRfidEnableRequest,
//   DryingRequest, SetDryTempRequest, DryCmdRequest, SetDryPowerRequest,
//   SetValveRequest, UpgradeRequest, FirmwareRequest, FlashLedRequest,
//   SetFanRequest, GetSensorStateResponse, LinearCalibrationRequest,
//   GetMaterialInfoRequest, SetSlotStatusRequest, SetMaterialNameRequest
//
// WRONG in hakimio ace2-pro.proto, for THIS firmware:
//
//   1. GetTempResponse - has 6 float fields there; the firmware descriptor at
//      0x080190E4 has SEVEN float (PB_LTYPE_FIXED32, 4-byte) fields, tags 1..7,
//      at struct offsets 0x00..0x18. Handler 0x0800BD7E writes tag 1 and tag 2
//      as literal 0 every time, so the six names in that file are also shifted
//      relative to the real slots.
//
//   2. MotorMoveRequest (cmd 72) - listed as 4 fields {motor_id, direction,
//      steps, speed}. The firmware request descriptor for cmd 72 is 0x08019328
//      with exactly TWO uint32 fields. Handler 0x08010004 reads field 1 as a
//      32-bit value and field 2 as a single byte, then calls the component
//      driver 0x0800F278 -> 0x0800F0CC, which walks bits 0..7 of field 1 and
//      sets each selected component to field 2. Same driver that SET_FAN uses
//      with masks 0x10/0x20 and SET_VALVE with 0x40/0x80. So cmd 72 is a
//      component on/off bitmask, not a 4-parameter motor move.
//
//   3. GetMotorStatusResponse (cmd 78) - listed as a flat
//      {motor_id, state, position}. The firmware response descriptor is
//      0x080191C8: a single REPEATED submessage field, tag 1, max_count 16,
//      element type 0x08018DF4 = {uint32, uint32}. Handler 0x0800FD5C always
//      writes count = 16 and fills the 16 pairs from the table at 0x20000054 -
//      the same table LINEAR_KEY_CALIBRATE (cmd 15, handler 0x0800FC98) writes
//      into. So this reads back key/linear calibration pairs.
//
//   4. GetMaterialInfoResponse (cmd 16) - listed as {uint32 index, string name}.
//      The firmware response 0x08019224 is {uint32 index = 1,
//      MaterialInfo info = 2} where MaterialInfo (0x08019484) =
//      {string name (char[22]) = 1, uint32 code = 2}. Handler 0x0800DA30 sets
//      has_info = 1 at offset 4, copies 21 name bytes to offset 8 and writes
//      the code word at offset 0x20. Field 2 is a nested message, not a string.
//
//   5. VersionResponse (cmd 5 IAP_VERSION) - listed as 2 fields. cmd 5 uses the
//      SAME descriptor as GET_INFO (0x08019148), which has THREE fields; the
//      third is the bool at struct offset 0x18 (InfoResponse.first_request).
//
//   6. SetPrinterStatusRequest (cmd 20) - listed as uint32 status. The firmware
//      descriptor 0x08019350 declares field 1 as PB_LTYPE_BOOL, data_size 1;
//      handler 0x0800E9EA reads it with LDRB. It is a bool.
//
// PRESENT in hakimio ace2-pro.proto but with NO descriptor and NO handler in
// this firmware (the command ids are not registered at all - see below):
//   DryTestRequest (cmd 67 DRY_TEST), RfidTestRequest (cmd 69 RFID_TEST),
//   SetKeyLogEnableRequest (cmd 74 SET_KEY_LOG_ENABLE).
//
// MISSING from hakimio ace2-pro.proto (no message given for it):
//   MOTOR_TEST (cmd 77) takes a request - it reuses the FeedOrRollbackRequest
//   shape (descriptor 0x08018F78, shared with cmd 8). Handler 0x0800B400
//   validates field1 <= 3, field2 <= 100, field4 <= 3.
//
// CAVEATS / things that CANNOT be recovered from nanopb descriptors:
//   - Field NAMES are not in the firmware. Every name above comes from
//     hakimio's file or from handler behaviour.
//   - enum vs uint32 is indistinguishable: nanopb encodes both as
//     PB_LTYPE_UVARINT with data_size 4.
//   - proto3 "singular" vs proto2 "optional" is only distinguishable by
//     size_offset != 0. All scalar fields here have size_offset 0 (implicit
//     presence); only submessage fields carry a has_ flag.
//   - Message names are not in the firmware either. Cmd72Request, Cmd78Response
//     and Cmd78Entry are placeholders invented here.
//
// DISPATCH DISCREPANCY (26 static entries vs 33 registered commands):
//   The static array at 0x08018C5C holds 26 {req_msgdesc, resp_msgdesc, handler}
//   triples. The other 7 commands use triples that are BUILT IN RAM at boot by
//   the code at 0x08015E6E..0x0801601A (an stmia of {req, resp, handler} into a
//   RAM slot, each guarded by a per-feature enable byte):
//     0x20000E70  cmd 73 GET_SENSOR_STATE      built at 0x08015F76
//     0x20000E7C  cmd 78 GET_MOTOR_STATUS(?)   built at 0x08015FFA
//     0x20000E88  cmd 15 LINEAR_KEY_CALIBRATE  built at 0x08015FB8
//     0x20000FA4  cmd 71 SET_FAN               built at 0x08015EB0
//     0x20000FB0  cmd 70 FLASH_LED             built at 0x08015F34
//     0x20000FBC  cmd 66 SET_VALVE             built at 0x08015EF2
//     0x20000FC8  cmd 72 MOTOR_MOVE(?)         built at 0x08015E6E
//   26 + 7 = 33. All 33 thunks call the generic dispatcher at 0x08009C5C.
// ============================================================================
'''
open(r'L:/ace2-fw-analysis/derived-protocol.proto', 'a').write(TAIL)
