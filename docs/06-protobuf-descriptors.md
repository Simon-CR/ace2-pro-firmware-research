# The complete protobuf wire format, recovered from the firmware

Every previous description of the ACE 2 protocol — including hakimio's `ace2-pro.proto`, which
this builds on — was assembled command by command from observed traffic. This document instead
decodes the **nanopb descriptors inside the firmware image**, so the field numbers, wire types, C
sizes and array bounds are read from the device's own tables rather than inferred.

Result: [`protocol/ace2-v1.1.31.proto`](../protocol/ace2-v1.1.31.proto) — all 40 messages, with
addresses in comments. Raw descriptor dump: [`protocol/descriptor-dump.txt`](../protocol/descriptor-dump.txt).
Reproduce with `tools/pbdesc_decode.py` and `tools/pbdesc_report.py`.

## The descriptor format

This build uses **nanopb 0.4.x**, not the 0.3 `pb_field_t` array.

```
pb_msgdesc_t = 7 x uint32 = 28 bytes      (pb_size_t is 32-bit in this build)
  { field_info*, submsg_info*, default_value*, field_callback*,
    field_count, required_count, largest_tag }

emitted layout per message:
  [ field_info words + 0 terminator ][ pb_msgdesc_t ][ submsg_info ptrs + NULL ]
```

which is why `submsg_info` is always exactly `&msgdesc + 28`.

`field_info` packed words (only the 1- and 2-word forms appear here; low 2 bits select the form):

```
1-word:  tag = (w>>2)&0x3F      type = (w>>8)&0xFF
         data_offset = (w>>16)&0xFF   size_offset = (w>>24)&0xF   data_size = (w>>28)&0xF

2-word:  w0: tag, type, array_size = (w0>>16)&0xFFF, size_offset = (w0>>28)&0xF
         w1: data_offset = w1&0xFFFF, data_size = (w1>>16)&0xFFF
```

`type` = `ATYPE(0xC0) | HTYPE(0x30) | LTYPE(0x0F)`; every field in this firmware is
`ATYPE_STATIC` with `HTYPE_SINGULAR (0x10)` or `HTYPE_REPEATED (0x20)`.

The format was established by triangulation against messages whose shape was already certain,
not by assumption:

- `FeedInfoResponse` (`0x08018F10`) decodes to a repeated submessage, tag 1, max 4 elements of
  12 bytes — exactly `FeedInfo{int32,int32,int32}`.
- `FilamentInfoResponse` (`0x08018FE0`) decodes to 12 fields, tags 1–12, strings at 20 bytes,
  `colors` repeated ×10 of 4 bytes, submessages of 16 and 8 bytes — matching the known shape
  including the `has_` bytes implied by `size_offset = 4`.
- `GenericResponse` (`0x080190A4`) is a single word: tag 1, varint.
- Independent corroboration: `FirmwareRequest` computes to exactly **72 bytes**, and the
  dispatcher at `0x08009C5C` memsets its request scratch at `0x200006BC` to exactly 72
  (`movs r1,#72`).

## Why the dispatch table has 26 entries but 33 commands are registered

Not missing descriptors. The static array at `0x08018C5C` holds 26 triples
(`{request_fields, response_fields, handler}`); the remaining **7 are constructed in RAM at boot**
by code at `0x08015E6E–0x0801601A`, each behind a per-feature enable byte:

| RAM slot | cmd | built at |
|---|---|---|
| `0x20000E70` | 73 `GET_SENSOR_STATE` | `0x08015F76` |
| `0x20000E7C` | 78 (key/linear calibration readback) | `0x08015FFA` |
| `0x20000E88` | 15 `LINEAR_KEY_CALIBRATE` | `0x08015FB8` |
| `0x20000FA4` | 71 `SET_FAN` | `0x08015EB0` |
| `0x20000FB0` | 70 `FLASH_LED` | `0x08015F34` |
| `0x20000FBC` | 66 `SET_VALVE` | `0x08015EF2` |
| `0x20000FC8` | 72 (component on/off) | `0x08015E6E` |

26 + 7 = 33, and all 40 descriptors are referenced (32 as request/response, 8 as submessages) with
**no orphans**.

**Commands 67 (`DRY_TEST`), 69 (`RFID_TEST`) and 74 (`SET_KEY_LOG_ENABLE`) are not registered at
all** in V1.1.31 — established by enumerating every registration site, not by absence of evidence.
This is why calling `RFID_TEST` returns nothing.

## Corrections to `ace2-pro.proto`

Six messages differ from the widely-used `.proto`. Each is backed by both the descriptor and the
handler's register accesses:

1. **`GetTempResponse` has 7 float fields, not 6** (`0x080190E4`, tags 1–7 at offsets 0x00–0x18).
   Handler `0x0800BD7E` writes tags 1 and 2 as literal zero on every call — so the existing six
   names are also mis-slotted by one.
2. **Command 72's request has 2 uint32 fields, not 4** (`0x08019328`). Handler `0x08010004`
   treats field 1 as an 8-bit component bitmask and field 2 as on/off, calling the same component
   driver (`0x0800F0CC`) that `SET_FAN` uses with masks `0x10`/`0x20` and `SET_VALVE` with
   `0x40`/`0x80`. It is not `MotorMoveRequest{motor_id,direction,steps,speed}`.
3. **Command 78's response is `repeated {uint32,uint32}` (max 16)**, not
   `{motor_id,state,position}` (`0x080191C8` → element `0x08018DF4`). Handler `0x0800FD5C` always
   emits 16 pairs from the table at `0x20000054` — the same table `LINEAR_KEY_CALIBRATE` writes.
   **It reads back key/linear calibration, not motor status.**
4. **`GetMaterialInfoResponse` field 2 is a nested message, not a string**:
   `{uint32 index = 1, MaterialInfo info = 2}` with `MaterialInfo{string name(char[22]) = 1,
   uint32 code = 2}` (`0x08019224` → `0x08019484`). Handler `0x0800DA30` sets `has_info` at +4,
   copies 21 name bytes at +8, and writes the code at +0x20.
5. **`VersionResponse` (cmd 5) has 3 fields** — cmd 5 shares `GET_INFO`'s descriptor
   `0x08019148`, including the trailing bool.
6. **`SetPrinterStatusRequest.status` is `bool`, not `uint32`** (`0x08019350`, LTYPE_BOOL,
   data_size 1; handler `0x0800E9EA` reads it with `LDRB`).

Also missing there: **`MOTOR_TEST` (77) does take a request** — it reuses the
`FeedOrRollbackRequest` descriptor `0x08018F78`, and handler `0x0800B400` validates
field 1 ≤ 3, field 2 ≤ 100, field 4 ≤ 3.

## What cannot be recovered

Firmware descriptors carry **no field names**, and nanopb encodes `enum` and `uint32` identically
(varint, 4 bytes) — so enum-versus-uint32 is unrecoverable from the image. Names in the generated
`.proto` are inherited from hakimio's file wherever the shape matches and marked `(?)` where they
are guesses; `Cmd72Request`, `Cmd78Response` and `Cmd78Entry` are placeholders.

`GET_MATERIAL_INFO` reads its name table from **`0x08007394`** — below the application's
`0x08008000` load base, i.e. in the bootloader/parameter region, which is not present in the OTA
image. Its content cannot be recovered without an SWD dump.
