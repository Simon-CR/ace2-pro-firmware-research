# Firmware sm_id extraction — design

**Goal:** any OpenSpool tag binds by identity on the ACE itself, with no host-side
raw-fetch (the torn-buffer path that blocks T1 today). Firmware-first.

## Why the firmware, why now
- The identity key is `sm_id` (SpoolmanScale `writeOpenSpool`), a **bare int**, the
  **last** JSON field: it lands at byte ~152 of a ~164–177B tag — **past** the ACE's
  144-byte (pages 4–39) read window. So T1 reads its own tag but never sees the id.
- Extraction is a **substring search**, not an NDEF parse: the JSON is plain ASCII in
  the tag. Find `sm_id`, skip to the first digit, copy digits. Proven in
  `validate_extract.py` against a faithful image (26/22/7/1234567 all OK; 144-byte
  window → None, full tag → id). The scary NDEF TLV state machine is unnecessary.
- Reading past page 39 is cheap: one `0x30 <page>` READ returns **16 bytes (4 pages)**
  (confirmed by the U1 extended-firmware `__reader_a_ntag215_read_all_data`). Tail =
  a few `0x30+page` transceives (40,44,48,52,56).

## Hook strategy — overwrite AFTER the parse, do not skip it
Hook **after** the positional parse has written sku+version into the cmd-13 record
(so its bookkeeping / "slot read" side-effects all still run), then **overwrite** the
sku with `SM<digits>` and version u16 with `101` **only if `sm_id` was found**. If not
found, leave the parse's output untouched → host raw-fetches → colour/material render
fallback, exactly as now. No parse-skipping, no bookkeeping to replicate, no RAM stash.

Record-layout caution (this broke attempt 1): the autonomous-reader record and the
cmd-13-served record differ. The write target is whatever the parse's own sku/version
STORE writes to — hook right after those stores and reuse their destination.

## Sequence
1. (hook, post-parse) read tail pages 40..56 via `0x30+page` transceive into the
   buffer past the 144 already present.
2. search full buffer for `sm_id`; skip to first digit; copy digits.
3. found → write `"SM"+digits` into the parse's sku field, `101` into its version u16.
4. not found → do nothing (render fallback preserved).

## Awaiting from argus (flash-critical, do not guess)
- transceive primitive to issue `0x30+page` and retrieve 4 bytes: address + calling
  convention; where the RC522 handle lives at the hook.
- the parse's sku STORE and version STORE: addresses + destination expression
  (= the write target AND a safe post-store hook point) + which register/base holds
  the record there.

## Recovery
Re-flash stock over RS485; bootloader never written. Worst case boots like stock.
