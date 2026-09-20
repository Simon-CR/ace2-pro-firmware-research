@ ACE 2 Pro V1.1.61O -- Rotisserie Telemetry and Print Lockout Stubs
        .syntax unified
        .thumb
        .text
        .global status_rotisserie_stub
        .thumb_func

status_rotisserie_stub:
        push    {r1, r3}
        mov     r3, r0
        movw    r1, #0x061c
        movt    r1, #0x2000
        ldrb    r1, [r1, #0]
        cbz     r1, .Lstatus_store
        movw    r1, #0x0098
        movt    r1, #0x2000
        ldrb    r1, [r1, #0]
        cbnz    r1, .Lstatus_store
        orr     r3, r3, #0x40
        and     r1, r0, #0x0F
        cmp     r1, #2
        bne     .Lstatus_store
        orr     r3, r3, #0x80
.Lstatus_store:
        cmp     r0, #5
        str     r3, [r2, #8]
        pop     {r1, r3}
        b.w     status_resume


