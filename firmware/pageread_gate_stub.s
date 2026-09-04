@ pageread_gate_stub -- make the extend-read failure path HONEST.
@
@ THE DEFECT. build_patch.py extends the stock page read (0x0800E18C) to 12 blocks so the
@ sm_id extract hook can find its bytes in the tail, and tolerates the page-39 NAK that an
@ NTAG213 returns by pointing BOTH failure branches at the success epilogue:
@
@   0x800E216  cbnz r0, 0x800e23c   ->  0x800e228   (transceive failed)
@   0x800E21C  bne.n 0x800e23c      ->  0x800e228   (rx status != 128)
@
@ In stock firmware either branch reached 0x800E23C with sl = 0, so the caller's
@ "cmp r0,#140 / bcc" correctly recorded a failed read. With the pokes, a failure at ANY
@ block - including the very first - falls through to the epilogue's memcpy and returns the
@ hardcoded 144, i.e. "I read a whole tag".
@
@ The buffer at 0x20000704 is DEVICE-GLOBAL and still holds the PREVIOUS tag. So the failing
@ lane's record is built from its neighbour's bytes, the native parse accepts them, and the
@ device caches that identity until eject. Measured 2026-09-03/04: T1 (reader 0) repeatedly
@ came back as T2's SM24 (reader 1) whenever a T2 read landed shortly before it - same
@ firmware, same spool, same tag, only the scan order differing. The operator sees a lane
@ that has permanently "lost its spool".
@
@ The docs call that redirect "NAK-tolerant". It is not tolerant, it is BLIND: nothing checks
@ that anything was read on this call.
@
@ THE FIX. Gate the epilogue on how far the loop actually got. r7 is the byte offset and is
@ incremented at 0x800E21E, AFTER both failure branches, so at a failure it holds exactly the
@ number of bytes already read:
@
@   block 0 fails          r7 = -16   -> real failure, return sl (0), caller sees < 140
@   failure at block 9     r7 = 112   -> 128 valid, still short of 140 -> rejected
@   NTAG213 NAK at page 48 r7 = 160   -> 176 valid -> tolerated, first 140 bytes are good
@   clean full read        r7 = 176   -> 192 valid -> tolerated
@
@ The exact invariant, since the off-by-one matters: iteration n writes 16 bytes at
@ 0x20000704 + r7 + 16, so on every path reaching 0x0800E228, valid_bytes = r7 + 16.
@ valid >= 140 is therefore exactly r7 >= 124.
@
@ 124 is the stock loop bound, i.e. the amount the unpatched firmware already considered a
@ complete read. Using it keeps the NTAG213 tolerance the pokes were added for and rejects
@ nothing the stock firmware would have accepted.
@
@ HOOKED AT THE EPILOGUE, not at the branches. Both failure branches are narrow (cbnz has a
@ 0-126 byte forward range, bne.n +-256) and cannot reach an appended stub; the epilogue is a
@ 4-byte movw that a b.w replaces exactly. Both the failure path and the normal loop exit
@ converge there, and the normal exit passes the gate by construction.
@
@ Flags: the preceding instruction is the loop's own bcc.n, which has already consumed them,
@ and the next flag consumer is past the resume point - so cmp here clobbers nothing live.
@ r1 is dead until the displaced movw sets it.

        .syntax unified
        .thumb
        .text
        .global pageread_gate_stub
        .thumb_func

pageread_gate_stub:
        cmp     r7, #124                @ bytes actually read this call
@ SIGNED. r7 is initialised to -16 by "mvn.w r7,#15" at 0x0800E196 and only incremented at
@ 0x0800E21E, so on a BLOCK-0 failure it still holds -16 (0xFFFFFFF0). bcc is an UNSIGNED
@ compare and reads that as 4294967280 - greater than 124 - so it would pass through the one
@ case this gate exists to stop, and a total read failure would still return a neighbour's
@ record. blt reads it as -16 and rejects. r7's full range here is [-16, 176], so a signed
@ compare is exact at every point and needs no reasoning about a wrapped constant.
        blt     .Lpg_fail               @ short -> the stock failure return, sl still 0
        movw    r1, #0x0704             @ the displaced instruction
        b.w     pageread_resume         @ 0x0800E22C, mid-epilogue
.Lpg_fail:
        b.w     pageread_fail           @ 0x0800E23C, "mov r0, sl" -> returns 0
