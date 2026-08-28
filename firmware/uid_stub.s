@ ACE 2 Pro V1.1.31 -- UID passthrough stub.
@
@ Hooked from the READFAILED(6) exit of the GET_FILAMENT_INFO / FILAMENT_IDENTIFY handler
@ (0x0800E7A8). Reaching that point means the ISO14443A select SUCCEEDED (so the tag's UID
@ is already in the caller's scratch buffer at sp+17) but the NTAG page read was refused --
@ which is what a MIFARE Classic tag (Bambu) does, since this firmware has no MFAuthent path.
@
@ Instead of returning READFAILED, report the tag as identified with sku = UID in hex and a
@ version sentinel the host can recognise. Anycubic tags never reach here; they take the
@ normal decode path and are unaffected.
@
@ Registers: r4 = response struct (must be preserved; the epilogue uses it).
@            r0-r3, r5 are dead on this path (callee-saved regs are restored by the pop).
@ Response layout: +4 u16 version, +8 sku[19], +140 u32 code.

        .syntax unified
        .thumb
        .text
        .global uid_stub
        .thumb_func
uid_stub:
        add.w   r1, sp, #17         @ UID bytes (7) written by the anticollision cascade
        add.w   r2, r4, #8          @ sku field
        movs    r3, #7
1:
        ldrb    r0, [r1], #1
        lsrs    r5, r0, #4          @ high nibble
        cmp     r5, #10
        ite     lt
        addlt   r5, r5, #48         @ '0'
        addge   r5, r5, #55         @ 'A' - 10
        strb    r5, [r2], #1
        and     r5, r0, #15         @ low nibble
        cmp     r5, #10
        ite     lt
        addlt   r5, r5, #48
        addge   r5, r5, #55
        strb    r5, [r2], #1
        subs    r3, r3, #1
        bne     1b

        movs    r5, #0
        strb    r5, [r2]            @ NUL terminate (14 chars + NUL fits in sku[19])
        movw    r5, #0x0201         @ version sentinel: "this sku is a raw tag UID"
        str     r5, [r4, #4]        @ 32-bit, matching the handler's own store to this field
        movs    r0, #0              @ code = SUCCESS
        b.w     epilogue            @ 0x0800E904: str r0,[r4,#140]; movs r0,#1; pop
