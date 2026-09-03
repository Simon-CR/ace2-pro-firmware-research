@ ACE 2 Pro V1.1.31 -- sm_id injection on the cmd-68 (FILAMENT_IDENTIFY) LIVE-READ path.
@
@ Companion to rawtag_extract_stub.s (which injects on the autonomous worker path). cmd-68 is the
@ on-demand, slot-attributed live read a host lock-in routine uses; it must return the same
@ injected sku so the host binds from a synchronous read, not just the background scan.
@
@ Hooked at 0x0800E8A2 (NOT 0x0800E842 - argus: writing at the E842 checkpoint is silently
@ clobbered by the native version/sku stores at 0x0800E882/0x0800E88E that run AFTER it). E8A2 is
@ reached ONLY on the success path, AFTER both native fields are populated, so we overwrite them.
@
@ At the hook (PROVEN): r4 = response struct base (version u16 at r4+4, sku[19] at r4+8, NUL at
@ r4+27 - same schema cmd-13 uses); r8 = sp+4, so the freshly-read page image is at [r8+24] and
@ mirrored at the global 0x20000704 (which the 3-byte extend-read poke filled with pages 4-51, so
@ sm_id past byte 144 IS present there - search 0x20000704, not the 140-byte local copy at r8+24).
@ Gate byte (page-4 byte 0) is [r8+24]: 0x7B = native Anycubic -> leave untouched; else foreign.
@ Displaced instruction: `add.w r0, r8, #88` (bytes 08 f1 58 00), resume 0x0800E8A6; r0 is consumed
@ downstream as the colour-list base, so we replicate it. r4/r8 are preserved by the callees;
@ r1-r3 are saved/restored defensively.
@
@ smid_find / smid_build are duplicated here (not shared) because build_patch assembles each stub
@ as an independent unit with no cross-linking; they are byte-identical to rawtag_extract_stub.s
@ and proven. If either is ever changed, change both.

        .syntax unified
        .thumb
        .text
        .global rawtag_cmd68_stub
        .thumb_func
rawtag_cmd68_stub:
        push    {r1, r2, r3, lr}
        ldrb    r0, [r8, #24]           @ page-4 byte 0 (gate)
        cmp     r0, #0x7b               @ native Anycubic? leave the native decode untouched
        beq     .Lc_done
        movw    r0, #0x0704
        movt    r0, #0x2000             @ 0x20000704 (pages 4-51; sm_id lives past the 140B copy)
        movs    r1, #192
        bl      smid_find               @ r0 = digit ptr (0 if none), r1 = count
        cmp     r0, #0
        beq     .Lc_done
        add     r2, r4, #8              @ response sku field
        bl      smid_build              @ "SM" + digits + NUL
@ 0x0203 = "identity injected; the sku is real, EVERY other field in this record is the
@ stock positional parse of a tag that is not in Anycubic's layout, i.e. garbage."
@ We used to write 101 here, which means "valid native decode, trust everything" - a lie
@ that only our own host knew to discount. Any other consumer (an Anycubic printer, most
@ obviously) would have read a nozzle target out of that garbage. An unknown version is
@ ignored by a consumer that does not know it, which is the safe default we want.
        movw    r0, #0x0203
        str     r0, [r4, #4]            @ response version (native word store, matches E882)
        movs    r0, #0
        strb    r0, [r4, #27]           @ ensure the 19-byte sku field's NUL terminator
.Lc_done:
        pop     {r1, r2, r3, lr}
        add.w   r0, r8, #88             @ replicate displaced `add.w r0, r8, #88`
        b.w     cmd68_resume              @ resume

@ ===========================================================================
@ Duplicated from rawtag_extract_stub.s (build assembles stubs independently). Proven equal.
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
