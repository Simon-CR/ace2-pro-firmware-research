@ ACE 2 Pro V1.1.44 -- sm_id injection and autonomous Bambu UID fallback.
@
@ Hooked at 0x0800FE36 (replaces `mov r6,r0` + `cmp r0,#140`, bytes 06 46 8c 28), the point
@ right after the background page read succeeds, where r6 still holds the reader object and
@ r5 is the scan record.
@
@ When NTAG page-read succeeds (r0 >= 140):
@   Foreign tags are scanned in 0x20000704 for sm_id -> writes 'SM<n>' at r5+28 and sentinel 0x0203.
@
@ When NTAG page-read fails (r0 < 140):
@   The native firmware already copied the 10-byte UID buffer from 0x20000ec7 to r5+3 at 0x0800FE22.
@   We verify the 4-byte MIFARE Classic BCC: r5[3]^r5[4]^r5[5]^r5[6] == r5[7].
@   If valid, we format the 8-char hex UID at r5+28, write sentinel 0x0201 at r5+26,
@   set status=2 (ready) at r5+22, set r6=140, and branch directly to scan_exit (0x0800FE98),
@   which returns r0=1 (success) without running the native Anycubic positional unpack.

        .syntax unified
        .thumb
        .text
        .global rawtag_extract_stub
        .thumb_func
rawtag_extract_stub:
        push    {r4, r5, r6, lr}
        sub     sp, #8
        str     r0, [sp, #0]            @ save byte count
        cmp     r0, #140
        bcs     .Lntag_ok               @ >= 140: proceed with Anycubic/OpenSpool parse

        @ --- AUTONOMOUS BAMBU / MIFARE UID FALLBACK ---
        @ Check if primary per-reader buffer (base+12 < 0x20001748)
        movw    r0, #0x1748
        movt    r0, #0x2000
        cmp     r5, r0
        bhs     .Lx_out

        @ Verify 4-byte UID BCC: r5[3] ^ r5[4] ^ r5[5] ^ r5[6] == r5[7]
        ldrb    r0, [r5, #3]
        ldrb    r1, [r5, #4]
        ldrb    r2, [r5, #5]
        ldrb    r3, [r5, #6]
        ldrb    r4, [r5, #7]
        orrs    r6, r0, r1
        orrs    r6, r6, r2
        orrs    r6, r6, r3
        beq     .Lx_out                 @ all zeroes -> no tag present

        eors    r0, r0, r1
        eors    r0, r0, r2
        eors    r0, r0, r3
        cmp     r0, r4
        bne     .Lx_out                 @ BCC mismatch -> not a valid 4-byte UID

        @ Format 4-byte UID into hex at r5+28 (sku)
        add.w   r1, r5, #3              @ source bytes
        add.w   r2, r5, #28             @ destination sku field
        movs    r3, #4                  @ 4 bytes
.Lhex_loop:
        ldrb    r0, [r1], #1
        lsrs    r4, r0, #4              @ high nibble
        cmp     r4, #10
        ite     lt
        addlt   r4, r4, #48             @ '0'
        addge   r4, r4, #55             @ 'A' - 10
        strb    r4, [r2], #1
        and     r4, r0, #15             @ low nibble
        cmp     r4, #10
        ite     lt
        addlt   r4, r4, #48
        addge   r4, r4, #55
        strb    r4, [r2], #1
        subs    r3, r3, #1
        bne     .Lhex_loop

        movs    r4, #0
        strb    r4, [r2]                @ NUL terminate (8 hex chars + NUL)
        movw    r4, #0x0201             @ version sentinel: Bambu UID
        strh    r4, [r5, #26]           @ version u16 -> record+286
        movs    r4, #2                  @ status = 2 (ready / identified)
        strb    r4, [r5, #22]

        add     sp, #8
        pop     {r4, r5, r6, lr}
        movs    r6, #140                @ r6 > 139 ensures native epilogue sets r0 = 1
        b.w     scan_exit               @ branch to 0x0800FE98 (clean exit as success)

.Lntag_ok:
        movw    r0, #0x1748             @ guard: primary per-reader buffers only
        movt    r0, #0x2000
        cmp     r5, r0
        bhs     .Lx_out
        ldrb    r0, [r5, #24]           @ only touch FOREIGN tags. Native Anycubic page 4 starts 0x7B
        cmp     r0, #0x7b
        beq     .Lx_out
        movw    r0, #0x0704             @ search the stock page buffer for sm_id
        movt    r0, #0x2000
        movs    r1, #192
        bl      smid_find
        cmp     r0, #0
        beq     .Lx_out
        add     r2, r5, #28             @ SKU field
        bl      smid_build              @ "SM" + digits + NUL
        movw    r0, #0x0203             @ injected-identity sentinel
        strh    r0, [r5, #26]

.Lx_out:
        ldr     r0, [sp, #0]            @ byte count
        add     sp, #8
        pop     {r4, r5, r6, lr}
        mov     r6, r0                  @ replicate 0x0800FE36
        cmp     r0, #140                @ replicate 0x0800FE38
        b.w     extract_resume          @ rejoin at 0x0800FE3A (native bcc.n 0x800fe94)

.global smid_find
        .thumb_func
smid_find:
        push    {r4, lr}
        add     r1, r0, r1              @ end
        subs    r3, r1, #6
.Lscan:
        cmp     r0, r3
        bhi     .Lnone
        ldrb    r2, [r0]
        cmp     r2, #0x73               @ 's'
        bne     .Lnext
        ldrb    r2, [r0, #1]
        cmp     r2, #0x6d               @ 'm'
        bne     .Lnext
        ldrb    r2, [r0, #2]
        cmp     r2, #0x5f               @ '_'
        bne     .Lnext
        ldrb    r2, [r0, #3]
        cmp     r2, #0x69               @ 'i'
        bne     .Lnext
        ldrb    r2, [r0, #4]
        cmp     r2, #0x64               @ 'd'
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
        cmp     r2, #0x7d               @ '}' before a digit -> malformed
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

@ smid_build(r0=digit ptr, r1=count, r2=dst) -> writes "SM" + digits + NUL at dst
        .global smid_build
        .thumb_func
smid_build:
        push    {r4, lr}
        movs    r3, #0x53               @ 'S'
        strb    r3, [r2], #1
        movs    r3, #0x4d               @ 'M'
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
