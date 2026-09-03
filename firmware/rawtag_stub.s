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
        @ OpenSpool/FilaMan IDENTITY INJECT. This branch is why a foreign tag read through
        @ cmd-68 used to come back sku=None: it committed 0x0202 and jumped to the epilogue, so
        @ the sm_id inject at 0x0800E8A2 was never reached and the host had to fall back to the
        @ op-9 raw walk (~2.88s, torn-prone, cross-reader-contaminated). The extend-read fills
        @ 0x20000704 with pages 4-51 on THIS path too (cmd-68 calls rfid_pageread 0x0800E18C -
        @ verified by disassembly), and the memcpy above rewrites only the first ~140 bytes with
        @ identical data, so sm_id in the tail survives. Find it and answer like a native decode
        @ (0x0203 + "SM<n>") so the host binds synchronously. Not found -> raw path, verbatim.
        movw    r0, #0x0704
        movt    r0, #0x2000             @ 0x20000704, pages 4-51 (192 bytes)
        movs    r1, #192
        bl      smid_find               @ r0 = digit ptr (0 if none), r1 = digit count
        cmp     r0, #0
        beq     .Lraw
        add     r2, r4, #8              @ response sku field (19 bytes, NUL at r4+27)
        bl      smid_build              @ "SM" + digits + NUL
@ 0x0203 = "identity injected; the sku is real, EVERY other field in this record is the
@ stock positional parse of a tag that is not in Anycubic's layout, i.e. garbage."
@ We used to write 101 here, which means "valid native decode, trust everything" - a lie
@ that only our own host knew to discount. Any other consumer (an Anycubic printer, most
@ obviously) would have read a nozzle target out of that garbage. An unknown version is
@ ignored by a consumer that does not know it, which is the safe default we want.
        movw    r0, #0x0203             @ injected-identity sentinel, NOT a native decode
        str     r0, [r4, #4]
        movs    r0, #0
        strb    r0, [r4, #27]           @ ensure the sku field is NUL-terminated
        movs    r0, #0                  @ code = SUCCESS
        b.w     epilogue

.Lraw:
        movw    r0, #0x0202             @ sentinel: raw image cached, fetch with op 9
        str     r0, [r4, #4]            @ 32-bit, matching the handler's own store
        movs    r0, #0                  @ code = SUCCESS
        b.w     epilogue                @ 0x0800E904: str r0,[r4,#140]; movs r0,#1; pop

        @ --- Anycubic: replay the displaced instruction and resume ------------------
anycubic:
        add.w   lr, sp, #140
        b.w     resume                  @ 0x0800E846

@ ===========================================================================
@ Duplicated from rawtag_cmd68_stub.s (build assembles each stub independently, no
@ cross-linking). Byte-identical and proven. If either copy changes, change all of them.
@ ===========================================================================

        .global smid_find
        .thumb_func
smid_find:
        push    {r4, lr}
        add     r1, r0, r1
        subs    r3, r1, #6
.Lscan:
        cmp     r0, r3
        bhi     .Lnone
        ldrb    r2, [r0]
        cmp     r2, #0x73
        bne     .Lnext
        ldrb    r2, [r0, #1]
        cmp     r2, #0x6d
        bne     .Lnext
        ldrb    r2, [r0, #2]
        cmp     r2, #0x5f
        bne     .Lnext
        ldrb    r2, [r0, #3]
        cmp     r2, #0x69
        bne     .Lnext
        ldrb    r2, [r0, #4]
        cmp     r2, #0x64
        beq     .Lkey
.Lnext:
        adds    r0, #1
        b       .Lscan
.Lkey:
        adds    r0, #5
.Lskip:
        cmp     r0, r1
        bhs     .Lnone
        ldrb    r2, [r0]
        cmp     r2, #0x7d
        beq     .Lnone
        cmp     r2, #0x30
        blo     .Lskn
        cmp     r2, #0x39
        bls     .Ldig
.Lskn:
        adds    r0, #1
        b       .Lskip
.Ldig:
        mov     r4, r0
.Lcnt:
        cmp     r0, r1
        bhs     .Lend
        ldrb    r2, [r0]
        cmp     r2, #0x30
        blo     .Lend
        cmp     r2, #0x39
        bhi     .Lend
        adds    r0, #1
        b       .Lcnt
.Lend:
        sub     r1, r0, r4
        mov     r0, r4
        pop     {r4, pc}
.Lnone:
        movs    r0, #0
        movs    r1, #0
        pop     {r4, pc}

        .global smid_build
        .thumb_func
smid_build:
        push    {r4, lr}
        movs    r3, #0x53
        strb    r3, [r2], #1
        movs    r3, #0x4d
        strb    r3, [r2], #1
.Lcpy:
        cmp     r1, #0
        beq     .Lbnul
        ldrb    r3, [r0], #1
        strb    r3, [r2], #1
        subs    r1, #1
        b       .Lcpy
.Lbnul:
        movs    r3, #0
        strb    r3, [r2]
        pop     {r4, pc}
