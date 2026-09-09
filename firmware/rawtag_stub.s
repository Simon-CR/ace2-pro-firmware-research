@ ACE 2 Pro V1.1.46 -- Native Multi-Format RFID Live Identification (cmd 68) Hook
@
@ Hooked at 0x0800E842 (HOOK_RAWTAG):
@   800e82a:  add.w  r1, r8, #24     @ r1 = sp+28, destination for the page data
@   800e82e:  bl     0x800e18c       @ rfid_pageread, returns byte count in r0
@   800e832:  cmp    r0, #140
@   800e834:  bcs.n  0x800e842       @ >= 140 bytes -> parse
@   800e836:  movs   r0, #6          @ READFAILED
@   800e842:  add.w  lr, sp, #140    @ <- HOOK_RAWTAG: We replace this instruction
@
@ Calling convention & state on entry:
@   r0 = byte count returned from rfid_pageread (>= 140)
@   r4 = pointer to FilamentInfoResponse protobuf response struct
@   0x20000704 = extended page read buffer (pages 4-51, up to 192 bytes)
@
@ Call C function:
@   int decode_cmd68_tag(uint8_t *resp, const uint8_t *page_buf, int bytes_read)
@   arg0 (r0) = r4 (resp struct)
@   arg1 (r1) = 0x20000704 (page buffer)
@   arg2 (r2) = r0 (byte count)
@
@ Return contract:
@   1  = Successfully decoded open format (OpenSpool, FilaMan, Prusament, Creality).
@        Response struct is fully populated with native metadata.
@        Set r0 = 0 and branch directly to epilogue (0x0800E904), completely
@        bypassing the stock Anycubic positional parse.
@   0  = Anycubic native tag (Magic 123 / Version 101).
@        Restore r0, replay displaced add.w lr, sp, #140, and branch to resume
@        (0x0800E846) so stock Anycubic unpack runs untouched.
@  -1  = Unrecognized tag format or read failure.
@        Commit raw cached sentinel (0x0202) to [r4, #4], set r0 = 0, and branch
@        to epilogue (0x0800E904).

        .syntax unified
        .thumb
        .text
        .global rawtag_stub
        .thumb_func

rawtag_stub:
        push    {r4, lr}
        sub     sp, #8
        str     r0, [sp, #0]            @ preserve byte count

        @ Call C decode_cmd68_tag(r4, 0x20000704, r0)
        mov     r2, r0                  @ arg2: bytes_read
        movw    r1, #0x0704             @ arg1: page_buf = 0x20000704
        movt    r1, #0x2000
        mov     r0, r4                  @ arg0: resp struct pointer (r4)
        bl      decode_cmd68_tag

        cmp     r0, #1
        beq     .Lcmd68_success

        cmp     r0, #0
        beq     .Lcmd68_anycubic

        @ r0 == -1: Unrecognized tag -> raw fallback sentinel 0x0202
        add     sp, #8
        pop     {r4, lr}
        movw    r0, #0x0202
        str     r0, [r4, #4]
        movs    r0, #0
        b.w     epilogue                @ 0x0800E904: str r0, [r4, #140]; movs r0, #1; pop

.Lcmd68_success:
        add     sp, #8
        pop     {r4, lr}
        movs    r0, #0                  @ code = 0 (SUCCESS: epilogue stores r0 into [r4, #140])
        b.w     epilogue                @ branch directly to 0x0800E904

.Lcmd68_anycubic:
        ldr     r0, [sp, #0]            @ restore byte count
        add     sp, #8
        pop     {r4, lr}
        add.w   lr, sp, #140            @ replay displaced instruction
        b.w     resume                  @ resume stock Anycubic positional parse at 0x0800E846
