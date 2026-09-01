@ ACE 2 Pro V1.1.31 -- raw tag passthrough stub.
@
@ Hooked at 0x0800E842: after the page read has succeeded and its length check has passed, and
@ IMMEDIATELY BEFORE the positional Anycubic parse begins. That ordering is the whole point --
@ nothing has been misinterpreted yet.
@
@   800e82a:  add.w  r1, r8, #24     @ r1 = sp+28, destination for the page data
@   800e82e:  bl     0x800e18c       @ the read; byte count returned in r0
@   800e832:  cmp    r0, #140
@   800e834:  bcs.n  0x800e842       @ enough bytes -> parse
@   800e836:  movs   r0, #6          @ READFAILED (where uid_stub.s hooks)
@   800e842:  add.w  lr, sp, #140    @ <- WE REPLACE THIS; the parse starts here
@
@ On entry:  sp+28 = the complete raw tag image
@            r0    = its byte count (>= 140)
@            r4    = response struct (must survive)
@ Displaced instruction: add.w lr, sp, #140, re-executed on the Anycubic path.
@
@ WHY THIS EXISTS. The firmware decodes exactly one layout: page 4 must begin 7B 00 65 00
@ (magic 123, version 101), after which every field is read at a FIXED OFFSET. A tag in any
@ other layout still selects and still reads perfectly -- it just is not Anycubic -- and the
@ positional parser then returns whatever bytes happen to land at those offsets, with code 0.
@ Observed on an OpenSpool tag: sku = 'application/json{"' (the NDEF MIME record header),
@ temp = 28770C, hotbed min 8804 > max 8762. Returned as SUCCESS, and it reached the heater
@ target, the panel, and a RESPOND MSG that broke on the embedded quotes. Stock firmware's
@ honest READFAILED is safer than that; this stub replaces the guess with the actual bytes.
@
@ ANYCUBIC TAGS ARE UNTOUCHED. They match the magic and branch straight back into the original
@ parse, so their behaviour is bit-for-bit unchanged. Only tags the firmware cannot decode take
@ the new path, which keeps this at the same risk profile as the existing hooks: an error exit
@ and a dead path.
@
@ WHY NOT DRIVE THE READER FROM THE HOST INSTEAD. The RC522 passthrough can do it, but the host
@ then has to park the tag itself -- a tag is only in the antenna's field while the spool turns,
@ so that means rotating up to ~600mm per lane, per read. The firmware has ALREADY done that
@ work by the time execution reaches here: the tag is parked, the RF layer driven, CRC and
@ anticollision handled. Taking the bytes at this instant costs nothing and needs no motion.
@
@ The image is copied to the firmware's own tag-page buffer at 0x20000704 (idle while background
@ scanning is off, and already the destination op 7 uses), where the host reads it back with
@ RC522 op 9. op 4 cannot do it: it masks its offset to 6 bits and starts at BUF+64, so it can
@ only reach bytes 64..127 -- exactly missing the sku, brand and material pages. op 9 takes the
@ full 8-bit offset and was added alongside this stub.
@
@ Version sentinel 0x0202 follows the convention uid_stub.s established with 0x0201 ("this sku
@ is a raw tag UID"). 0x0202 means "this tag is not Anycubic-format; its raw image is cached in
@ the page buffer -- read it with op 9 and parse it on the host". The host owns format
@ identification from there, so supporting a new tag layout never needs another flash.

        .syntax unified
        .thumb
        .text
        .global rawtag_stub
        .thumb_func

rawtag_stub:
        ldr     r1, [sp, #28]           @ page 4, the first four bytes of the image
        movw    r2, #0x007B             @ bytes 7B 00 65 00 little-endian = 0x0065007B
        movt    r2, #0x0065
        cmp     r1, r2
        beq     anycubic

        @ --- not Anycubic: hand the raw bytes to the host ---------------------------
        mov     r2, r0                  @ byte count from the read, as its own length
        movw    r0, #0x0704
        movt    r0, #0x2000             @ 0x20000704, the tag-page buffer
        add     r1, sp, #28
        bl      memcpy
        movw    r0, #0x0202             @ sentinel: raw image cached, fetch with op 9
        str     r0, [r4, #4]            @ 32-bit, matching the handler's own store
        movs    r0, #0                  @ code = SUCCESS
        b.w     epilogue                @ 0x0800E904: str r0,[r4,#140]; movs r0,#1; pop

        @ --- Anycubic: replay the displaced instruction and resume ------------------
anycubic:
        add.w   lr, sp, #140
        b.w     resume                  @ 0x0800E846
