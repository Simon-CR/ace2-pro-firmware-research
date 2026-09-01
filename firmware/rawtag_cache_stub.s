@ ACE 2 Pro V1.1.31 -- raw tag capture on the AUTONOMOUS read path.
@
@ Hooked at 0x0800FE3C, immediately after the background reader's page read succeeds and
@ immediately BEFORE its positional Anycubic parse begins:
@
@   800fe28:  add.w  r1, r5, #24     @ r1 = r5+24, destination for the page data
@   800fe32:  bl     0x800e18c       @ the read; byte count in r0 (also kept in r6)
@   800fe38:  cmp    r0, #140
@   800fe3a:  bcc.n  0x800fe94       @ too few bytes -> bail
@   800fe3c:  add.w  lr, r5, #136    @ <- WE REPLACE THIS; the parse starts here
@
@ WHY THIS EXISTS AND rawtag_stub.s DOES NOT SUFFICE. There are exactly two callers of the
@ page-read helper 0x800e18c: the command handler at 0x800e82e (FILAMENT_IDENTIFY, cmd 68) and
@ this one. rawtag_stub.s hooks the first - and on this machine cmd 68 NEVER SUCCEEDS. Measured
@ on hardware with V1.1.3X installed: an explicit FILAMENT_IDENTIFY returns code 3 (SELECT
@ failed) on slot 0 and slot 2, whose Anycubic tags the ACE decodes perfectly a moment later on
@ its own. So a host-initiated read cannot catch a tag at all, and every real read on this
@ machine comes through HERE, from the background scan, which is why the earlier hook could
@ never fire no matter which tag was in the bay.
@
@ WHAT IT DOES, AND WHY IT IS THE SMALLEST POSSIBLE CHANGE. It copies the raw image to the
@ firmware's own page buffer at 0x20000704 and then runs the original parse UNCHANGED. There is
@ no magic check and no branch: every tag, Anycubic or not, leaves its bytes where RC522 op 9
@ can read them, and the firmware's own decode proceeds exactly as before. So the behaviour of
@ an Anycubic tag is bit-for-bit identical, and nothing downstream needs to know this exists.
@
@ The host already has the trigger it needs: a foreign tag still produces the same positional
@ misparse, its plausibility gate still rejects it, and THAT rejection is the signal to fetch
@ the image and decode it properly. No sentinel, no response-record surgery, no protocol change
@ - which matters, because the slot record's layout differs between this function and the
@ command handler and guessing at it is how the first attempt went wrong.
@
@ r5 and r6 are callee-saved and survive the memcpy. r0 is dead here - the byte count the parse
@ uses was already copied to r6 at 0x800fe36 - and r1/r2/r3/ip/lr are reloaded by the ldmia at
@ 0x800fe40, so clobbering them is safe.

        .syntax unified
        .thumb
        .text
        .global rawtag_cache_stub
        .thumb_func

rawtag_cache_stub:
        mov     r2, r0                  @ byte count, as its own length
        movw    r0, #0x0704
        movt    r0, #0x2000             @ 0x20000704, the tag-page buffer
        add     r1, r5, #24             @ the image the read just produced
        bl      memcpy

        add.w   lr, r5, #136            @ the displaced instruction
        b.w     cache_resume            @ 0x0800FE40
