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
        pop     {r4, lr}
        movs    r0, #0              @ code = SUCCESS
        b.w     epilogue            @ 0x0800E904: str r0,[r4,#140]; movs r0,#1; pop
