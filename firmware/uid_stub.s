@ ACE 2 Pro V1.1.31 -- UID passthrough stub.
@
@ Hooked from the READFAILED(6) exit of the GET_FILAMENT_INFO / FILAMENT_IDENTIFY handler
@ (0x0800E7A8). Reaching that point means the ISO14443A select SUCCEEDED (so the tag's UID
@ is at r8+3 = sp+7) but the NTAG page read was refused -- which is what a MIFARE Classic
@ tag (Bambu) does, since stock firmware has no MFAuthent path.

        .syntax unified
        .thumb
        .text
        .global uid_stub
        .thumb_func
uid_stub:
        push    {r4, lr}
        mov     r0, r4              @ arg0: resp struct pointer
        add.w   r1, r8, #3          @ arg1: UID buffer (UID is at select_buf+3 = r8+3)
        ldr     r2, [r7, #0]        @ arg2: slot index (from request [r7])
        bl      decode_cmd68_uid_tag
        cmp     r0, #1
        beq     .Luid_decoded

        @ Tag not recognized as Bambu Lab -> fall back to raw UID hex
        pop     {r4, lr}
        add.w   r1, r8, #3          @ UID bytes (4 bytes)
        add.w   r2, r4, #8          @ sku field
        movs    r3, #4
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
        subs    r3, #1
        bne     1b

        movs    r5, #0
        strb    r5, [r2]            @ NUL terminate (8 chars + NUL)
        movw    r5, #0x0201         @ version sentinel: "this sku is a raw tag UID"
        str     r5, [r4, #4]        @ 32-bit, matching the handler's own store to this field
        movs    r0, #0              @ code = SUCCESS
        b.w     epilogue            @ 0x0800E904: str r0,[r4,#140]; movs r0,#1; pop

.Luid_decoded:
        pop     {r4, lr}
        movs    r0, #0              @ code = SUCCESS
        b.w     epilogue            @ 0x0800E904: str r0,[r4,#140]; movs r0,#1; pop
