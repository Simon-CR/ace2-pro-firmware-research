@ ACE 2 Pro V1.1.31 -- sm_id injection on the AUTONOMOUS read path.
@
@ Hooked at 0x0800FE36 (replaces `mov r6,r0` + `cmp r0,#140`, bytes 06 46 8c 28), the point
@ right after the background page read succeeds, where r6 still holds the reader object and
@ r5 is the scan record. PROVEN (three argus passes on full_objdump.txt):
@
@   * r5 (worker arg1) == the commit function's memcpy SOURCE == 0x20001600+reader_idx*164+12.
@     There is no separate promotion: whatever we write at r5+X is copied verbatim by
@     commit's memcpy(dest,src,164) to the cmd-13 cache record at record+(260+X-... ) -- i.e.
@     r5+28 -> record+288 (SKU), r5+26 -> record+286 (version). Confirmed empirically: a native
@     tag reads 'S','M','2','4' at r5+28 (raw NTAG page 5).
@   * The native parse at 0x0800FE3C+ only rearranges r5[100..163]; it never touches r5[24..99],
@     so our writes at r5+26/+28 survive untouched.
@   * The stable flag (r5+22 -> record+282 = 2) is set natively on every >=140-byte read, so we
@     leave it alone. No "is this Anycubic" check gates the pipeline.
@   * A SEPARATE RC522 tail-read here reboot-loops the ACE (re-entering the reader races the
@     scan FSM - proven on hardware). Instead the stock page-read 0x0800E18C is patched (3 bytes,
@     see build_patch.py) to read pages 4..51 into 0x20000704, NAK-tolerant. This hook then just
@     SEARCHES that buffer - a pure memory scan, no reader re-entry, no race.
@
@ We (FOREIGN tags only, gated on the 0x7B native magic at r5+24 so T0/T2 are never touched):
@ search 0x20000704[0..192] for sm_id, write "SM<n>" to r5+28 and version 101 to r5+26, which the
@ commit copies to the cmd-13 SKU/version. Fail-safe: no sm_id -> write nothing, native flow
@ bit-for-bit unchanged. The +340 odometer-branch buffer (a reader index the commit never reads)
@ is guarded out. NOTE: 0x20000704 is not zeroed between reads, so a *partial* foreign read could
@ leave a prior tag's tail - T1's own full NTAG215 read populates its tail fresh, so this is a
@ rare multi-tag edge left to harden (zero the tail region, or validate it against the head).
@
@ smid_read_tail below is the (now unused) RC522 read that raced; kept as a reference for the
@ per-block reader convention. The live path uses smid_find over the extended stock buffer.
@
@ Registers: r4 (output struct) and r5 (scan record) are preserved; r6 is reloaded to the byte
@ count (native semantics) before rejoining; r7-r9/ip/lr are reloaded by the native parse.

        .syntax unified
        .thumb
        .text
        .global rawtag_extract_stub
        .thumb_func
rawtag_extract_stub:
        push    {r4, r5, r6, lr}
        sub     sp, #8
        str     r0, [sp, #0]            @ save byte count (smid_find/build clobber r0-r3)
        cmp     r0, #140
        bcc     .Lx_out                 @ short read -> native gate fails -> no inject
        movw    r0, #0x1748             @ guard: primary per-reader buffers only (base+12 for
        movt    r0, #0x2000             @ reader 0/1 are < 0x20001748; the +340 branch is >=).
        cmp     r5, r0
        bhs     .Lx_out
        ldrb    r0, [r5, #24]           @ only touch FOREIGN tags. Native Anycubic page 4 starts
        cmp     r0, #0x7b               @ 0x7B; OpenSpool starts 0x03 (NDEF TLV). Protects T0/T2.
        beq     .Lx_out
        movw    r0, #0x0704             @ search the stock page buffer, now holding pages 4..51
        movt    r0, #0x2000             @ (0x20000704) via the 3-byte extend patch. NO RC522 I/O
        movs    r1, #192                @ here - a pure memory scan, so no scan-FSM race.
        bl      smid_find
        cmp     r0, #0
        beq     .Lx_out                 @ no sm_id -> leave the native flow untouched
        add     r2, r5, #28             @ SKU field (raw page 5) -> record+288
        bl      smid_build              @ "SM" + digits + NUL
        movs    r0, #101                @ mark native so the host takes the SKU path
        strh    r0, [r5, #26]           @ version u16 -> record+286
.Lx_out:
        ldr     r0, [sp, #0]            @ byte count
        add     sp, #8
        pop     {r4, r5, r6, lr}
        mov     r6, r0                  @ replicate 0x0800FE36  (mov r6,r0)
        cmp     r0, #140                @ replicate 0x0800FE38  (flags for the native bcc)
        b.w     extract_resume              @ rejoin at the original bcc.n

@ ===========================================================================
@ Hook-independent core (validated: assembles clean; smid_find == validate_extract.py).
@ ===========================================================================

@ smid_read_tail(r0=readerobj, r1=dest, r2=startpage, r3=nblocks) -> r0 = bytes read (0 on fail)
@ Faithful replica of the stock per-block read loop at 0x0800E18C: handle=[readerobj+4]; stage
@ {0x30,page}; set bit-framing regs 18/19 |= 0x80; timer(handle,1); flush(handle);
@ transceive(handle,12,txbuf,2,rxbuf,&status); ok iff status==0 AND rxstatus==128 (16 bytes).
        .global smid_read_tail
        .thumb_func
smid_read_tail:
        push    {r4, r5, r6, r7, r8, r9, sl, fp, lr}
        sub     sp, #24
        mov     r5, r0                  @ readerobj
        mov     r7, r1                  @ running dest
        mov     r6, r2                  @ running page
        mov     fp, r3                  @ blocks remaining
        mov     sl, r1                  @ dest origin
        add     r9, sp, #16             @ txbuf ptr (2-byte frame)
.Lblk:
        ldr     r4, [r5, #4]            @ handle
        cmp     r4, #0
        beq     .Lfail
        movs    r0, #0x30
        strh    r0, [sp, #16]
        strb    r6, [sp, #17]           @ frame = { 0x30, page }
        movs    r0, #0
        str     r0, [sp, #12]           @ rxstatus = 0
        mov     r0, r4
        movs    r1, #18
        bl      rc522_read_reg
        orr     r2, r0, #0x80
        mov     r0, r4
        movs    r1, #18
        bl      rc522_write_reg
        ldr     r4, [r5, #4]
        mov     r0, r4
        movs    r1, #19
        bl      rc522_read_reg
        orr     r2, r0, #0x80
        mov     r0, r4
        movs    r1, #19
        bl      rc522_write_reg
        ldr     r0, [r5, #4]
        movs    r1, #1
        bl      rc522_timer
        ldr     r0, [r5, #4]
        bl      rc522_flush
        str     r7, [sp]                @ [sp+0] = rxbuf = dest
        add     r0, sp, #12
        str     r0, [sp, #4]            @ [sp+4] = &rxstatus
        ldr     r0, [r5, #4]            @ handle
        movs    r1, #12
        mov     r2, r9                  @ txbuf
        movs    r3, #2                  @ txlen
        bl      rc522_transceive
        cmp     r0, #0
        bne     .Lfail
        ldr     r0, [sp, #12]
        cmp     r0, #128                @ 128 bits = 16 bytes = 4 pages
        bne     .Lfail
        adds    r7, #16
        adds    r6, #4
        subs    fp, #1
        bne     .Lblk
        sub     r0, r7, sl              @ bytes read
        b       .Ldone
.Lfail:
        movs    r0, #0
.Ldone:
        add     sp, #24
        pop     {r4, r5, r6, r7, r8, r9, sl, pc}

@ smid_find(r0=buf, r1=len) -> r0 = ptr to first digit of sm_id value (0 if none), r1 = count
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
