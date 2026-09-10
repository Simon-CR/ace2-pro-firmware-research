@ ACE 2 Pro V1.1.61O -- Rotisserie Telemetry and Print Lockout Stubs
        .syntax unified
        .thumb
        .text
        .global status_rotisserie_stub
        .global dryroll_gate_stub
        .thumb_func

status_rotisserie_stub:
        push    {r1, r3, lr}
        and     r3, r0, #0x0F
        movw    r1, #0x061c
        movt    r1, #0x2000
        ldrb    r1, [r1, #0]
        cbz     r1, .Lstatus_store
        movw    r1, #0x0098
        movt    r1, #0x2000
        ldrb    r1, [r1, #0]
        cbnz    r1, .Lstatus_store
        orr     r0, r0, #0x40
        cmp     r3, #2
        bne     .Lstatus_store
        orr     r0, r0, #0x80
.Lstatus_store:
        cmp     r0, #5
        str     r0, [r2, #8]
        pop     {r1, r3, pc}

dryroll_gate_stub:
        ldrb    r0, [r0, #0]
        cbz     r0, .Lgate_skip
        push    {r1}
        movw    r1, #0x0098
        movt    r1, #0x2000
        ldrb    r1, [r1, #0]
        cbnz    r1, .Lgate_skip_pop
        pop     {r1}
        b.w     status_resume_roll
.Lgate_skip_pop:
        pop     {r1}
.Lgate_skip:
        b.w     status_skip_roll
